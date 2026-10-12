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
import socket
import sys
import time
import threading
import inspect
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait


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


def _step_for_line(filename, lineno, action):
    """The Selenium IDE step ("# N | action | target | value" comment) that the source line
    `lineno` of `filename` belongs to: the nearest preceding comment with this action, else
    the nearest preceding comment. None when the file has none."""
    steps = _load_step_comments(filename)
    if not steps:
        return None

    before = [s for s in steps if s["line"] <= lineno]
    if not before:
        return None

    candidates = [s for s in before if s["action"].lower() == action.lower()]
    if candidates:
        return candidates[-1]
    return before[-1]


def _source_step(action, target="", value=""):
    frame = _test_source_frame()
    if not frame:
        return None
    return _step_for_line(frame.filename, frame.lineno, action)


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


# ---------------------------------------------------------------------------
# Live execution events: an append-only events.jsonl next to steps.json.
#
# One JSON object per line, flushed immediately, so another process can follow the
# run while pytest is still executing. steps.json (above) is unchanged and remains
# the only input of result persistence. Events are advisory: writing one can never
# fail or slow a test, and nothing here reads the file back.
#
# Only fields that are safe without the backend's sanitizer are written: no typed
# values, no exception messages, no tracebacks, no full URLs. A step's target is its
# element tag name (click/type/clear only). A failed step carries the exception
# CLASS name, never its text. Optional descriptive fields (labels, locators, page,
# "# STEP:" text) are filtered as described further below. "nodeid" is the pytest id (parametrized ids can embed
# data), so it is an internal correlation key: the consumer must sanitize it before
# it is shown to anyone.
# ---------------------------------------------------------------------------
EVENTS_PATH = RUN_DIR / "events.jsonl"
EVENT_LOCK = threading.Lock()
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,30}$")
_TAG_TARGET_ACTIONS = ("type", "click", "clear")


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _emit(event):
    """Appends one complete line and flushes it. Never raises."""
    try:
        line = json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
        with EVENT_LOCK:
            EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(EVENTS_PATH, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
    except Exception:
        pass


def _event_target(action, target):
    if action in _TAG_TARGET_ACTIONS and isinstance(target, str) and _TAG_RE.match(target):
        return target
    return ""


# ---------------------------------------------------------------------------
# Safe, human-readable facts about a step (optional event fields).
#
# Everything below is filtered HERE, before it is written, and filtered again by the
# backend before it is published. A fact that cannot be shown to be safe is left
# out, never guessed. Never read or written: typed text, an input's value, cookies,
# storage, headers, exception messages, the page's HTML.
#
#   element_role / element_label / element_hint   what the element is called
#   sensitive                                      the field looks like a credential
#   locator_by / locator_value                     how the test found the element
#   description                                    the author's "# STEP: ..." comment
#   page                                           host + path of an opened URL
#   wait_condition / wait_timeout / wait_until_not a WebDriverWait
#   window_size, source_line                       set_window_size / failed assert
# ---------------------------------------------------------------------------
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LONG_DIGITS_RE = re.compile(r"\d{8,}|\d(?:[ -]?\d){11,}")  # ids, phone/card-like numbers
_TOKEN_LIKE_RE = re.compile(r"[A-Za-z0-9+/_=-]{20,}")
_SECRET_ASSIGN_RE = re.compile(r"(?i)(?:password|passwd|pwd|secret|token|api[-_ ]?key)\s*[=:]\s*\S")
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)password|passwd|passcode|passphrase|\bpwd\b|secret|token|api[-_ ]?key|\botp\b|one[-_ ]?time"
    r"|\bcvv\b|\bcvc\b|card[-_ ]?number|credit[-_ ]?card|\bpin\b|\bssn\b|credential"
    r"|(?:verification|security|auth(?:entication)?)[-_ ]?code|\b2fa\b|\bmfa\b"
)
_SENSITIVE_AUTOCOMPLETE = ("current-password", "new-password", "one-time-code")
_LOCATOR_BY = ("id", "name", "xpath", "link text", "partial link text", "tag name", "class name", "css selector")
_IDE_BY = {"id": "id", "name": "name", "css": "css selector", "xpath": "xpath", "linktext": "link text",
           "link": "link text", "class": "class name", "tag": "tag name"}
_ROLE_RE = re.compile(r"^[a-z]{3,20}$")
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,60}$")
_TOKEN_SEGMENT_RE = re.compile(r"^(?:[A-Za-z0-9_-]{20,}|[0-9a-fA-F]{16,})$|\d{8,}")


