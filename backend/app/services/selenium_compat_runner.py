"""
Selenium IDE execution compatibility layer.

Ported from the proven reference implementation (an earlier Selenium
Automation Platform project's ``backend/runner_plugin.py``), adapted to
this project's architecture: instead of being installed as a pytest plugin
via ``-p`` on a fixed PYTHONPATH, this module's source is embedded verbatim
as the generated ``conftest.py`` written into each Run's throwaway temp
directory (see ``execution_service._write_conftest``). pytest auto-loads
any ``conftest.py`` in the directory it's pointed at and treats top-level
``pytest_*`` functions in it exactly as it would in an installed plugin, so
no new wiring (no ``-p`` flag, no PYTHONPATH, no package install) is
needed to get the same behavior.

This file is never applied to, or derived from, an uploaded Selenium IDE
.py file's content. It patches Selenium/WebDriver classes at import time
so that whatever an uploaded test does — ``webdriver.Chrome()``,
``.find_element(...).click()``, ``ActionChains(...).perform()`` — runs
through this compatibility layer without the uploaded source ever being
edited.

Behavioral reference: the OLD project's runner_plugin.py. Where this
module deviates, it's because the current architecture requires it:
  - Browser selection is read from the existing ``SELENIUM_BROWSER`` env
    var (already set by execution_service) instead of a new
    ``AUTOMATION_BROWSER`` var.
  - The 10-second implicit wait is folded directly into the browser
    factory instead of being a second, separate WebDriver.__init__ patch.
  - Step/screenshot state is written under ``SELENIUM_COMPAT_RUN_DIR``
    (the Run's own temp directory) instead of a dedicated runs/ folder,
    since nothing here is meant to outlive the temp directory — there is
    no Run schema field to persist steps/screenshots into, and this task
    does not add one.
"""

import json
import os
import re
import time
import threading
import inspect
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


RUN_DIR = Path(os.environ.get("SELENIUM_COMPAT_RUN_DIR", "."))
SHOTS_DIR = Path(os.environ.get("SELENIUM_COMPAT_SCREENSHOT_DIR", RUN_DIR / "screenshots"))

# "all" captures every successful step too; "failed" (the default) only
# captures failures, matching the current project's existing behavior of
# not producing screenshots for passing runs; "none" disables screenshots
# entirely.
SCREENSHOT_MODE = os.environ.get("SELENIUM_COMPAT_SCREENSHOT_MODE", "failed").lower().strip()

IMPLICIT_WAIT_SECONDS = float(os.environ.get("SELENIUM_IMPLICIT_WAIT_SECONDS", "10"))
FIND_TIMEOUT = float(os.environ.get("SELENIUM_FIND_TIMEOUT", "12"))
STALE_CLICK_TIMEOUT = float(os.environ.get("SELENIUM_STALE_CLICK_TIMEOUT", "10"))
FIND_POLL = 0.2

EVENTS = []
LOCK = threading.Lock()
CUR = threading.local()
STEP_CACHE = {}


def _node():
    return getattr(CUR, "nodeid", "")


def _test_source_frame():
    for frame in inspect.stack():
        if frame.function.startswith("test"):
            return frame
    return None


def _load_step_comments(filename):
    """Parses a Selenium IDE-generated file's own
    ``# N | action | target | value`` comments (the exact format Selenium
    IDE's Python export writes above every command) to correlate a runtime
    Selenium call back to its IDE step number. Read-only — never rewrites
    the uploaded file."""

    if filename in STEP_CACHE:
        return STEP_CACHE[filename]

    result = []
    try:
        lines = Path(filename).read_text(encoding="utf-8", errors="ignore").splitlines()
        for i, line in enumerate(lines, 1):
            match = re.search(r"#\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*$", line)
            if match:
                result.append(
                    {
                        "line": i,
                        "number": int(match.group(1)),
                        "action": match.group(2).strip(),
                        "target": match.group(3).strip(),
                        "value": match.group(4).strip(),
                    }
                )
    except Exception:
        pass

    STEP_CACHE[filename] = result
    return result


