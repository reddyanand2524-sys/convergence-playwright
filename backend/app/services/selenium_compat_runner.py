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

import ast
import json
import os
import re
import sys
import textwrap
import time
import threading
import types
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

# Continue-after-error: a failed step (action or assertion) is recorded and
# the test body keeps executing the remaining steps. "0" restores the old
# stop-at-first-failure behavior.
CONTINUE_ON_ERROR = os.environ.get("SELENIUM_COMPAT_CONTINUE_ON_ERROR", "1").strip() != "0"

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


def shot(driver, label):
    if SCREENSHOT_MODE == "none" or driver is None:
        return ""
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{len(EVENTS) + 1:04d}_{label}_{int(time.time() * 1000)}.png"
        driver.save_screenshot(str(SHOTS_DIR / name))
        return name
    except Exception:
        return ""


_SENSITIVE_RE = re.compile(r"pass(word|wd)?|pwd|secret|token|api[-_]?key|otp|cvv", re.I)
REDACTED = "***REDACTED***"
_ERROR_TYPE_RE = re.compile(r"^\s*([A-Za-z_][\w.]*)\(")


def _page_info():
    drv = getattr(CUR, "driver", None)
    url = title = None
    if drv is not None:
        try:
            url = drv.current_url
        except Exception:
            pass
        try:
            title = drv.title
        except Exception:
            pass
    return url, title


def _element_is_password(element):
    try:
        return (element.get_attribute("type") or "").lower() == "password"
    except Exception:
        return False


def _record(action, target="", value="", duration=0, status="PASS", error="", screenshot="", step=None,
            locator=None, sensitive=False, skip_reason="", synthetic=False):
    """status: PASS | FAIL | SKIP. SKIP means the step was defined but could
    not be executed (``skip_reason`` says why); duration is then 0 and the
    step is flagged attempted=False."""

    if status == "PASS" and not screenshot and SCREENSHOT_MODE == "all":
        drv = getattr(CUR, "driver", None)
        if drv is not None:
            label = f"step_{step['number']}" if step else action
            screenshot = shot(drv, label)

    completed = time.time()
    event_target = step["target"] if step else str(target)
    event_value = step["value"] if step else str(value)
    if sensitive or _SENSITIVE_RE.search(event_target or "") or (
        locator and _SENSITIVE_RE.search(str(locator[1]))
    ):
        if action in ("type", "sendKeys", "send_keys", "clear") or sensitive:
            event_value = REDACTED if event_value else event_value

    url, title = _page_info() if status != "SKIP" else (None, None)
    error_type = None
    if error:
        match = _ERROR_TYPE_RE.match(error)
        error_type = match.group(1) if match else None

    event = {
        "nodeid": _node(),
        "step": step["number"] if step else None,
        "action": step["action"] if step else action,
        "target": event_target,
        "value": event_value,
        "status": status,
        "duration": round(duration, 4),
        "error": error,
        "screenshot": screenshot,
        "timestamp": completed,
        "started_at": completed - duration,
        "url": url,
        "title": title,
        "locator_type": locator[0] if locator else None,
        "locator_value": str(locator[1]) if locator else None,
        "attempted": status != "SKIP",
        "skip_reason": skip_reason or None,
        "error_type": error_type,
        "synthetic": synthetic,
    }

    with LOCK:
        EVENTS.append(event)
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            (RUN_DIR / "steps.json").write_text(json.dumps(EVENTS, indent=2), encoding="utf-8")
        except Exception:
            pass


def _wrap(cls, name, action, target_fn=lambda s, a, k: "", value_fn=lambda s, a, k: ""):
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
        try:
            result = old(self, *a, **k)
            _record(action, target, value, time.perf_counter() - st, "PASS", step=step,
                    locator=getattr(self, "_selenium_compat_locator", None),
                    sensitive=name == "send_keys" and _element_is_password(self))
            return result
        except Exception as e:
            drv = self if hasattr(self, "save_screenshot") else getattr(self, "parent", None)
            sc = shot(drv, f"step_{step['number']}" if step else action)
            _record(action, target, value, time.perf_counter() - st, "FAIL", repr(e), sc, step,
                    locator=getattr(self, "_selenium_compat_locator", None),
                    sensitive=name == "send_keys" and _element_is_password(self))
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