def _safe_text(value, limit=40):
    """Short human text (a label, a button's caption). None unless it is clearly not
    a secret: no '@', no long digit runs, nothing token-like, no 'password=...'."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit or _CONTROL_RE.search(text) or "@" in text or "<" in text or ">" in text:
        return None
    if _LONG_DIGITS_RE.search(text) or _TOKEN_LIKE_RE.search(text) or _SECRET_ASSIGN_RE.search(text):
        return None
    return text


def _safe_locator_value(value, limit=80):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or _CONTROL_RE.search(text):
        return None
    if _LONG_DIGITS_RE.search(text) or re.search(r"[A-Za-z0-9+/_=-]{32,}", text) or _SECRET_ASSIGN_RE.search(text):
        return None
    return text


def _safe_page(url):
    """host[:port] + path of an http(s) URL. Credentials, query, fragment and
    token-like path segments are dropped. "" when nothing safe remains."""
    try:
        parts = urlsplit(str(url).strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return ""
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        segments = [("…" if _TOKEN_SEGMENT_RE.search(s) else s) for s in parts.path.split("/")]
        page = host + "/".join(segments)
        if len(page) > 100 or _CONTROL_RE.search(page):
            return ""
        return page
    except Exception:
        return ""


def _locator_fields(by, value):
    if by in _LOCATOR_BY:
        safe = _safe_locator_value(value)
        if safe:
            return {"locator_by": by, "locator_value": safe}
    return {}


def _ide_locator(target):
    """("id", "username") from a Selenium IDE target such as "id=username"."""
    if isinstance(target, str) and "=" in target:
        kind, _, rest = target.partition("=")
        by = _IDE_BY.get(kind.strip().lower())
        if by and rest:
            return by, rest
    return None


_FACTS_JS = (
    "var e=arguments[0]; if(!e||!e.tagName){return null;}"
    "var t=e.tagName.toLowerCase(); var ty=(e.getAttribute('type')||'').toLowerCase();"
    "function a(n){return (e.getAttribute(n)||'').slice(0,200);}"
    "var l=''; try{ if(e.labels&&e.labels.length){ l=(e.labels[0].innerText||e.labels[0].textContent||'').slice(0,200);} }catch(x){}"
    "var o={tag:t,type:ty,role:a('role'),aria:a('aria-label'),id:(e.id||'').slice(0,200),name:a('name'),"
    "placeholder:a('placeholder'),autocomplete:a('autocomplete'),label:l,title:a('title')};"
    # A caption is read only from things that ARE a caption; never an input's value.
    "var cap=(t==='button'||t==='a'||t==='summary'||o.role==='button'||o.role==='link');"
    "var sub=(t==='input'&&(ty==='submit'||ty==='button'||ty==='reset'));"
    "if(cap){o.text=(e.innerText||e.textContent||'').slice(0,200);}"
    "else if(sub){o.text=a('value');}"
    "return o;"
)


def _element_facts(element):
    """One browser round trip. None whenever it cannot be had; never raises."""
    try:
        driver = getattr(element, "_parent", None) or getattr(element, "parent", None)
        if driver is None:
            return None
        facts = driver.execute_script(_FACTS_JS, element)
        return facts if isinstance(facts, dict) else None
    except Exception:
        return None


def _implied_role(tag, kind, explicit):
    if isinstance(explicit, str) and _ROLE_RE.match(explicit):
        return explicit
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag == "select":
        return "combobox"
    if tag == "textarea":
        return "textbox"
    if tag == "input":
        return {"checkbox": "checkbox", "radio": "radio", "submit": "button", "button": "button",
                "reset": "button", "image": "button", "range": "slider"}.get(kind, "textbox")
    return ""


def _is_sensitive(facts):
    if str(facts.get("type", "")).lower() == "password":
        return True
    auto = str(facts.get("autocomplete", "")).lower()
    if auto in _SENSITIVE_AUTOCOMPLETE or auto.startswith("cc-"):
        return True
    joined = " ".join(str(facts.get(k, "")) for k in ("id", "name", "aria", "placeholder", "label", "autocomplete"))
    return bool(_SENSITIVE_NAME_RE.search(joined))


def _element_fields(element, step):
    fields = {}
    facts = _element_facts(element)
    if facts:
        tag = str(facts.get("tag", "")).lower()
        sensitive = _is_sensitive(facts)
        role = _implied_role(tag, str(facts.get("type", "")).lower(), facts.get("role"))
        if role:
            fields["element_role"] = role
        if sensitive:
            fields["sensitive"] = True
        # The label of a credential field ("Password") is shown; its placeholder, which
        # may be an example value, is not.
        names = ("aria", "label") if sensitive else ("aria", "label", "text", "title")
        for key in names:
            label = _safe_text(facts.get(key))
            if label:
                fields["element_label"] = label
                break
        if not sensitive:
            hint = _safe_text(facts.get("placeholder"))
            if hint and hint != fields.get("element_label"):
                fields["element_hint"] = hint

    pair = getattr(element, "_selenium_compat_locator", None)
    locator = pair if isinstance(pair, tuple) and len(pair) == 2 else None
    if locator is None and step and step.get("target"):
        locator = _ide_locator(step["target"])
    if locator is None and facts:
        if facts.get("id"):
            locator = ("id", facts["id"])
        elif facts.get("name"):
            locator = ("name", facts["name"])
    if locator is not None:
        fields.update(_locator_fields(locator[0], locator[1]))
    return fields


# "# STEP: Open the login page" directly above a statement (blank lines allowed)
# titles that statement. Only the nearest comment counts.
_STEP_COMMENT_RE = re.compile(r"^\s*#\s*STEP\s*:\s*(.*?)\s*$", re.IGNORECASE)
_STMT_CACHE = {}


def _statement_index(filename):
    if filename not in _STMT_CACHE:
        try:
            source = Path(filename).read_text(encoding="utf-8", errors="ignore")
            spans = sorted(
                (n.lineno, n.end_lineno)
                for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.stmt) and getattr(n, "end_lineno", None)
            )
            _STMT_CACHE[filename] = (source.splitlines(), spans)
        except Exception:
            _STMT_CACHE[filename] = ([], [])
    return _STMT_CACHE[filename]


def _test_frame():
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_name.startswith("test"):
            return frame
        frame = frame.f_back
    return None


def _explicit_description():
    try:
        frame = _test_frame()
        if frame is None:
            return ""
        lines, spans = _statement_index(frame.f_code.co_filename)
        if not lines:
            return ""
        line = frame.f_lineno
        containing = [s for s in spans if s[0] <= line <= s[1]]
        start = min(containing, key=lambda s: s[1] - s[0])[0] if containing else line
        i = start - 2
        while i >= 0 and not lines[i].strip():
            i -= 1
        match = _STEP_COMMENT_RE.match(lines[i]) if i >= 0 else None
        return (_safe_text(match.group(1), 100) or "") if match else ""
    except Exception:
        return ""


def _event_meta(action, target, step, element, extra):
    meta = {}
    try:
        if action == "open":
            page = _safe_page(target)
            if page:
                meta["page"] = page
        if element is not None and action in _TAG_TARGET_ACTIONS:
            meta.update(_element_fields(element, step))
        if extra:
            meta.update(extra)
        description = _explicit_description()
        if description:
            meta["description"] = description
    except Exception:
        pass
    return meta


def _next_step_index():
    index = getattr(CUR, "step_seq", 0)
    CUR.step_seq = index + 1
    return index


def _step_begin(action, target, step, element=None, extra=None):
    """Emits step_started and returns a token for _step_end. A wrapped operation that
    runs inside another wrapped operation (depth > 0) is part of the outer step: it
    gets a token that emits nothing, so it never produces a second step."""
    try:
        depth = getattr(CUR, "step_depth", 0)
        CUR.step_depth = depth + 1
        if depth:
            return {"nested": True, "done": False}
        index = _next_step_index()
        nodeid = _node()
        name = step["action"] if step else action
        token = {
            "nested": False,
            "done": False,
            "nodeid": nodeid,
            "step_id": f"{nodeid}#{index}",
            "step_index": index,
            "step_number": step["number"] if step else None,
            "action": name,
            "target": _event_target(action, target),
            "meta": _event_meta(action, target, step, element, extra),
            "t0": time.perf_counter(),
        }
        _emit({
            "type": "step_started",
            "nodeid": token["nodeid"],
            "step_id": token["step_id"],
            "step_index": index,
            "step_number": token["step_number"],
            "action": name,
            "target": token["target"],
            **token["meta"],
            "status": "RUNNING",
            "timestamp": _utc_now(),
        })
        return token
    except Exception:
        return None


def _step_end(token, status, exc=None):
    """Emits step_passed / step_failed once per token (later calls are ignored)."""
    try:
        if token is None or token["done"]:
            return
        token["done"] = True
        CUR.step_depth = max(0, getattr(CUR, "step_depth", 1) - 1)
        if token["nested"]:
            return
        event = {
            "type": "step_passed" if status == "PASS" else "step_failed",
            "nodeid": token["nodeid"],
            "step_id": token["step_id"],
            "step_index": token["step_index"],
            "step_number": token["step_number"],
            "action": token["action"],
            "target": token["target"],
            **token["meta"],
            "status": "PASSED" if status == "PASS" else "FAILED",
            "duration": round(max(0.0, time.perf_counter() - token["t0"]), 4),
            "timestamp": _utc_now(),
        }
        if status != "PASS" and exc is not None:
            event["error_category"] = type(exc).__name__
        _emit(event)
    except Exception:
        pass


def _current_test_line():
    """Line of the test file that is executing right now (the failing call), or None."""
    try:
        frame = _test_frame()
        line = frame.f_lineno if frame is not None else None
        return line if isinstance(line, int) and 0 < line < 1_000_000 else None
    except Exception:
        return None


def _emit_failed_step(action, exc, extra=None, step=None):
    """A failure that is only known once it has happened (element not found, a failed
    assertion): one step_failed event, no step_started and no duration. Skipped inside
    another instrumented step, which reports its own outcome. `step` is the Selenium IDE
    step the failing line belongs to (its number is shown; its value never is)."""
    try:
        if getattr(CUR, "step_depth", 0):
            return
        index = _next_step_index()
        nodeid = _node()
        fields = dict(extra or {})
        if "source_line" not in fields and action != "setup":
            line = _current_test_line()
            if line:
                fields["source_line"] = line
        event = {
            "type": "step_failed",
            "nodeid": nodeid,
            "step_id": f"{nodeid}#{index}",
            "step_index": index,
            "step_number": step["number"] if step else None,
            "action": action,
            "target": "",
            **fields,
            "status": "FAILED",
            "timestamp": _utc_now(),
            "error_category": type(exc).__name__,
        }
        description = _explicit_description() if action != "assert" else ""
        if description:
            event["description"] = description
        _emit(event)
    except Exception:
        pass


# A failure that happens inside the test body but is only an exception so far: the test
# may still catch it (try/except NoSuchElementException ...). It is written to steps.json
# - and so to the stored results - only if that same exception object is what ends the
# test (see pytest_runtest_makereport). Until then it is remembered here.
def _remember_failure(action, exc, step, locator, started):
    try:
        if getattr(CUR, "step_depth", 0):
            return
        CUR.pending_failure = {
            "exc": exc, "action": action, "step": step, "locator": locator,
            "duration": max(0.0, time.perf_counter() - started),
        }
    except Exception:
        pass


def _failed_step_target(pending):
    """Target text of a recorded failed step: the Selenium IDE target when the line
    belongs to an IDE step, else the safe locator, else nothing."""
    step = pending.get("step")
    if step:
        return step.get("target", "")
    locator = pending.get("locator")
    if locator and locator[0] in _LOCATOR_BY and _safe_locator_value(locator[1]):
        return f"{locator[0]}={locator[1]}"
    return ""


def _failure_text(exc):
    """"ExceptionType: message" for the stored step (the backend sanitizes the message when
    it is saved and shown: Selenium's stacktrace block, paths, known secrets)."""
    return f"{type(exc).__name__}: {exc}"[:2000]


def _record_failed_step(action, step, target, duration, drv, error):
    """steps.json record of the action that failed, with a screenshot when one can be
    taken. The IDE step keeps its number; its value (which can be typed text) is dropped."""
    recorded = {**step, "action": action, "value": ""} if step else None
    label = f"step_{step['number']}" if step else action
    screenshot = shot(drv, label) if drv else ""
    _record(action, target, "", duration, "FAIL", error, screenshot, recorded)


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
        extra = None
        if action == "setWindowSize" and len(a) >= 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in a[:2]):
            extra = {"window_size": f"{a[0]}x{a[1]}"}
        token = _step_begin(action, target, step, self if isinstance(self, WebElement) else None, extra)
        try:
            result = old(self, *a, **k)
            _step_end(token, "PASS")
            _record(action, target, value, time.perf_counter() - st, "PASS", step=step)
            return result
        except Exception as e:
            _step_end(token, "FAIL", e)
            drv = self if hasattr(self, "save_screenshot") else getattr(self, "parent", None)
            sc = shot(drv, f"step_{step['number']}" if step else action)
            _record(action, target, value, time.perf_counter() - st, "FAIL", repr(e), sc, step)
            raise

    fn._selenium_compat_wrapped = True
    fn._selenium_compat_original = old
    setattr(cls, name, fn)