def _source_step(action, target="", value=""):
    frame = _test_source_frame()
    if not frame:
        return None

    steps = _load_step_comments(frame.filename)
    if not steps:
        return None

    before = [s for s in steps if s["line"] <= frame.lineno]
    if not before:
        return None

    candidates = [s for s in before if s["action"].lower() == action.lower()]
    if candidates:
        return candidates[-1]
    return before[-1]


# ---------------------------------------------------------------------------
# Screenshot-only redaction of typed fields.
#
# Anything a test types may be sensitive, whatever the field is called, so every
# element that received typed text (and every password input) is blurred for the
# instant of the capture and restored right after. Nothing is changed
# permanently: blur is an inline CSS filter (no layout, focus or event effect),
# the original inline value is kept in a temporary attribute and put back, and
# restoring runs in a finally block even if the capture fails. The typed text is
# only ever passed to the page as a script argument; it is never logged here.
# Stale, hidden or detached elements are skipped silently.
# ---------------------------------------------------------------------------
_MAX_TRACKED = 50

_REDACT_ELEMENT_JS = (
    "var e=arguments[0];"
    "if(e && !e.hasAttribute('data-cv-redacted')){"
    "e.setAttribute('data-cv-redacted', e.style.filter || '');"
    "e.style.filter='blur(7px)';}"
)
# Password inputs always; plus any field still holding a typed value (covers fields
# the page re-rendered, so the tracked element reference went stale).
_REDACT_PAGE_JS = (
    "var vals=arguments[0]||[];"
    "function hide(e){if(e && !e.hasAttribute('data-cv-redacted')){"
    "e.setAttribute('data-cv-redacted', e.style.filter || '');e.style.filter='blur(7px)';}}"
    "document.querySelectorAll('input[type=password]').forEach(hide);"
    "if(vals.length){document.querySelectorAll("
    "'input,textarea,[contenteditable=\"\"],[contenteditable=\"true\"]').forEach(function(e){"
    "var v=(e.value!==undefined&&e.value!==null?e.value:e.textContent)||'';"
    "for(var i=0;i<vals.length;i++){if(vals[i]&&v.indexOf(vals[i])!==-1){hide(e);break;}}});}"
)
_RESTORE_JS = (
    "document.querySelectorAll('[data-cv-redacted]').forEach(function(e){"
    "e.style.filter=e.getAttribute('data-cv-redacted');e.removeAttribute('data-cv-redacted');});"
)


def _track_typed(element, keys):
    """Remembers an element that received typed text, and the printable text, so
    screenshots taken afterwards can redact it. Never raises."""
    try:
        if getattr(CUR, "typed_elements", None) is None:
            CUR.typed_elements = []
            CUR.typed_values = []
        if element is not None:
            CUR.typed_elements.append(element)
            del CUR.typed_elements[:-_MAX_TRACKED]
        for key in keys:
            # Selenium's special keys (Keys.ENTER ...) are non-printable characters.
            if isinstance(key, str) and len(key) >= 2 and key.isprintable() and key not in CUR.typed_values:
                CUR.typed_values.append(key)
        del CUR.typed_values[:-_MAX_TRACKED]
    except Exception:
        pass


def _redact_typed(driver):
    for element in list(getattr(CUR, "typed_elements", None) or []):
        if getattr(element, "parent", None) is not driver:
            continue
        try:
            driver.execute_script(_REDACT_ELEMENT_JS, element)
        except Exception:
            pass  # stale / detached / hidden element: skip, never fail the test
    try:
        driver.execute_script(_REDACT_PAGE_JS, list(getattr(CUR, "typed_values", None) or []))
    except Exception:
        pass


def _restore_typed(driver):
    try:
        driver.execute_script(_RESTORE_JS)
    except Exception:
        pass


def shot(driver, label):
    if SCREENSHOT_MODE == "none" or driver is None:
        return ""
    redacting = False
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{len(EVENTS) + 1:04d}_{label}_{int(time.time() * 1000)}.png"
        redacting = True  # set first: restore must run even if redaction stops half-way
        _redact_typed(driver)
        driver.save_screenshot(str(SHOTS_DIR / name))
        return name
    except Exception:
        return ""
    finally:
        if redacting:
            _restore_typed(driver)