_wrap(WebElement, "send_keys", "type", lambda s, a, k: getattr(s, "tag_name", ""), lambda s, a, k: a[0] if a else "")
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
        _record("click", target, duration=time.perf_counter() - st, status="PASS", step=step,
                locator=getattr(self, "_selenium_compat_locator", None))
        return None
    except Exception as e:
        drv = getattr(self, "_parent", None)
        sc = shot(drv, f"step_{step['number']}" if step else "click")
        _record("click", target, duration=time.perf_counter() - st, status="FAIL", error=repr(e), screenshot=sc, step=step,
                locator=getattr(self, "_selenium_compat_locator", None))
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


# ---------------------------------------------------------------------------
# Continue-after-error step guard.
#
# Selenium IDE exports put every step's statements at the top level of the
# test method, so one failing statement (a missing element, a failed
# ``assert``) would normally abort the rest of the test. Before a test body
# runs, its source is parsed and every top-level statement is wrapped as
#
#     if __cc_begin__(n):
#         with __cc_guard__(n):
#             <original statement>
#
# The transformed function is compiled against the uploaded file's own
# filename and line numbers (tracebacks and step correlation are unchanged)
# and only replaces the in-memory test object -- the uploaded file on disk is
# never modified, and assertions are never disabled: a failed assertion is
# caught at its statement boundary, recorded as FAIL, and the test is still
# reported as failed at the end (the first swallowed exception is re-raised).
#
# Dependency handling: a statement whose names (e.g. a variable assigned by a
# failed statement) depend on a failed statement is recorded as SKIP with a
# reason instead of being run. If the browser itself becomes unusable every
# later driver-dependent statement is skipped. Independent statements keep
# running.
# ---------------------------------------------------------------------------
_DRIVER_ROOTS = {"self.driver": "driver", "driver": "driver"}
_BROWSER_DEAD_MARKERS = (
    "invalid session id", "invalidsessionid", "no such window", "nosuchwindow",
    "disconnected", "not reachable", "maxretryerror", "max retries exceeded",
    "connection refused", "already closed", "session deleted",
    "target window already closed", "cannot connect to chrome",
)
_UNWRAPPED = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom,
    ast.Global, ast.Nonlocal, ast.Pass, ast.Return, ast.Break, ast.Continue,
)
STEPS_META = {}
_STEP_INFOS = {}