# ---------------------------------------------------------------------------
# Browser constructor patching + implicit wait + legacy initialization.
#
# Selenium IDE files are commonly generated with webdriver.Chrome()
# regardless of which browser the Run UI selected. Every Selenium browser
# constructor is patched to the Run's selected browser (read from the
# SELENIUM_BROWSER env var, already set by execution_service), so selecting
# Edge or Firefox can never silently fall back to Chrome. The 10-second
# implicit wait (the platform's existing readiness mechanism) is applied
# right after construction, in the same place, rather than via a second
# separate WebDriver.__init__ patch.
#
# Legacy initialization (Selenium 3 era exports, e.g.
#   webdriver.Remote(command_executor='http://localhost:4444/wd/hub',
#                    desired_capabilities=DesiredCapabilities.CHROME) )
# is adapted when the call happens, not by rewriting the uploaded source: the
# uploaded file, its stored copy and its line numbers are never touched.
#   * desired_capabilities / capabilities become Options of the Run's browser.
#   * chrome_options= / firefox_options= / edge_options= become options=.
#   * webdriver.Remote honours the script's command_executor. Only the Selenium
#     IDE default (localhost:4444) with nothing listening - i.e. no Grid in this
#     environment - starts the Run's browser locally, with a visible notice.
#   * Anything that cannot be adapted safely (executable_path, an unknown
#     capability, conflicting values, ...) raises ConvergenceCompatibilityError:
#     a setup error that names the problem, the script line and the original
#     Selenium error. It is never reported as a test assertion failure.
# A call that uses only Selenium 4 arguments is passed through unchanged.
# ---------------------------------------------------------------------------
_BROWSER_CLASSES = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}
_selected_browser = os.environ.get("SELENIUM_BROWSER", "chrome").lower().strip()
if _selected_browser not in _BROWSER_CLASSES:
    _selected_browser = "chrome"