def _record(action, target="", value="", duration=0, status="PASS", error="", screenshot="", step=None):
    if status == "PASS" and not screenshot and SCREENSHOT_MODE == "all":
        drv = getattr(CUR, "driver", None)
        if drv is not None:
            label = f"step_{step['number']}" if step else action
            screenshot = shot(drv, label)

    event = {
        "nodeid": _node(),
        "step": step["number"] if step else None,
        "action": step["action"] if step else action,
        "target": step["target"] if step else str(target),
        "value": step["value"] if step else str(value),
        # The value actually typed at run time. "value" above prefers the Selenium IDE
        # comment's text when one matches, so a value that came from a fixture or data
        # module would otherwise never be recorded. This key exists only so the
        # execution service can mask it everywhere; it is never persisted or shown.
        "runtime_value": str(value) if (action == "type" and value) else "",
        "status": status,
        "duration": round(duration, 4),
        "error": error,
        "screenshot": screenshot,
        "timestamp": time.time(),
    }

    with LOCK:
        EVENTS.append(event)
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            (RUN_DIR / "steps.json").write_text(json.dumps(EVENTS, indent=2), encoding="utf-8")
        except Exception:
            pass


def _wrap(cls, name, action, target_fn=lambda s, a, k: "", value_fn=lambda s, a, k: "", before=None):
    if not hasattr(cls, name):
        return
    old = getattr(cls, name)
    if getattr(old, "_selenium_compat_wrapped", False):
        return

    def fn(self, *a, **k):
        st = time.perf_counter()
        target = target_fn(self, a, k)
        value = value_fn(self, a, k)
        step = _source_step(action, target, value)
        if before is not None:
            before(self, a, k)  # e.g. remember typed elements before any screenshot is taken
        try:
            result = old(self, *a, **k)
            _record(action, target, value, time.perf_counter() - st, "PASS", step=step)
            return result
        except Exception as e:
            drv = self if hasattr(self, "save_screenshot") else getattr(self, "parent", None)
            sc = shot(drv, f"step_{step['number']}" if step else action)
            _record(action, target, value, time.perf_counter() - st, "FAIL", repr(e), sc, step)
            raise

    fn._selenium_compat_wrapped = True
    fn._selenium_compat_original = old
    setattr(cls, name, fn)


# ---------------------------------------------------------------------------
# Browser constructor patching + implicit wait.
#
# Selenium IDE files are commonly generated with webdriver.Chrome()
# regardless of which browser the Run UI selected. Every Selenium browser
# constructor is patched to the Run's selected browser (read from the
# SELENIUM_BROWSER env var, already set by execution_service), so selecting
# Edge or Firefox can never silently fall back to Chrome. The 10-second
# implicit wait (the platform's existing readiness mechanism) is applied
# right after construction, in the same place, rather than via a second
# separate WebDriver.__init__ patch.
# ---------------------------------------------------------------------------
_BROWSER_CLASSES = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}
_selected_browser = os.environ.get("SELENIUM_BROWSER", "chrome").lower().strip()
if _selected_browser not in _BROWSER_CLASSES:
    _selected_browser = "chrome"

_selected_name = _BROWSER_CLASSES[_selected_browser]
_original_ctors = {name: getattr(webdriver, name) for name in _BROWSER_CLASSES.values() if hasattr(webdriver, name)}
_selected_original_ctor = _original_ctors.get(_selected_name)


def _make_browser_factory(name, original_ctor):
    def create(*args, **kwargs):
        driver = original_ctor(*args, **kwargs)
        driver.implicitly_wait(IMPLICIT_WAIT_SECONDS)
        CUR.driver = driver
        return driver

    create.__name__ = name
    create.__qualname__ = name
    return create


if _selected_original_ctor is not None:
    for _ctor_name in _original_ctors:
        setattr(webdriver, _ctor_name, _make_browser_factory(_selected_name, _selected_original_ctor))