def _chain(node):
    """Dotted name for ``a.b.c`` rooted at a plain Name, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _loads_and_stores(stmt):
    loads, stores = set(), set()

    def add_target(target):
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                add_target(elt)
            return
        node = target
        while isinstance(node, ast.Subscript):
            node = node.value
        chain = _chain(node)
        if chain:
            stores.add(chain)

    if isinstance(stmt, ast.Assign):
        for t in stmt.targets:
            add_target(t)
    elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        add_target(stmt.target)

    for node in ast.walk(stmt):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            chain = _chain(node)
            if chain:
                loads.add(chain)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.add(node.id)
    return loads, stores


def _depends_on(loads, poisoned):
    for name in loads:
        for bad in poisoned:
            if name == bad or name.startswith(bad + "."):
                return bad
    return None


def _step_for_line(filename, lineno):
    steps = _load_step_comments(filename)
    before = [st for st in steps if st["line"] <= lineno]
    return before[-1] if before else None


class _StepInfo:
    def __init__(self, stmt, filename):
        self.lineno = stmt.lineno
        self.filename = filename
        self.loads, self.stores = _loads_and_stores(stmt)
        self.is_assert = isinstance(stmt, ast.Assert)


def _begin(index):
    """Returns True if the statement should run; otherwise records it as
    SKIP (with the reason) and returns False."""
    info = _STEP_INFOS[index]
    poisoned = getattr(CUR, "poisoned", None)
    if poisoned is None:
        return True

    reason = None
    bad = _depends_on(info.loads, poisoned)
    if bad:
        reason = f"Depends on '{bad}', which is unavailable because {poisoned[bad]} failed"
        origin = poisoned[bad]
    elif getattr(CUR, "browser_dead", None) and _depends_on(info.loads, _DRIVER_ROOTS):
        reason = f"The browser became unusable after {CUR.browser_dead} failed"
        origin = CUR.browser_dead
    else:
        return True

    _record("python", status="SKIP", step=_step_for_line(info.filename, info.lineno), skip_reason=reason)
    for name in info.stores:
        poisoned[name] = origin
    return False


def _format_error(exc):
    return f"{type(exc).__name__}({str(exc)!r})"


class _Guard:
    def __init__(self, index):
        self.info = _STEP_INFOS[index]

    def __enter__(self):
        with LOCK:
            self.start_count = len(EVENTS)
        self.started = time.perf_counter()
        return self

    def _record_unobserved_pass(self):
        """A statement mapped to a Selenium IDE step that completed without
        the compatibility layer observing any Selenium call (e.g. a plain
        Python/JS-store step) still executed; record that truthfully so the
        step is not mistaken for one that never ran."""
        step = _step_for_line(self.info.filename, self.info.lineno)
        if step is None:
            return
        with LOCK:
            observed = any(ev.get("nodeid") == _node() for ev in EVENTS[self.start_count:])
        if not observed:
            _record(step["action"], duration=time.perf_counter() - self.started, status="PASS", step=step, synthetic=True)

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._record_unobserved_pass()
            return False
        # KeyboardInterrupt / SystemExit / pytest.skip / pytest.exit propagate.
        if not isinstance(exc, Exception) and type(exc).__name__ != "Failed":
            return False

        info = self.info
        step = _step_for_line(info.filename, info.lineno)
        with LOCK:
            already = any(
                ev.get("nodeid") == _node() and ev.get("status") == "FAIL"
                for ev in EVENTS[self.start_count:]
            )
        if not already:
            drv = getattr(CUR, "driver", None)
            sc = shot(drv, f"step_{step['number']}" if step else "step_failure") if drv else ""
            _record(
                "assert" if info.is_assert else "python",
                duration=time.perf_counter() - self.started,
                status="FAIL",
                error=_format_error(exc),
                screenshot=sc,
                step=step,
            )

        label = f"step {step['number']}" if step else f"the statement at line {info.lineno}"
        poisoned = getattr(CUR, "poisoned", None)
        if poisoned is not None:
            for name in info.stores:
                poisoned[name] = label
        text = f"{type(exc).__name__} {exc}".lower()
        if any(marker in text for marker in _BROWSER_DEAD_MARKERS):
            CUR.browser_dead = label

        failures = getattr(CUR, "failures", None)
        if failures is None:
            return False
        failures.append(exc)
        return True


class _Transformer(ast.NodeTransformer):
    def __init__(self, filename):
        self.filename = filename

    def visit_FunctionDef(self, node):
        new_body = []
        for position, stmt in enumerate(node.body):
            is_doc = (
                position == 0
                and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
            )
            if is_doc or isinstance(stmt, _UNWRAPPED):
                new_body.append(stmt)
                continue
            index = len(_STEP_INFOS)
            _STEP_INFOS[index] = _StepInfo(stmt, self.filename)
            guard_call = ast.Call(func=ast.Name("__cc_guard__", ast.Load()), args=[ast.Constant(index)], keywords=[])
            guarded = ast.With(items=[ast.withitem(context_expr=guard_call, optional_vars=None)], body=[stmt])
            begin_call = ast.Call(func=ast.Name("__cc_begin__", ast.Load()), args=[ast.Constant(index)], keywords=[])
            new_body.append(ast.copy_location(ast.If(test=begin_call, body=[guarded], orelse=[]), stmt))
        node.body = new_body
        node.decorator_list = []
        return node  # nested functions are deliberately not visited


def _prune_synthetic():
    """Drops a step's "statement executed" marker once the compatibility
    layer observed a real Selenium call for that same step, so each step
    shows only its genuine actions."""
    with LOCK:
        real = {(e["nodeid"], e["step"]) for e in EVENTS if not e.get("synthetic")}
        EVENTS[:] = [e for e in EVENTS if not (e.get("synthetic") and (e["nodeid"], e["step"]) in real)]
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            (RUN_DIR / "steps.json").write_text(json.dumps(EVENTS, indent=2), encoding="utf-8")
        except Exception:
            pass


def _write_meta():
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "steps_meta.json").write_text(
            json.dumps({**STEPS_META, "__run__": {"continuationEnabled": CONTINUE_ON_ERROR}}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _register_meta(item):
    """Records which steps the test *defines* (its Selenium IDE step
    comments) so the report can show steps that never ran. Done at
    collection time so it also exists when setup fails."""
    if type(item).__name__ != "Function":
        return None
    function = inspect.unwrap(item.function)
    try:
        source_lines, first = inspect.getsourcelines(function)
        filename = inspect.getsourcefile(function)
    except Exception:
        return None

    last = first + len(source_lines) - 1
    defined = [st for st in _load_step_comments(filename) if first <= st["line"] <= last] if filename else []
    STEPS_META[item.nodeid] = {
        "file": Path(filename).name if filename else None,
        "defined_steps": [
            {"number": st["number"], "action": st["action"], "target": st["target"], "value": st["value"]}
            for st in defined
        ],
    }
    return function, source_lines, first, filename


def pytest_collection_modifyitems(session, config, items):
    for item in items:
        try:
            _register_meta(item)
        except Exception:
            pass
    _write_meta()


def _install_step_guard(item):
    registered = _register_meta(item)
    if registered is None:
        return
    function, source_lines, first, filename = registered
    _write_meta()

    if not CONTINUE_ON_ERROR or not filename:
        return
    if inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function):
        return

    tree = ast.parse(textwrap.dedent("".join(source_lines)))
    if not tree.body or not isinstance(tree.body[0], ast.FunctionDef):
        return
    ast.increment_lineno(tree, first - 1)
    tree = _Transformer(filename).visit(tree)
    ast.fix_missing_locations(tree)
    code = compile(tree, filename, "exec")

    globals_ = function.__globals__
    globals_["__cc_begin__"] = _begin
    globals_["__cc_guard__"] = _Guard
    namespace = {}
    exec(code, globals_, namespace)
    new_function = namespace[function.__name__]
    new_function.__defaults__ = function.__defaults__
    new_function.__kwdefaults__ = function.__kwdefaults__

    instance = getattr(item, "instance", None)
    item.obj = types.MethodType(new_function, instance) if instance is not None else new_function


# ---------------------------------------------------------------------------
# pytest hooks -- recognized automatically because this module's source is
# the generated conftest.py itself (see module docstring).
# ---------------------------------------------------------------------------
import pytest as _pytest


def pytest_runtest_setup(item):
    CUR.nodeid = item.nodeid
    CUR.driver = None


@_pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    CUR.nodeid = item.nodeid
    CUR.failures = []
    CUR.poisoned = {}
    CUR.browser_dead = None
    try:
        _install_step_guard(item)
    except Exception as exc:
        print(f"[selenium_compat] step guard not installed for {item.nodeid}: {exc!r}", file=sys.stderr)

    try:
        result = yield
    finally:
        _prune_synthetic()

    # Every statement ran to completion (or was skipped); if any swallowed a
    # failure the test must still FAIL. The first original exception is
    # re-raised so pytest's diagnostics still point at the user's own line.
    failures = getattr(CUR, "failures", None) or []
    if failures:
        first = failures[0]
        try:
            first._cc_reraised = True
        except Exception:
            pass
        raise first
    return result


def _record_unguarded_step_failure(item, call, error, screenshot):
    """When the step guard is off (or could not wrap the failing statement)
    the exception ends the test; attribute it to the defined step whose line
    raised, taken from the traceback, so that step is not reported as
    merely "not executed"."""
    try:
        test_file = os.path.normcase(os.path.abspath(str(item.path)))
        line = None
        for entry in call.excinfo.traceback:
            if os.path.normcase(os.path.abspath(str(entry.path))) == test_file:
                line = entry.lineno + 1  # outermost frame in the test file = the test body's own line
                break
        step = _step_for_line(str(item.path), line) if line else None
        if step is None:
            return
        already = any(
            e.get("nodeid") == item.nodeid and e.get("step") == step["number"] and e.get("status") == "FAIL"
            for e in EVENTS
        )
        if not already:
            _record("python", status="FAIL", error=error_repr(call.excinfo.value), screenshot=screenshot, step=step)
    except Exception as exc:
        print(f"[selenium_compat] could not attribute failure to a step: {exc!r}", file=sys.stderr)


def error_repr(exc):
    return f"{type(exc).__name__}({str(exc)!r})"


def pytest_runtest_makereport(item, call):
    if not call.excinfo:
        return
    import traceback as _traceback
    error = "".join(_traceback.format_exception_only(call.excinfo.type, call.excinfo.value)).strip()

    if call.when == "setup":
        # Browser/driver construction (or any fixture) failed before the test
        # body ran. Recorded as a marker so the report can show every defined
        # step as SKIPPED with this as the reason.
        _record("TEST_SETUP_FAILURE", duration=0, status="FAIL", error=error)
        return

    if call.when != "call":
        return
    if getattr(call.excinfo.value, "_cc_reraised", False):
        return  # already recorded as step-level FAIL event(s)
    drv = getattr(getattr(item, "instance", None), "driver", None)
    if drv is None:
        for v in getattr(item, "funcargs", {}).values():
            if isinstance(v, WebDriver):
                drv = v
                break
    sc = shot(drv, "test_failure") if drv else ""
    _record_unguarded_step_failure(item, call, error, sc)
    _record(
        "TEST_FAILURE",
        duration=0,
        status="FAIL",
        error=error,
        screenshot=sc,
    )


def pytest_sessionfinish(session, exitstatus):
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "steps.json").write_text(json.dumps(EVENTS, indent=2), encoding="utf-8")
    except Exception:
        pass
    _write_meta()