_selected_name = _BROWSER_CLASSES[_selected_browser]
_original_ctors = {name: getattr(webdriver, name) for name in _BROWSER_CLASSES.values() if hasattr(webdriver, name)}
_selected_original_ctor = _original_ctors.get(_selected_name)
_original_remote = webdriver.Remote

# Looked up at call time (so tests can substitute them without starting a browser).
_CTORS = {"selected": _selected_original_ctor, "remote": _original_remote}
_OPTIONS_CLASSES = {"chrome": ChromeOptions, "edge": EdgeOptions, "firefox": FirefoxOptions}


class ConvergenceCompatibilityError(Exception):
    """The script's browser initialization uses something Convergence cannot adapt.
    A setup problem to fix in the script or the Run's browser, not a failed test."""


def _compat_location():
    """"file.py:LINE" of the script line that made the call (first frame that is
    neither this module nor Selenium)."""
    try:
        here = os.path.normcase(os.path.abspath(__file__))
        frame = sys._getframe(1)
        while frame is not None:
            name = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
            if name != here and f"{os.sep}selenium{os.sep}" not in name:
                return f"{os.path.basename(name)}:{frame.f_lineno}"
            frame = frame.f_back
    except Exception:
        pass
    return ""


def _setup_error(message, original=None):
    """Raises ConvergenceCompatibilityError (and reports a failed "setup" step to the
    live timeline). Values of capabilities are never put in the message: only names."""
    where = _compat_location()
    text = message + (f" (script line {where})" if where else "")
    if original is not None:
        text += f" Original error: {type(original).__name__}: {original}"
    error = ConvergenceCompatibilityError(text)
    _emit_failed_step("setup", error)
    raise error from original


