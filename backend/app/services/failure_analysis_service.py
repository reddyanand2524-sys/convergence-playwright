"""Deterministic, rule-based failure analysis.

Turns the records Convergence already stores for a failed test (exception,
failing source line, steps, screenshots, run metadata) into a structured
explanation: a category, evidence, a hedged likely cause and recommended
actions. Nothing here touches the database or the browser, and nothing is
persisted: the analysis is computed on read from existing rows, so reading it
never creates records and the API, UI and reports all derive from the same
function and cannot disagree.

Honesty rules
-------------
* ``evidence`` contains only facts read from recorded data (after redaction).
* ``likely_cause`` is always a hedged hypothesis and ``recommended_actions``
  are suggestions; neither is presented as a confirmed root cause.
* ``confidence`` states how strongly the recorded exception identifies the
  *category* of failure; it does not confirm why it happened:
    high   - a specific exception class whose meaning the library defines
    medium - inferred from message text or from the exception plus context
    low    - generic/unknown exception or too little recorded detail
* Expected/actual values of an assertion are never invented: only the
  recorded assertion message is shown.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from app.services import failure_analysis_rules as rules
from app.services.failure_sanitizer import (
    clean_exception_message,
    clean_exception_type,
    has_sensitive_context,
    mask_known_secrets,
    redact_source_line,
    sanitize_step_error,
    sanitize_step_target,
    redact_url,
    sanitize_traceback,
    truncate,
)

logger = logging.getLogger(__name__)

FAILING_TEST_STATUSES = ("FAILED", "ERROR")
_FAIL_STEP_STATUSES = ("FAIL", "FAILED", "ERROR")
_PASS_STEP_STATUSES = ("PASS", "PASSED")
_NAVIGATION_ACTIONS = ("open", "get", "navigate", "navigateto")
_PYTEST_ASSERT_RE = re.compile(r"^assert\b")
# Generic classes that still point at their category reasonably well.
_MEDIUM_GENERIC_TYPES = {"WebDriverException", "Failed"}
_TIMELINE_LENGTH = 5
_MAX_EVIDENCE_VALUE = 400

CONFIDENCE_NOTE = (
    "Confidence describes how strongly the recorded exception identifies the category of "
    "failure. It does not confirm why the failure happened."
)


# --------------------------------------------------------------------------
# Inputs (adapter-friendly, no ORM dependency)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ScreenshotInput:
    id: int
    capture_reason: str | None = None
    file_name: str | None = None


@dataclass(frozen=True)
class StepInput:
    sequence_index: int
    action: str
    status: str
    step_number: int | None = None
    target: str | None = None
    duration_seconds: float | None = None
    error: str | None = None
    occurred_at: datetime | None = None
    screenshots: tuple[ScreenshotInput, ...] = ()


@dataclass(frozen=True)
class FailureInput:
    test_id: int
    nodeid: str
    status: str
    has_failure_record: bool = False
    file_name: str | None = None
    method_name: str | None = None
    duration_seconds: float | None = None
    completed_at: datetime | None = None
    run_completed_at: datetime | None = None
    browser: str | None = None
    exception_type: str | None = None
    exception_message: str | None = None
    source_file: str | None = None
    source_line_number: int | None = None
    source_line: str | None = None
    raw_traceback: str | None = None
    steps: tuple[StepInput, ...] = ()
    test_screenshots: tuple[ScreenshotInput, ...] = ()


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceItem:
    label: str
    value: str


@dataclass(frozen=True)
class TimelineStep:
    role: str  # "failed" | "last_successful" | "preceding"
    sequence_index: int
    step_number: int | None
    action: str
    target: str | None
    status: str
    duration_seconds: float | None
    occurred_at: datetime | None
    error: str | None
    screenshot_ids: tuple[int, ...]


@dataclass(frozen=True)
class FailureAnalysis:
    failure_category: str
    failure_title: str
    summary: str
    likely_cause: str
    confidence: str
    confidence_note: str
    # exception_type | pytest_assertion | exception_type+failed_step | message_pattern | fallback
    category_basis: str
    evidence: tuple[EvidenceItem, ...]
    recommended_actions: tuple[str, ...]
    test_status: str
    exception_type: str | None
    exception_message: str | None
    file_name: str | None
    line_number: int | None
    source_line: str | None
    failed_step: TimelineStep | None
    last_successful_step: TimelineStep | None
    timeline: tuple[TimelineStep, ...]
    page_url: str | None
    page_url_source: str | None
    timestamp: datetime | None
    browser: str | None
    duration_seconds: float | None
    screenshot_id: int | None
    traceback: str | None


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _text(value: Any) -> str | None:
    """Coerces odd/malformed values to a clean string (or None)."""
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text.strip() or None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_navigation(step: StepInput | None) -> bool:
    return bool(step and (step.action or "").strip().lower() in _NAVIGATION_ACTIONS)


def _step_label(step: StepInput) -> str:
    number = step.step_number if step.step_number is not None else step.sequence_index
    target = _safe_target(step)
    return f"#{number} {step.action}" + (f" {target}" if target else "")


def _safe_target(step: StepInput) -> str | None:
    """Step target for display: URLs get credentials/tokens masked, and
    targets that name a secret field keep the locator but nothing typed."""
    target = _text(step.target)
    if not target:
        return None
    if re.match(r"(?i)^https?://", target):
        target = redact_url(target)
    return truncate(target, 200)


# --------------------------------------------------------------------------
# Step analysis
# --------------------------------------------------------------------------

def _ordered_steps(inp: FailureInput) -> list[StepInput]:
    return sorted(inp.steps, key=lambda s: s.sequence_index)


def find_failed_step(steps: list[StepInput]) -> StepInput | None:
    for step in steps:
        if (step.status or "").upper() in _FAIL_STEP_STATUSES:
            return step
    return None


def find_last_successful_step(steps: list[StepInput], failed: StepInput | None) -> StepInput | None:
    candidates = steps if failed is None else [s for s in steps if s.sequence_index < failed.sequence_index]
    for step in reversed(candidates):
        if (step.status or "").upper() in _PASS_STEP_STATUSES:
            return step
    return None


def find_page_url(steps: list[StepInput], failed: StepInput | None) -> tuple[str | None, str | None]:
    """Last navigation target at or before the failing step. This is the last
    page the script asked the browser to open, which is not guaranteed to be
    the page the browser was on when the failure happened."""
    limit = failed.sequence_index if failed else (steps[-1].sequence_index if steps else None)
    for step in reversed(steps):
        if limit is not None and step.sequence_index > limit:
            continue
        if _is_navigation(step) and _text(step.target):
            return _safe_target(step), "Last navigation (open) step before the failure; not verified as the page shown at failure time."
    return None, None


def _timeline_step(step: StepInput, role: str) -> TimelineStep:
    return TimelineStep(
        role=role,
        sequence_index=step.sequence_index,
        step_number=step.step_number,
        action=step.action,
        target=_safe_target(step),
        status=(step.status or "").upper(),
        duration_seconds=step.duration_seconds,
        occurred_at=step.occurred_at,
        error=truncate(clean_exception_message(_text(step.error)), 300),
        screenshot_ids=tuple(s.id for s in step.screenshots),
    )


def build_timeline(
    steps: list[StepInput], failed: StepInput | None, last_ok: StepInput | None
) -> tuple[TimelineStep, ...]:
    window = steps if failed is None else [s for s in steps if s.sequence_index <= failed.sequence_index]
    window = window[-_TIMELINE_LENGTH:]
    out = []
    for step in window:
        if failed is not None and step.sequence_index == failed.sequence_index:
            role = "failed"
        elif last_ok is not None and step.sequence_index == last_ok.sequence_index:
            role = "last_successful"
        else:
            role = "preceding"
        out.append(_timeline_step(step, role))
    return tuple(out)


def pick_failure_screenshot(
    steps: list[StepInput], failed: StepInput | None, test_screenshots: Iterable[ScreenshotInput]
) -> int | None:
    """Prefers an explicit failure capture, then any capture on the failed
    step, then the last capture recorded for the test."""
    everything = [s for step in steps for s in step.screenshots] + list(test_screenshots)
    for reason in ("TEST_FAILURE", "STEP_FAILURE"):
        for shot in reversed(everything):
            if (shot.capture_reason or "").upper() == reason:
                return shot.id
    if failed is not None and failed.screenshots:
        return failed.screenshots[-1].id
    return everything[-1].id if everything else None


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def classify(
    exception_type: str | None, message: str | None, failed_step: StepInput | None
) -> tuple[str, str, str]:
    """Returns (category, confidence, basis). Specific exception classes win,
    then message patterns, then generic classes, then UNKNOWN."""
    name = rules.short_exception_name(exception_type)
    text = message or ""

    # pytest renders a failed plain `assert` as "E   assert <expression>" (no
    # exception class), which is stored as the exception type/message. pytest
    # emits that form only for failed assertions, so it is reliable evidence.
    # Checked on the raw text: the short name above is cut at the last dot, which
    # would break expressions such as "assert driver.title == 'x'".
    raw_type = (exception_type or "").strip()
    if _PYTEST_ASSERT_RE.match(raw_type) or (not raw_type and _PYTEST_ASSERT_RE.match(text)):
        return rules.ASSERTION_FAILURE, "high", "pytest_assertion"

    category = rules.TYPE_RULES.get(name)
    if category:
        if name == "InvalidArgumentException" and not re.search(r"url", text, re.I):
            return rules.WEBDRIVER_ERROR, "medium", "exception_type"
        if category == rules.TIMEOUT and _is_navigation(failed_step):
            return rules.NAVIGATION_FAILURE, "medium", "exception_type+failed_step"
        return category, "high", "exception_type"

    for category, pattern in rules.MESSAGE_RULES:
        if pattern.search(text):
            return category, "medium", "message_pattern"

    category = rules.GENERIC_TYPE_RULES.get(name)
    if category:
        confidence = "medium" if name in _MEDIUM_GENERIC_TYPES else "low"
        return category, confidence, "exception_type"

    return rules.UNKNOWN, "low", "fallback"


# --------------------------------------------------------------------------
# Evidence and summary
# --------------------------------------------------------------------------

# Selenium embeds the locator as JSON-like text, but does NOT escape quotes
# inside the selector (real message: "selector":"[id="x"]"}), so the selector
# is everything up to the closing quote-brace.
_LOCATOR_RE = re.compile(r'"method"\s*:\s*"(?P<method>[^"]+)"\s*,\s*"selector"\s*:\s*"(?P<selector>.*?)"\s*\}')
_INTERCEPT_RE = re.compile(r"other element would receive the click:\s*(?P<el>.+)", re.I)
_BROWSER_ERROR_RE = re.compile(r"net::ERR_[A-Z_]+")


def _category_details(category: str, message: str) -> list[EvidenceItem]:
    """Facts that can be read straight out of the recorded message."""
    items: list[EvidenceItem] = []
    if category == rules.ELEMENT_NOT_FOUND:
        match = _LOCATOR_RE.search(message)
        if match:
            selector = match["selector"].replace('\\"', '"')  # tolerate an escaped form too
            items.append(EvidenceItem("Locator", f"{match['method']}: {selector}"))
    elif category == rules.CLICK_INTERCEPTED:
        match = _INTERCEPT_RE.search(message)
        if match:
            items.append(EvidenceItem("Element that received the click", truncate(match["el"].strip(), 200) or ""))
    elif category in (rules.NETWORK_FAILURE, rules.NAVIGATION_FAILURE):
        match = _BROWSER_ERROR_RE.search(message)
        if match:
            items.append(EvidenceItem("Browser error code", match.group(0)))
    return items


def _seconds(value: float | None) -> str | None:
    return f"{value:.1f}s" if isinstance(value, (int, float)) else None


def build_evidence(
    inp: FailureInput,
    *,
    category: str,
    message: str | None,
    failed: StepInput | None,
    last_ok: StepInput | None,
    page_url: str | None,
    screenshot_id: int | None,
    steps: list[StepInput],
) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = []

    def add(label: str, value: str | None) -> None:
        if value:
            items.append(EvidenceItem(label, truncate(value, _MAX_EVIDENCE_VALUE) or ""))

    add("Exception", clean_exception_type(_text(inp.exception_type)))
    add("Message", message)
    items.extend(_category_details(category, message or ""))
    if inp.source_file and inp.source_line_number is not None:
        add("Location", f"{inp.source_file}:{inp.source_line_number}")
    add("Source line", redact_source_line(truncate(_text(inp.source_line), 300)))
    if inp.status == "ERROR":
        add("Test status", "ERROR (pytest reports a problem outside the test body, such as setup or teardown)")
    else:
        add("Test status", inp.status)
    if failed:
        detail = _step_label(failed)
        if _seconds(failed.duration_seconds):
            detail += f" (ran for {_seconds(failed.duration_seconds)})"
        add("Failed step", detail)
    elif steps:
        add("Failed step", "No recorded step is marked failed.")
    else:
        add("Steps", "No steps were recorded for this test.")
    add("Last successful step", _step_label(last_ok) if last_ok else None)
    add("Last navigation target", page_url)
    add("Failure screenshot", "Captured" if screenshot_id else "None recorded")
    add("Browser", _text(inp.browser))
    add("Test duration", _seconds(inp.duration_seconds))
    return tuple(items)


_SUMMARY_LEAD = {
    rules.ELEMENT_NOT_FOUND: "Selenium could not find an element on the page",
    rules.TIMEOUT: "A wait expired before its condition was met",
    rules.ELEMENT_NOT_INTERACTABLE: "An element was found but could not be interacted with",
    rules.CLICK_INTERCEPTED: "A click was received by a different element than the target",
    rules.STALE_ELEMENT: "An element reference became invalid after the page changed",
    rules.ASSERTION_FAILURE: "An assertion in the test script failed",
    rules.NAVIGATION_FAILURE: "The browser could not complete a navigation",
    rules.NETWORK_FAILURE: "A network error prevented reaching the target",
    rules.APPLICATION_ERROR: "The error text points to an application-side error",
    rules.API_ERROR: "An API call made by the test returned an error",
    rules.BROWSER_CONFIGURATION_ERROR: "The selected browser could not be started",
    rules.WEBDRIVER_ERROR: "The WebDriver session or browser failed during the test",
    rules.TEST_DATA_ERROR: "The test script hit a data-related Python error",
    rules.ENVIRONMENT_ERROR: "The execution environment raised an error",
    rules.UNKNOWN: "The test failed with an unclassified error",
}


def build_summary(category: str, failed: StepInput | None, evidence: tuple[EvidenceItem, ...]) -> str:
    lead = _SUMMARY_LEAD[category]
    locator = next((e.value for e in evidence if e.label == "Locator"), None)
    if locator:
        lead += f" ({locator})"
    if failed is not None:
        return f"{lead} during step {_step_label(failed)}."
    return f"{lead}."


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def analyze_failure(inp: FailureInput) -> FailureAnalysis | None:
    """Returns the analysis for a failed test, or None when the test did not
    fail. Tolerates missing diagnostics, steps, screenshots and tracebacks
    (legacy records): it then classifies from whatever was recorded."""
    status = (_text(inp.status) or "").upper()
    if status not in FAILING_TEST_STATUSES and not inp.has_failure_record:
        return None

    steps = _ordered_steps(inp)
    failed = find_failed_step(steps)
    last_ok = find_last_successful_step(steps, failed)

    exception_type = clean_exception_type(_text(inp.exception_type))
    raw_message = _text(inp.exception_message) or (_text(failed.error) if failed else None)
    sensitive = has_sensitive_context(_text(inp.source_line), _text(inp.raw_traceback))
    message = clean_exception_message(raw_message, sensitive_context=sensitive)

    category, confidence, basis = classify(exception_type, message, failed)
    page_url, page_url_source = find_page_url(steps, failed)
    screenshot_id = pick_failure_screenshot(steps, failed, inp.test_screenshots)
    evidence = build_evidence(
        inp, category=category, message=message, failed=failed, last_ok=last_ok,
        page_url=page_url, screenshot_id=screenshot_id, steps=steps,
    )
    timestamp = (failed.occurred_at if failed else None) or inp.completed_at or inp.run_completed_at

    return FailureAnalysis(
        failure_category=category,
        failure_title=rules.TITLES[category],
        summary=build_summary(category, failed, evidence),
        likely_cause=rules.LIKELY_CAUSES[category],
        confidence=confidence,
        confidence_note=CONFIDENCE_NOTE,
        category_basis=basis,
        evidence=evidence,
        recommended_actions=rules.RECOMMENDED_ACTIONS[category],
        test_status=status or "FAILED",
        exception_type=exception_type,
        exception_message=message,
        file_name=_text(inp.source_file) or _text(inp.file_name),
        line_number=_int(inp.source_line_number),
        source_line=redact_source_line(truncate(_text(inp.source_line), 300)),
        failed_step=_timeline_step(failed, "failed") if failed else None,
        last_successful_step=_timeline_step(last_ok, "last_successful") if last_ok else None,
        timeline=build_timeline(steps, failed, last_ok),
        page_url=page_url,
        page_url_source=page_url_source,
        timestamp=timestamp,
        browser=_text(inp.browser),
        duration_seconds=inp.duration_seconds,
        screenshot_id=screenshot_id,
        traceback=sanitize_traceback(_text(inp.raw_traceback), sensitive_context=sensitive),
    )


def safe_analyze(inp: FailureInput) -> FailureAnalysis | None:
    """analyze_failure() that can never break the caller.

    The analysis is an enhancement layered on the existing results API and
    reports; a defect or unexpected data shape here must not turn a working
    results page or report download into an error. The full exception is
    logged, so the failure is never hidden."""
    try:
        return analyze_failure(inp)
    except Exception:
        logger.exception("Failure analysis failed for test_result %s", getattr(inp, "test_id", "?"))
        return None


# --------------------------------------------------------------------------
# Adapter: duck-typed records (SQLAlchemy rows or report dataclasses)
# --------------------------------------------------------------------------

def _screenshot_input(shot: Any) -> ScreenshotInput:
    return ScreenshotInput(
        id=shot.id,
        capture_reason=_text(getattr(shot, "capture_reason", None)),
        file_name=_text(getattr(shot, "file_name", None)),
    )


def input_from_records(
    test: Any,
    steps: Iterable[Any],
    screenshots_by_step: dict[Any, list[Any]],
    test_screenshots: Iterable[Any],
    failure: Any | None,
    browser: str | None,
    run_completed_at: datetime | None,
    secrets: tuple[str, ...] = (),
) -> FailureInput:
    """Builds a FailureInput from stored records. ``screenshots_by_step``
    maps a step's id to its screenshots; ``failure`` is the stored
    FailureDiagnostic-like record (or None). ``secrets`` are literals known to be
    secret (see failure_sanitizer.collect_known_secrets); every recorded text is
    masked here, so the analysis never sees them."""

    def mask(value: Any) -> str | None:
        return mask_known_secrets(_text(value), secrets)

    step_inputs = tuple(
        StepInput(
            sequence_index=_int(getattr(step, "sequence_index", 0)) or 0,
            step_number=_int(getattr(step, "step_number", None)),
            action=_text(getattr(step, "action", None)) or "",
            target=sanitize_step_target(_text(getattr(step, "target", None)), secrets),
            status=_text(getattr(step, "status", None)) or "",
            duration_seconds=getattr(step, "duration_seconds", None),
            error=sanitize_step_error(_text(getattr(step, "error", None)), secrets),
            occurred_at=getattr(step, "occurred_at", None),
            screenshots=tuple(_screenshot_input(s) for s in screenshots_by_step.get(step.id, [])),
        )
        for step in steps
    )
    return FailureInput(
        test_id=test.id,
        nodeid=_text(getattr(test, "nodeid", None)) or "",
        status=_text(getattr(test, "status", None)) or "",
        has_failure_record=failure is not None,
        file_name=_text(getattr(test, "file_name", None)),
        method_name=_text(getattr(test, "method_name", None)),
        duration_seconds=getattr(test, "duration_seconds", None),
        completed_at=getattr(test, "completed_at", None),
        run_completed_at=run_completed_at,
        browser=_text(browser),
        exception_type=mask(getattr(failure, "exception_type", None)),
        exception_message=mask(getattr(failure, "exception_message", None)),
        source_file=_text(getattr(failure, "source_file", None)),
        source_line_number=_int(getattr(failure, "source_line_number", None)),
        source_line=mask(getattr(failure, "source_line", None)),
        raw_traceback=mask(getattr(failure, "raw_traceback", None)),
        steps=step_inputs,
        test_screenshots=tuple(_screenshot_input(s) for s in test_screenshots),
    )