# ---------------------------------------------------------------------------
# Common WebDriver / WebElement action tracking (for step correlation).
# ---------------------------------------------------------------------------
for _n, _a in [
    ("get", "open"),
    ("refresh", "refresh"),
    ("back", "back"),
    ("forward", "forward"),
    ("set_window_size", "setWindowSize"),
]:
    _wrap(
        WebDriver, _n, _a,
        lambda s, a, k: str(a[0]) if a else "",
        lambda s, a, k: str(a[1]) if len(a) > 1 else "",
    )

_wrap(
    WebElement, "send_keys", "type",
    lambda s, a, k: getattr(s, "tag_name", ""),
    lambda s, a, k: a[0] if a else "",
    before=lambda s, a, k: _track_typed(s, a),
)
_wrap(WebElement, "clear", "clear")


# ---------------------------------------------------------------------------
# Resilient click: scroll into view, recover from stale/intercepted/
# not-interactable elements, fall back to a live-DOM JS click, retry until
# timeout. The uploaded test's ``.click()`` call site is never touched —
# only WebElement.click's implementation is replaced at runtime.
# ---------------------------------------------------------------------------
_native_element_click = WebElement.click
_native_find_element = WebDriver.find_element


def _find_in_frames(driver, by, value, depth=0, max_depth=5):
    try:
        return _native_find_element(driver, by, value)
    except Exception:
        shadow = _find_shadow_element(driver, by, value)
        if shadow is not None:
            return shadow
        if depth >= max_depth:
            return None

    try:
        frames = driver.find_elements("css selector", "iframe,frame")
    except Exception:
        frames = []

    for frame in frames:
        try:
            driver.switch_to.frame(frame)
            found = _find_in_frames(driver, by, value, depth + 1, max_depth)
            if found is not None:
                try:
                    found._selenium_compat_locator = (by, value)
                except Exception:
                    pass
                return found
            driver.switch_to.parent_frame()
        except Exception:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                pass
    return None


def _find_shadow_element(driver, by, value):
    if by not in ("id", "css selector", "css"):
        return None
    css = f"#{value}" if by == "id" else value
    script = """
    const selector = arguments[0];
    function find(root) {
      try {
        const direct = root.querySelector(selector);
        if (direct) return direct;
        const all = root.querySelectorAll('*');
        for (const el of all) {
          if (el.shadowRoot) {
            const found = find(el.shadowRoot);
            if (found) return found;
          }
        }
      } catch (e) {}
      return null;
    }
    return find(document);
    """
    try:
        return driver.execute_script(script, css)
    except Exception:
        return None


def _fresh_element_for_click(element):
    locator = getattr(element, "_selenium_compat_locator", None)
    parent = getattr(element, "_parent", None)
    if not locator or parent is None:
        return None
    by, value = locator
    deadline = time.monotonic() + STALE_CLICK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            found = _find_in_frames(parent, by, value)
            if found is not None:
                try:
                    found._selenium_compat_locator = locator
                except Exception:
                    pass
                return found
        except Exception:
            pass
        try:
            parent.switch_to.default_content()
        except Exception:
            pass
        time.sleep(FIND_POLL)
    return None


def _js_click_by_locator(driver, locator):
    by, value = locator
    if by not in ("css selector", "css", "id"):
        return False
    selector = f"#{value}" if by == "id" else value
    script = """
        const selector = arguments[0];
        function clickLive(root) {
            let el = null;
            try { el = root.querySelector(selector); } catch (e) { return false; }
            if (!el) return false;
            try { el.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
            try { el.click(); return true; } catch (e) {}
            let parent = el.parentElement;
            for (let i = 0; parent && i < 6; i++, parent = parent.parentElement) {
                const tag = (parent.tagName || '').toLowerCase();
                const role = parent.getAttribute && parent.getAttribute('role');
                const onclick = parent.getAttribute && parent.getAttribute('onclick');
                const cls = String(parent.className || '');
                if (tag === 'a' || tag === 'button' || role === 'button' || onclick || /cursor-pointer/.test(cls)) {
                    try { parent.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
                    try { parent.click(); return true; } catch (e) {}
                }
            }
            return false;
        }
        return clickLive(document);
    """
    try:
        return bool(driver.execute_script(script, selector))
    except Exception:
        return False