_NOTICES = []


def _compat_notice(text):
    """A visible, non-fatal note about something that was adapted or not applied."""
    if text not in _NOTICES:
        _NOTICES.append(text)
        print(f"[convergence] {text}", file=sys.stderr, flush=True)


def _finish_driver(driver):
    driver.implicitly_wait(IMPLICIT_WAIT_SECONDS)
    CUR.driver = driver
    return driver


# -- capabilities -> Options ---------------------------------------------------
_BROWSER_NAMES = {"chrome": "chrome", "microsoftedge": "edge", "msedge": "edge", "edge": "edge", "firefox": "firefox"}
_W3C_CAPS = frozenset((
    "acceptInsecureCerts", "pageLoadStrategy", "platformName", "browserVersion", "proxy", "setWindowRect",
    "strictFileInteractability", "timeouts", "unhandledPromptBehavior", "webSocketUrl",
))
_CAP_ALIASES = {"acceptSslCerts": "acceptInsecureCerts", "version": "browserVersion", "platform": "platformName"}
# JSON-wire capabilities that no longer mean anything: dropped, with a notice.
_LEGACY_NOOP_CAPS = frozenset((
    "javascriptEnabled", "takesScreenshot", "handlesAlerts", "cssSelectorsEnabled", "nativeEvents", "rotatable",
    "locationContextEnabled", "databaseEnabled", "applicationCacheEnabled", "browserConnectionEnabled",
    "webStorageEnabled", "takesHeapSnapshot", "hasTouchScreen", "networkConnectionEnabled",
))
_VENDOR_OPTION_CAPS = {"goog:chromeOptions": "chrome", "chromeOptions": "chrome", "ms:edgeOptions": "edge",
                       "moz:firefoxOptions": "firefox"}
_VENDOR_PREFIX_OWNER = {"goog": "chrome", "ms": "edge", "moz": "firefox"}
_REMOVED_ARGUMENTS = {
    "executable_path": "the path of a driver on the developer's machine; Convergence supplies the driver",
    "service_args": "pass a selenium Service object (service=) instead",
    "service_log_path": "pass a selenium Service object (service=) instead",
    "firefox_profile": "set preferences on FirefoxOptions instead",
    "firefox_binary": "set binary_location on FirefoxOptions instead",
    "browser_profile": "set preferences on FirefoxOptions instead",
    "proxy": "set the proxy on Options instead",
    "port": "pass a selenium Service object (service=) instead",
}


def _apply_vendor_options(options, owner, key, value, problems, notes):
    if owner != _selected_browser:
        notes.append(f"'{key}' is for {owner} but this Run uses {_selected_browser}; it was not applied")
        return
    if not isinstance(value, dict):
        problems.append(f"'{key}' must be a dict")
        return
    for name, item in value.items():
        if name == "args" and isinstance(item, (list, tuple)):
            for arg in item:
                if isinstance(arg, str) and arg not in options.arguments:
                    options.add_argument(arg)
        elif name == "prefs" and isinstance(item, dict):
            if _selected_browser == "firefox":
                current = dict(getattr(options, "preferences", {}) or {})
                for pref, pref_value in item.items():
                    if pref in current and current[pref] != pref_value:
                        problems.append(f"preference '{pref}' is set twice with different values")
                    else:
                        options.set_preference(pref, pref_value)
            else:
                current = dict(getattr(options, "experimental_options", {}).get("prefs", {}) or {})
                for pref, pref_value in item.items():
                    if pref in current and current[pref] != pref_value:
                        problems.append(f"preference '{pref}' is set twice with different values")
                merged = {**item, **current}
                options.add_experimental_option("prefs", merged)
        elif name in ("binary", "extensions", "localState", "profile"):
            notes.append(f"'{key}.{name}' refers to files on the developer's machine; it was not applied")
        elif _selected_browser != "firefox":
            options.add_experimental_option(name, item)
        else:
            notes.append(f"'{key}.{name}' is not supported for firefox; it was not applied")