def _click_current_or_fresh(element):
    from selenium.common.exceptions import (
        StaleElementReferenceException,
        ElementClickInterceptedException,
        ElementNotInteractableException,
    )

    locator = getattr(element, "_selenium_compat_locator", None)
    deadline = time.monotonic() + STALE_CLICK_TIMEOUT
    last_error = None

    while time.monotonic() < deadline:
        if locator:
            fresh = _fresh_element_for_click(element)
            if fresh is not None:
                element = fresh

        try:
            try:
                element._parent.execute_script(
                    "arguments[0].scrollIntoView({block:'center', inline:'center'});", element
                )
            except Exception:
                pass
            _native_element_click(element)
            return element
        except (StaleElementReferenceException, ElementClickInterceptedException, ElementNotInteractableException) as exc:
            last_error = exc

            if locator:
                try:
                    driver = element._parent
                    if _js_click_by_locator(driver, locator):
                        return element
                except Exception as js_exc:
                    last_error = js_exc

            if locator:
                fresh = _fresh_element_for_click(element)
                if fresh is not None:
                    element = fresh
                    try:
                        _native_element_click(element)
                        return element
                    except (StaleElementReferenceException, ElementClickInterceptedException, ElementNotInteractableException) as exc2:
                        last_error = exc2
            time.sleep(FIND_POLL)
            continue
        except Exception as exc:
            last_error = exc
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Unable to click element after recovery retries")


def _resilient_click(self):
    st = time.perf_counter()
    step = _source_step("click")
    target = ""
    try:
        try:
            target = getattr(self, "tag_name", "")
        except Exception:
            pass
        try:
            self._parent.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});", self
            )
        except Exception:
            pass

        clicked = _click_current_or_fresh(self)
        try:
            target = getattr(clicked, "tag_name", target)
        except Exception:
            pass
        _record("click", target, duration=time.perf_counter() - st, status="PASS", step=step)
        return None
    except Exception as e:
        drv = getattr(self, "_parent", None)
        sc = shot(drv, f"step_{step['number']}" if step else "click")
        _record("click", target, duration=time.perf_counter() - st, status="FAIL", error=repr(e), screenshot=sc, step=step)
        raise


_resilient_click._selenium_compat_resilient = True
WebElement.click = _resilient_click


# ---------------------------------------------------------------------------
# Resilient find_element: retries within a timeout, and searches nested
# iframe/frame contexts and open shadow roots when a plain lookup fails.
# The locator is kept on the returned element so a later resilient click
# can re-resolve it if the DOM replaces the node.
# ---------------------------------------------------------------------------
def _resilient_find_element(self, by="id", value=None):
    deadline = time.monotonic() + FIND_TIMEOUT

    while time.monotonic() < deadline:
        try:
            found = _find_in_frames(self, by, value)
            if found is not None:
                try:
                    found._selenium_compat_locator = (by, value)
                except Exception:
                    pass
                return found
        except Exception:
            pass

        try:
            self.switch_to.default_content()
        except Exception:
            pass
        time.sleep(FIND_POLL)

    try:
        self.switch_to.default_content()
    except Exception:
        pass
    return _native_find_element(self, by, value)


if not getattr(WebDriver.find_element, "_selenium_compat_resilient", False):
    _resilient_find_element._selenium_compat_resilient = True
    WebDriver.find_element = _resilient_find_element


# ---------------------------------------------------------------------------
# Selenium IDE ActionChains compatibility: legacy
# move_to_element(element, x, y) alongside modern
# move_to_element_with_offset(...), and transient-failure retry for
# perform().
# ---------------------------------------------------------------------------
_original_move_to_element = ActionChains.move_to_element
_original_move_to_element_with_offset = getattr(ActionChains, "move_to_element_with_offset", None)
_original_actionchains_perform = ActionChains.perform


def _compatible_move_to_element(self, to_element, *args, **kwargs):
    if not args and not kwargs:
        return _original_move_to_element(self, to_element)

    if len(args) == 2 and not kwargs:
        xoffset, yoffset = args
        if _original_move_to_element_with_offset is not None:
            return _original_move_to_element_with_offset(self, to_element, xoffset, yoffset)
        _original_move_to_element(self, to_element)
        return self.move_by_offset(xoffset, yoffset)

    if not args and set(kwargs).issubset({"xoffset", "yoffset"}):
        xoffset = kwargs.get("xoffset", 0)
        yoffset = kwargs.get("yoffset", 0)
        if _original_move_to_element_with_offset is not None:
            return _original_move_to_element_with_offset(self, to_element, xoffset, yoffset)
        _original_move_to_element(self, to_element)
        return self.move_by_offset(xoffset, yoffset)

    return _original_move_to_element(self, to_element, *args, **kwargs)


ActionChains.move_to_element = _compatible_move_to_element


def _actionchains_driver(chain):
    return getattr(chain, "_driver", None) or getattr(chain, "driver", None)


def _retry_actionchains_perform(self, *args, **kwargs):
    st = time.perf_counter()
    step = _source_step("actionChains")
    driver = _actionchains_driver(self)
    last_error = None

    try:
        from selenium.common.exceptions import (
            StaleElementReferenceException,
            ElementClickInterceptedException,
            MoveTargetOutOfBoundsException,
            ElementNotInteractableException,
        )
        transient = (
            StaleElementReferenceException,
            ElementClickInterceptedException,
            MoveTargetOutOfBoundsException,
            ElementNotInteractableException,
        )
    except Exception:
        transient = (Exception,)

    for attempt in range(1, 6):
        try:
            result = _original_actionchains_perform(self, *args, **kwargs)
            _record("actionChains", duration=time.perf_counter() - st, status="PASS", step=step)
            return result
        except transient as exc:
            last_error = exc
            if driver is not None:
                try:
                    driver.execute_script(
                        "window.scrollBy(0, 0); document.documentElement.scrollTop = document.documentElement.scrollTop;"
                    )
                except Exception:
                    pass
            time.sleep(min(0.25 * attempt, 1.0))
        except Exception as exc:
            last_error = exc
            break

    sc = shot(driver, "actionChains") if driver else ""
    _record(
        "actionChains",
        duration=time.perf_counter() - st,
        status="FAIL",
        error=repr(last_error) if last_error else "ActionChains execution failed",
        screenshot=sc,
        step=step,
    )
    if last_error:
        raise last_error
    raise RuntimeError("ActionChains execution failed")


ActionChains.perform = _retry_actionchains_perform

# Text typed through ActionChains is tracked for screenshot redaction too.
_original_ac_send_keys_to_element = ActionChains.send_keys_to_element
_original_ac_send_keys = ActionChains.send_keys


def _tracked_send_keys_to_element(self, element, *keys_to_send):
    _track_typed(element, keys_to_send)
    return _original_ac_send_keys_to_element(self, element, *keys_to_send)


def _tracked_send_keys(self, *keys_to_send):
    _track_typed(None, keys_to_send)
    return _original_ac_send_keys(self, *keys_to_send)


ActionChains.send_keys_to_element = _tracked_send_keys_to_element
ActionChains.send_keys = _tracked_send_keys


# ---------------------------------------------------------------------------
# pytest hooks — recognized automatically because this module's source is
# the generated conftest.py itself (see module docstring).
# ---------------------------------------------------------------------------
def pytest_runtest_setup(item):
    CUR.nodeid = item.nodeid
    CUR.typed_elements = []  # typed-field redaction state is per test
    CUR.typed_values = []


def pytest_runtest_call(item):
    CUR.nodeid = item.nodeid


def pytest_runtest_makereport(item, call):
    if call.when != "call" or not call.excinfo:
        return
    drv = getattr(getattr(item, "instance", None), "driver", None)
    if drv is None:
        for v in getattr(item, "funcargs", {}).values():
            if isinstance(v, WebDriver):
                drv = v
                break
    sc = shot(drv, "test_failure") if drv else ""
    import traceback as _traceback
    _record(
        "TEST_FAILURE",
        duration=0,
        status="FAIL",
        error="".join(_traceback.format_exception_only(call.excinfo.type, call.excinfo.value)).strip(),
        screenshot=sc,
    )


def pytest_sessionfinish(session, exitstatus):
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "steps.json").write_text(json.dumps(EVENTS, indent=2), encoding="utf-8")
    except Exception:
        pass