def _legacy_caps_to_options(caps, options=None):
    """Options of the Run's browser built from legacy capabilities. Duplicates and
    conflicts (with each other or with `options`) are errors, never silent overrides."""
    if not isinstance(caps, dict):
        _setup_error(f"desired_capabilities must be a dict of capabilities, not {type(caps).__name__}.")
    selected = _selected_browser
    if options is None:
        options = _OPTIONS_CLASSES[selected]()
    elif not isinstance(options, _OPTIONS_CLASSES[selected]):
        _setup_error(
            f"The script passes both capabilities and options of a different browser than this Run's "
            f"browser ({selected}); Convergence cannot combine them."
        )
    existing = dict(getattr(options, "capabilities", {}) or {})
    problems, notes, unknown = [], [], []
    w3c, vendor_caps, vendor_options = {}, {}, []

    for raw, value in caps.items():
        if raw == "browserName":
            name = _BROWSER_NAMES.get(str(value).lower().strip())
            if name is None:
                problems.append(f"browserName '{value}' is not supported (Convergence runs chrome, edge or firefox)")
            elif name != selected:
                _compat_notice(
                    f"The script asks for {name}, but this Run's browser is {selected}; {selected} is used. "
                    f"(Change the project's browser to run the script in {name}.)"
                )
            continue
        key = _CAP_ALIASES.get(raw, raw)
        if raw in _LEGACY_NOOP_CAPS or (raw == "marionette" and value is not False):
            notes.append(f"legacy capability '{raw}' has no effect in Selenium 4 and was ignored")
        elif raw == "marionette":
            problems.append("marionette=False (the legacy Firefox driver) is not supported")
        elif key in _W3C_CAPS:
            if raw == "platform" and str(value).upper() in ("ANY", ""):
                continue
            if key in w3c and w3c[key][1] != value:
                problems.append(f"'{raw}' and '{w3c[key][0]}' both set {key} with different values")
            else:
                w3c[key] = (raw, value)
        elif raw in _VENDOR_OPTION_CAPS:
            vendor_options.append((raw, value))
        elif ":" in raw:
            owner = _VENDOR_PREFIX_OWNER.get(raw.split(":", 1)[0])
            if owner and owner != selected:
                notes.append(f"'{raw}' is for {owner} but this Run uses {selected}; it was not applied")
            else:
                vendor_caps[raw] = value
        else:
            unknown.append(raw)

    if unknown:
        problems.append("unsupported capability name(s): " + ", ".join(sorted(map(str, unknown))))
    for key, (raw, value) in w3c.items():
        if key in existing and existing[key] != value:
            problems.append(f"'{raw}' conflicts with the value already set in options")
        else:
            options.set_capability(key, value)
    for key, value in vendor_caps.items():
        if key in existing and existing[key] != value:
            problems.append(f"'{key}' conflicts with the value already set in options")
        else:
            options.set_capability(key, value)
    for key, value in vendor_options:
        _apply_vendor_options(options, _VENDOR_OPTION_CAPS[key], key, value, problems, notes)

    if problems:
        _setup_error("The script's capabilities cannot be converted to Selenium 4 options: " + "; ".join(problems) + ".")
    for note in notes:
        _compat_notice(note)
    return options


def _reject_removed_arguments(kwargs):
    for name in kwargs:
        if name in _REMOVED_ARGUMENTS:
            _setup_error(
                f"The argument '{name}' was removed in Selenium {selenium.__version__} and cannot be adapted "
                f"automatically: {_REMOVED_ARGUMENTS[name]}."
            )


def _wrap_signature_error(error):
    """A TypeError from a changed constructor signature becomes an actionable setup error;
    any other exception (a genuine start-up failure) is left alone."""
    text = str(error)
    if "unexpected keyword argument" in text or "positional argument" in text:
        _setup_error(
            f"The browser initialization does not match Selenium {selenium.__version__}'s API and Convergence "
            f"cannot adapt it automatically. Use Selenium 4 Options/Service arguments.",
            error,
        )


def _prepare_local_call(args, kwargs):
    kwargs = dict(kwargs)
    if args and isinstance(args[0], (str, os.PathLike)):
        _setup_error(
            "A driver path was passed as the first argument (Selenium 3 executable_path), which Selenium 4 "
            "no longer accepts; Convergence supplies the driver."
        )
    legacy_options = [name for name in ("chrome_options", "firefox_options", "edge_options") if name in kwargs]
    legacy_caps = [name for name in ("desired_capabilities", "capabilities") if name in kwargs]
    if not legacy_options and not legacy_caps:
        _reject_removed_arguments(kwargs)
        return args, kwargs  # Selenium 4 arguments: unchanged

    if len(legacy_caps) > 1:
        _setup_error("Both desired_capabilities and capabilities are given; use one.")
    if len(legacy_options) > 1 or (legacy_options and "options" in kwargs):
        _setup_error(f"The browser options are given more than once ({', '.join(legacy_options + (['options'] if 'options' in kwargs else []))}).")
    if legacy_options:
        kwargs["options"] = kwargs.pop(legacy_options[0])
    caps = kwargs.pop(legacy_caps[0]) if legacy_caps else None
    _reject_removed_arguments(kwargs)
    if caps is not None:
        kwargs["options"] = _legacy_caps_to_options(caps, kwargs.get("options"))
    return args, kwargs


def _start_local(args, kwargs):
    try:
        driver = _CTORS["selected"](*args, **kwargs)
    except TypeError as error:
        _wrap_signature_error(error)
        raise
    return _finish_driver(driver)


def _make_browser_factory(name):
    def create(*args, **kwargs):
        args, kwargs = _prepare_local_call(args, kwargs)
        return _start_local(args, kwargs)

    create.__name__ = name
    create.__qualname__ = name
    return create


# -- webdriver.Remote ------------------------------------------------------------
_IDE_DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1")


def _ide_default_endpoint(url):
    """(host, port) when `url` is the Selenium IDE / Grid default on this machine
    (localhost:4444); otherwise None. Any other endpoint is used exactly as given."""
    try:
        parts = urlsplit(url)
        if parts.hostname in _IDE_DEFAULT_HOSTS and parts.port == 4444:
            return parts.hostname, parts.port
    except Exception:
        pass
    return None


def _listening(host, port, timeout=0.5):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _make_remote_factory():
    def create_remote(*args, **kwargs):
        kwargs = dict(kwargs)
        positional = list(args)
        executor = positional.pop(0) if positional else kwargs.pop("command_executor", "http://127.0.0.1:4444")
        if positional and isinstance(positional[0], dict):  # Selenium 3: (executor, desired_capabilities, ...)
            kwargs["desired_capabilities"] = positional.pop(0)
        if positional:
            _setup_error(
                "webdriver.Remote was called with positional arguments after the capabilities (Selenium 3 "
                "browser_profile/proxy/...), which Selenium 4 no longer accepts. Use keyword arguments and Options."
            )

        legacy_caps = [name for name in ("desired_capabilities", "capabilities") if name in kwargs]
        if len(legacy_caps) > 1:
            _setup_error("Both desired_capabilities and capabilities are given; use one.")
        caps = kwargs.pop(legacy_caps[0]) if legacy_caps else None
        _reject_removed_arguments(kwargs)

        options = kwargs.pop("options", None)
        if caps is not None:
            options = _legacy_caps_to_options(caps, options)
        elif options is None:
            options = _OPTIONS_CLASSES[_selected_browser]()

        endpoint = _ide_default_endpoint(executor) if isinstance(executor, str) else None
        if endpoint is not None and not _listening(*endpoint):
            _compat_notice(
                f"No Selenium server answers at {executor}; Convergence runs this Run's browser "
                f"({_selected_browser}) inside the backend, as it does for webdriver.{_selected_name}()."
            )
            return _start_local((), {"options": options})

        try:
            driver = _CTORS["remote"](command_executor=executor, options=options, **kwargs)
        except TypeError as error:
            _wrap_signature_error(error)
            raise
        return _finish_driver(driver)

    create_remote.__name__ = "Remote"
    create_remote.__qualname__ = "Remote"
    return create_remote


if _selected_original_ctor is not None:
    for _ctor_name in _original_ctors:
        setattr(webdriver, _ctor_name, _make_browser_factory(_selected_name))
    webdriver.Remote = _make_remote_factory()


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
    token = None
    try:
        try:
            target = getattr(self, "tag_name", "")
        except Exception:
            pass
        token = _step_begin("click", target, step, self)
        try:
            self._parent.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});", self
            )
        except Exception:
            pass

        clicked = _click_current_or_fresh(self)
        _step_end(token, "PASS")
        try:
            target = getattr(clicked, "tag_name", target)
        except Exception:
            pass
        _record("click", target, duration=time.perf_counter() - st, status="PASS", step=step)
        return None
    except Exception as e:
        _step_end(token, "FAIL", e)
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
    started = time.perf_counter()
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
    try:
        return _native_find_element(self, by, value)
    except Exception as exc:
        # Element-not-found is only known now: report it as one failed step (nothing is
        # reported while the retries above are still looking).
        step = None if getattr(CUR, "step_depth", 0) else _source_step("find")
        _emit_failed_step("find", exc, _locator_fields(by, value), step=step)
        _remember_failure("find", exc, step, (by, value), started)
        raise


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

    token = _step_begin("actionChains", "", step)
    for attempt in range(1, 6):
        try:
            result = _original_actionchains_perform(self, *args, **kwargs)
            _step_end(token, "PASS")
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

    _step_end(token, "FAIL", last_error)
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
# Explicit waits: WebDriverWait.until / until_not are reported as one step each. The
# original method runs unchanged (same timeout, polling, exceptions); only its outcome
# is observed. Time.sleep and other waits are plain Python and are not detected.
# ---------------------------------------------------------------------------
def _wait_meta(wait, method, name):
    meta = {}
    try:
        # Only Selenium's own expected_conditions are named; a lambda or a function of the
        # test's own is "custom" (its name is the author's, not something to display).
        module = getattr(method, "__module__", "") or type(method).__module__
        qualname = getattr(method, "__qualname__", "") or type(method).__name__
        condition = qualname.split(".<locals>")[0].split(".")[-1]
        named = module == "selenium.webdriver.support.expected_conditions" and _NAME_RE.match(condition)
        meta["wait_condition"] = condition if named else "custom"
        for cell in getattr(method, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, tuple) and len(value) == 2 and all(isinstance(x, str) for x in value):
                meta.update(_locator_fields(value[0], value[1]))
                break
        timeout = getattr(wait, "_timeout", None)
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 0 <= timeout <= 3600:
            meta["wait_timeout"] = round(float(timeout), 2)
        if name == "until_not":
            meta["wait_until_not"] = True
    except Exception:
        pass
    return meta


def _wrap_wait(name):
    old = getattr(WebDriverWait, name, None)
    if old is None or getattr(old, "_selenium_compat_wrapped", False):
        return

    def fn(self, method, *a, **k):
        meta = _wait_meta(self, method, name)
        token = _step_begin("wait", "", None, None, meta)
        try:
            result = old(self, method, *a, **k)
        except Exception as exc:
            _step_end(token, "FAIL", exc)
            if token is not None and not token.get("nested"):
                locator = (meta.get("locator_by"), meta.get("locator_value"))
                _remember_failure("wait", exc, _source_step("wait"), locator if all(locator) else None,
                                  token.get("t0", time.perf_counter()))
            raise
        _step_end(token, "PASS")
        return result

    fn._selenium_compat_wrapped = True
    fn._selenium_compat_original = old
    setattr(WebDriverWait, name, fn)


_wrap_wait("until")
_wrap_wait("until_not")


# ---------------------------------------------------------------------------
# pytest hooks — recognized automatically because this module's source is
# the generated conftest.py itself (see module docstring).
# ---------------------------------------------------------------------------
def pytest_runtest_setup(item):
    CUR.nodeid = item.nodeid
    CUR.typed_elements = []  # typed-field redaction state is per test
    CUR.typed_values = []
    CUR.step_seq = 0  # live-event step numbering restarts for every test
    CUR.step_depth = 0
    CUR.pending_failure = None


def pytest_runtest_logstart(nodeid, location):
    _emit({"type": "test_started", "nodeid": nodeid, "status": "RUNNING", "timestamp": _utc_now()})


_TEST_STATUS = {"passed": "PASSED", "failed": "FAILED", "skipped": "SKIPPED"}


def pytest_runtest_logreport(report):
    # The "call" report is the test's outcome; a setup report only ends the test when
    # setup itself failed or skipped (there is no call phase then).
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        _emit({
            "type": "test_finished",
            "nodeid": report.nodeid,
            "status": _TEST_STATUS.get(report.outcome, "FAILED"),
            "duration": round(max(0.0, float(getattr(report, "duration", 0.0) or 0.0)), 4),
            "timestamp": _utc_now(),
        })


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
    pending = getattr(CUR, "pending_failure", None)
    CUR.pending_failure = None
    if pending is not None and call.excinfo.value is pending["exc"]:
        # The action that actually ended the test (e.g. the element lookup before a mouse-over)
        # becomes a failed step in the stored results, so they agree with the failure analysis.
        _record_failed_step(
            pending["action"], pending["step"], _failed_step_target(pending),
            pending["duration"], drv, _failure_text(pending["exc"]),
        )
    sc = shot(drv, "test_failure") if drv else ""
    if issubclass(call.excinfo.type, AssertionError):
        # A failed assert is only known once it has failed. Only the line number is
        # reported, never the expression (it can contain secrets).
        extra = {}
        step = None
        try:
            # The last traceback entry inside the test's own file (never a library or
            # helper module, whose line numbers would mean nothing here).
            for entry in reversed(list(call.excinfo.traceback)):
                if Path(str(entry.path)) == Path(str(item.path)):
                    line = entry.lineno + 1
                    if 0 < line < 1_000_000:
                        extra = {"source_line": line}
                        # The test's frame is gone by now, so the IDE step comes from the line.
                        step = _step_for_line(str(entry.path), line, "assert")
                    break
        except Exception:
            extra = {}
        _emit_failed_step("assert", call.excinfo.value, extra, step=step)
        # Stored too (class name only: the message or expression can contain secrets).
        _record_failed_step("assert", step, "", 0, drv, "AssertionError()")
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
