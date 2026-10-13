"""
Structured JSON execution report (schema version 1.0).

Pure module: no database, no FastAPI, no Selenium. It turns the raw facts a
Run's pytest subprocess already produces — the compatibility layer's
``steps.json`` events, its ``steps_meta.json`` (the steps each test *defines*),
the parsed JUnit testcases and the screenshots on disk — into one validated
``ExecutionReport``. Nothing is invented: every event, status, timestamp and
duration comes from a recorded event; steps that were defined but never ran
are reported as SKIPPED with the real reason.

Status vocabulary: PASSED | FAILED | SKIPPED.

Step status (a *defined* step, e.g. Selenium IDE step 4):
  FAILED   any recorded event for the step failed
  PASSED   at least one event ran and none failed
  SKIPPED  no event ran: either recorded as skipped because a prerequisite
           failed, or never reached (reason recorded)
Test status:
  FAILED   pytest reported failed/error, or any step FAILED
  SKIPPED  pytest reported skipped
  PASSED   otherwise (skipped steps are still counted and shown)
Run (overall) status:
  FAILED   any test FAILED, no tests were reported, or a run-level error exists
  SKIPPED  every test SKIPPED
  PASSED   otherwise
"""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

REPORT_VERSION = "1.0"
Status = Literal["PASSED", "FAILED", "SKIPPED"]

_MAX_MESSAGE_CHARS = 1000
_SAFE_SCREENSHOT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.png$")
_MARKER_ACTIONS = ("TEST_FAILURE", "TEST_SETUP_FAILURE")

REDACTED = "***REDACTED***"
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|cookie|session[_-]?id)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
]


def redact(text: str | None) -> str | None:
    """Masks credential-looking fragments (``password=...``, ``Bearer ...``)."""
    if text is None:
        return None
    text = _SECRET_PATTERNS[1].sub(f"Bearer {REDACTED}", text)
    return _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}" if m.group(3) != REDACTED and not m.group(3).startswith("Bearer") else m.group(0), text)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def iso_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    try:
        return _fmt(datetime.fromtimestamp(float(epoch), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def iso_from_datetime(value: datetime | None) -> str | None:
    """Naive datetimes are treated as UTC (the DB stores utcnow())."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return _fmt(value.astimezone(timezone.utc))


def _fmt(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _check_iso(value: str | None) -> str | None:
    if value is None:
        return value
    datetime.fromisoformat(value.replace("Z", "+00:00"))  # raises ValueError -> validation error
    return value


Timestamp = Annotated[str, AfterValidator(_check_iso)]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorInfo(_Model):
    type: str
    message: str
    category: str


class PageError(ErrorInfo):
    eventNumber: int | None = None
    stepNumber: int | None = None
    testId: str | None = None
    timestamp: Timestamp | None = None
    url: str | None = None
    screenshot: str | None = None


class Event(_Model):
    eventNumber: int
    testId: str | None = None
    stepNumber: int | None = None
    stepName: str
    command: str
    locatorType: str | None = None
    locatorValue: str | None = None
    value: str | None = None
    status: Status
    attempted: bool
    skipReason: str | None = None
    startTime: Timestamp | None = None
    completionTime: Timestamp | None = None
    executionTimeMs: int | None = Field(default=None, ge=0)
    url: str | None = None
    pageTitle: str | None = None
    error: ErrorInfo | None = None
    screenshot: str | None = None


class Page(_Model):
    pageNumber: int
    pageId: str
    url: str | None = None
    pageTitle: str | None = None
    screenshot: str | None = None
    events: list[Event]
    errors: list[PageError]


class Locator(_Model):
    strategy: str
    value: str


class Step(_Model):
    """Final state of one *defined* step (or, for tests without a step
    definition, one recorded action)."""

    testId: str
    stepNumber: int
    stepName: str
    command: str
    locatorType: str | None = None
    locatorValue: str | None = None
    value: str | None = None
    status: Status
    attempted: bool
    skipReason: str | None = None
    startTime: Timestamp | None = None
    completionTime: Timestamp | None = None
    executionTimeMs: int | None = Field(default=None, ge=0)
    error: ErrorInfo | None = None
    screenshot: str | None = None
    # Additive, optional (absent in stored 1.0 reports).
    stepId: str | None = None
    name: str | None = None  # same as stepName
    action: str | None = None  # same as command
    locator: Locator | None = None  # same as locatorType/locatorValue
    expected: str | None = None  # defined value of assert*/verify* steps only
    attempt: int | None = Field(default=None, ge=1)  # 1 when attempted


class Summary(_Model):
    totalSteps: int = Field(ge=0)
    attemptedSteps: int = Field(ge=0)
    executedSteps: int = Field(ge=0)  # alias of attemptedSteps
    passedSteps: int = Field(ge=0)
    failedSteps: int = Field(ge=0)
    skippedSteps: int = Field(ge=0)
    totalErrors: int = Field(ge=0)
    totalPages: int = Field(ge=0)
    # Additive (optional so stored 1.0 reports without them stay valid).
    # notRunSteps: defined steps never attempted because the test stopped or
    # never reached them (a subset of skippedSteps; dependency skips are not
    # counted here). failureCount = failedSteps.
    notRunSteps: int | None = Field(default=None, ge=0)
    failureCount: int | None = Field(default=None, ge=0)
    continuationEnabled: bool | None = None  # null when the runner did not say
    warningCount: int | None = None  # warnings are not tracked -> null


class TestReport(_Model):
    __test__ = False  # not a pytest test class

    testId: str
    testName: str
    status: Status
    startTime: Timestamp | None = None
    completionTime: Timestamp | None = None
    executionTimeMs: int | None = Field(default=None, ge=0)
    actualOutcome: str
    summary: Summary


class ReportMetadata(_Model):
    projectId: int | None = None
    projectName: str | None = None
    runId: int | None = None
    runName: str | None = None  # runs have no name
    testFile: str | None = None
    executionEngine: str | None = None
    environment: str | None = None
    startedAt: Timestamp | None = None
    finishedAt: Timestamp | None = None
    durationMs: int | None = Field(default=None, ge=0)
    triggeredBy: str | None = None  # not recorded by this tool


class EnvironmentInfo(_Model):
    browser: str | None = None
    browserVersion: str | None = None
    operatingSystem: str | None = None
    baseUrl: str | None = None


class LogEntry(_Model):
    timestamp: Timestamp | None = None
    level: Literal["INFO", "WARNING", "ERROR"]
    message: str
    testId: str | None = None
    stepNumber: int | None = None


class Attempt(_Model):
    attemptNumber: int = Field(ge=1)
    status: Status
    startTime: Timestamp | None = None
    completionTime: Timestamp | None = None
    executionTimeMs: int | None = Field(default=None, ge=0)


class Artifacts(_Model):
    jsonReport: str
    screenshotsDirectory: str
    screenshots: list[str]
    # Not produced by this tool; always null rather than claimed.
    executionLog: str | None = None
    video: str | None = None
    trace: str | None = None


class ExecutionReport(_Model):
    reportVersion: str
    executionId: str
    testcaseId: str | None = None
    testcaseName: str | None = None
    browser: str | None = None
    status: Status
    startTime: Timestamp | None = None
    completionTime: Timestamp
    executionTimeMs: int | None = Field(default=None, ge=0)
    expectedOutcome: str | None = None
    actualOutcome: str
    summary: Summary
    tests: list[TestReport]
    steps: list[Step]
    pages: list[Page]
    errors: list[PageError]
    attempts: list[Attempt]
    artifacts: Artifacts
    # Additive, optional (absent in stored 1.0 reports).
    reportType: str | None = None
    metadata: ReportMetadata | None = None
    environment: EnvironmentInfo | None = None
    executionLog: list[LogEntry] | None = None

    @model_validator(mode="after")
    def _summary_matches_records(self) -> "ExecutionReport":
        expected = summarize(self.steps, self.pages, self.errors)
        # continuationEnabled/warningCount are not derivable from the records;
        # optional counts absent from older stored reports are not compared.
        skip = {"continuationEnabled", "warningCount"}
        actual = self.summary.model_dump(exclude=skip)
        wanted = expected.model_dump(exclude=skip)
        for key, value in actual.items():
            if value is None and key in ("notRunSteps", "failureCount"):
                continue
            if value != wanted[key]:
                raise ValueError(f"summary {actual} does not match records {wanted}")
        if self.summary.executedSteps != self.summary.attemptedSteps:
            raise ValueError("executedSteps must equal attemptedSteps")
        return self


def summarize(steps: list[Step], pages: list[Page], errors: list[PageError]) -> Summary:
    attempted = sum(1 for s in steps if s.attempted)
    return Summary(
        totalSteps=len(steps),
        attemptedSteps=attempted,
        executedSteps=attempted,
        passedSteps=sum(1 for s in steps if s.status == "PASSED"),
        failedSteps=sum(1 for s in steps if s.status == "FAILED"),
        skippedSteps=sum(1 for s in steps if s.status == "SKIPPED"),
        totalErrors=len(errors),
        totalPages=len(pages),
        notRunSteps=sum(
            1 for s in steps
            if s.status == "SKIPPED" and not s.attempted and (s.skipReason or "").startswith("Not executed")
        ),
        failureCount=sum(1 for s in steps if s.status == "FAILED"),
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
_CATEGORY_BY_TYPE = {
    "NoSuchElementException": "LOCATOR_ERROR",
    "InvalidSelectorException": "INVALID_SELECTOR",
    "TimeoutException": "TIMEOUT",
    "ElementClickInterceptedException": "CLICK_INTERCEPTED",
    "ElementNotInteractableException": "ELEMENT_NOT_INTERACTABLE",
    "ElementNotVisibleException": "ELEMENT_NOT_INTERACTABLE",
    "StaleElementReferenceException": "STALE_ELEMENT",
    "NoSuchFrameException": "FRAME_ERROR",
    "NoSuchWindowException": "BROWSER_CRASH",
    "InvalidSessionIdException": "BROWSER_CRASH",
    "SessionNotCreatedException": "BROWSER_INIT_ERROR",
    "AssertionError": "ASSERTION_FAILURE",
    "Failed": "ASSERTION_FAILURE",
}


def classify_error(error_type: str | None, message: str | None, *, setup: bool = False) -> str:
    name = (error_type or "").split(".")[-1]
    text = f"{name} {message or ''}".lower()
    if setup:
        return "BROWSER_INIT_ERROR"
    if name in _CATEGORY_BY_TYPE:
        return _CATEGORY_BY_TYPE[name]
    if any(m in text for m in ("invalid session id", "disconnected", "not reachable", "target window already closed", "max retries exceeded", "connection refused")):
        return "BROWSER_CRASH"
    if "session not created" in text or "cannot find chrome binary" in text or "unable to obtain driver" in text:
        return "BROWSER_INIT_ERROR"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    return "UNEXPECTED_ERROR"


def parse_error(raw: str | None, *, setup: bool = False) -> ErrorInfo | None:
    """Turns the runner's ``repr(exc)`` / ``Type: message`` strings into a
    clean, redacted, length-bounded ErrorInfo."""
    if not raw:
        return None
    raw = raw.strip()
    error_type, message = None, raw

    match = re.match(r"^([A-Za-z_][\w.]*)\((.*)\)$", raw, re.S)
    if match:
        error_type, inner = match.group(1), match.group(2)
        try:
            literal = ast.literal_eval(inner)
            message = literal if isinstance(literal, str) else inner
        except (ValueError, SyntaxError):
            message = inner
    else:
        match = re.match(r"^([A-Za-z_][\w.]*):\s*(.*)$", raw, re.S)
        if match:
            error_type, message = match.group(1), match.group(2)

    error_type = (error_type or "Error").split(".")[-1]
    message = re.sub(r"^Message:\s*", "", message.strip())
    message = re.split(r"\n\s*Stacktrace:|\bStacktrace:\n", message)[0].strip()
    message = (redact(message) or "")[:_MAX_MESSAGE_CHARS] or error_type
    return ErrorInfo(type=error_type, message=message, category=classify_error(error_type, message, setup=setup))


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
_IDE_LOCATOR_RE = re.compile(r"^(id|css|xpath|name|linkText|partialLinkText|class|tag)=(.*)$", re.S)


def _locator(event: dict) -> tuple[str | None, str | None]:
    ide = _IDE_LOCATOR_RE.match(event.get("target") or "")
    if ide:
        return ide.group(1), redact(ide.group(2))
    if event.get("locator_type"):
        return event.get("locator_type"), redact(event.get("locator_value"))
    return None, None


def _ms(seconds) -> int | None:
    try:
        return max(0, int(round(float(seconds) * 1000)))
    except (TypeError, ValueError):
        return None


def _screenshot_path(name, available: set[str] | None) -> str | None:
    """A screenshot is only referenced if it is a plain, safe file name and
    (when the directory listing is known) actually exists."""
    if not name or not isinstance(name, str) or not _SAFE_SCREENSHOT_RE.match(name):
        return None
    if available is not None and name not in available:
        return None
    return f"screenshots/{name}"


def _step_name(event: dict) -> str:
    action = event.get("action") or "action"
    target = event.get("target")
    return f"{action} {target}".strip() if target else action


def _event_status(event: dict) -> str:
    return {"PASS": "PASSED", "FAIL": "FAILED", "SKIP": "SKIPPED"}.get(event.get("status"), "FAILED")


def _event_value(event: dict, locator_value: str | None) -> str | None:
    value = event.get("value")
    if not value:
        return None
    if _looks_sensitive(f"{event.get('target') or ''} {locator_value or ''}", event.get("action") or ""):
        return REDACTED
    return redact(value)


def _make_event(number: int, test_id: str, event: dict, available) -> Event:
    status = _event_status(event)
    locator_type, locator_value = _locator(event)
    skipped = status == "SKIPPED"
    return Event(
        eventNumber=number,
        testId=test_id,
        stepNumber=event.get("step"),
        stepName=_step_name(event),
        command=event.get("action") or "action",
        locatorType=locator_type,
        locatorValue=locator_value,
        value=_event_value(event, locator_value),
        status=status,
        attempted=not skipped,
        skipReason=redact(event.get("skip_reason")) if skipped else None,
        startTime=None if skipped else iso_from_epoch(event.get("started_at")),
        completionTime=None if skipped else iso_from_epoch(event.get("timestamp")),
        executionTimeMs=None if skipped else _ms(event.get("duration")),
        url=event.get("url"),
        pageTitle=redact((event.get("title") or "")[:300]) or None,
        error=parse_error(event.get("error")) if status == "FAILED" else None,
        screenshot=_screenshot_path(event.get("screenshot"), available),
    )


def _step_from_events(test_id: str, number: int, defined: dict | None, evs: list[Event], raw: list[dict],
                      absent_reason: str) -> Step:
    first = raw[0] if raw else {}
    failed = [e for e in evs if e.status == "FAILED"]
    ran = [e for e in evs if e.status == "PASSED"]
    skipped = [e for e in evs if e.status == "SKIPPED"]

    if failed:
        status, lead = "FAILED", failed[0]
    elif ran:
        status, lead = "PASSED", ran[-1]
    else:
        status, lead = "SKIPPED", (skipped[0] if skipped else None)

    attempted = bool(failed or ran)
    timed = [e for e in evs if e.attempted]
    starts = sorted(e.startTime for e in timed if e.startTime)
    ends = sorted(e.completionTime for e in timed if e.completionTime)
    total_ms = sum(e.executionTimeMs or 0 for e in timed) if timed else None
    shots = [e.screenshot for e in (failed or ran) if e.screenshot]

    if defined:
        command, target, value = defined["action"], defined.get("target") or "", defined.get("value") or ""
        name = f"{command} {target}".strip()
        locator = _IDE_LOCATOR_RE.match(target)
        locator_type, locator_value = (locator.group(1), redact(locator.group(2))) if locator else (None, None)
        if locator_type is None and lead is not None:
            locator_type, locator_value = lead.locatorType, lead.locatorValue
        redacted_value = None
        if lead is not None and lead.value:
            redacted_value = lead.value
        elif value:
            redacted_value = REDACTED if _looks_sensitive(target, command) else redact(value)
    else:
        command = lead.command if lead else (first.get("action") or "action")
        name = lead.stepName if lead else _step_name(first)
        locator_type, locator_value = (lead.locatorType, lead.locatorValue) if lead else (None, None)
        redacted_value = lead.value if lead else None

    skip_reason = None
    if status == "SKIPPED":
        skip_reason = (skipped[0].skipReason if skipped else None) or absent_reason

    return Step(
        testId=test_id,
        stepNumber=number,
        stepName=name,
        command=command,
        locatorType=locator_type,
        locatorValue=locator_value,
        value=redacted_value,
        status=status,
        attempted=attempted,
        skipReason=skip_reason,
        startTime=starts[0] if starts else None,
        completionTime=ends[-1] if ends else None,
        executionTimeMs=total_ms,
        error=failed[0].error if failed else None,
        screenshot=shots[-1] if shots else None,
        stepId=f"{test_id}#step-{number}",
        name=name,
        action=command,
        locator=Locator(strategy=locator_type, value=locator_value) if locator_type and locator_value is not None else None,
        expected=redacted_value if defined and re.match(r"(?i)^(assert|verify)", command) and redacted_value else None,
        attempt=1 if attempted else None,
    )


def _looks_sensitive(target: str, command: str) -> bool:
    return command in ("type", "sendKeys", "send_keys") and bool(re.search(r"(?i)pass(word|wd)?|pwd|secret|token|otp|cvv", target))


def _group_pages(events: list[Event], page_offset: int) -> list[Page]:
    """A new page starts whenever the recorded URL changes. Events without a
    URL (e.g. skipped steps) stay on the current page."""
    pages: list[Page] = []
    current: Page | None = None

    def new_page(url):
        number = page_offset + len(pages) + 1
        page = Page(pageNumber=number, pageId=f"PAGE-{number:03d}", url=url, pageTitle=None,
                    screenshot=None, events=[], errors=[])
        pages.append(page)
        return page

    for event in events:
        if current is None:
            current = new_page(event.url)
        elif event.url and current.url is None:
            current.url = event.url
        elif event.url and event.url != current.url:
            current = new_page(event.url)
        current.events.append(event)
        if event.pageTitle:
            current.pageTitle = event.pageTitle
        if event.screenshot:
            current.screenshot = event.screenshot
        if event.error:
            current.errors.append(PageError(
                eventNumber=event.eventNumber, stepNumber=event.stepNumber, testId=event.testId,
                timestamp=event.completionTime, url=event.url, screenshot=event.screenshot,
                **event.error.model_dump(),
            ))
    return pages


def build_execution_report(
    *,
    run_id: int,
    test_results: list[dict],
    events: list[dict],
    meta: dict | None = None,
    run_started_at: datetime | None = None,
    generated_at: datetime | None = None,
    browser: str | None = None,
    exit_code: int | None = None,
    available_screenshots: set[str] | None = None,
    run_level_errors: list[str] | None = None,
    project_id: int | None = None,
    project_name: str | None = None,
) -> ExecutionReport:
    """``test_results`` items: nodeid, method_name, status (PASSED/FAILED/ERROR/
    SKIPPED), duration. ``events`` is the runner's steps.json list; ``meta`` its
    steps_meta.json ({nodeid: {defined_steps: [...]}})."""

    meta = meta or {}
    generated = generated_at or datetime.now(timezone.utc)
    run_level_errors = list(run_level_errors or [])

    by_node: dict[str, list[dict]] = {}
    for ev in events:
        by_node.setdefault(ev.get("nodeid") or "", []).append(ev)

    all_steps: list[Step] = []
    all_pages: list[Page] = []
    all_errors: list[PageError] = []
    tests: list[TestReport] = []
    event_counter = 0
    node_ids = [t["nodeid"] for t in test_results]

    for tr in test_results:
        node = tr["nodeid"]
        raw_events = by_node.get(node, [])
        setup_failure = next((e for e in raw_events if e.get("action") == "TEST_SETUP_FAILURE"), None)
        test_failure = next((e for e in raw_events if e.get("action") == "TEST_FAILURE"), None)
        step_events = [e for e in raw_events if e.get("action") not in _MARKER_ACTIONS]

        built: list[Event] = []
        for ev in step_events:
            event_counter += 1
            built.append(_make_event(event_counter, node, ev, available_screenshots))

        # Test-level errors that are not attributable to a step.
        test_errors: list[PageError] = []
        marker = setup_failure or test_failure
        if marker:
            info = parse_error(marker.get("error"), setup=bool(setup_failure))
            if info:
                test_errors.append(PageError(
                    stepNumber=None, testId=node, timestamp=iso_from_epoch(marker.get("timestamp")),
                    url=marker.get("url"), screenshot=_screenshot_path(marker.get("screenshot"), available_screenshots),
                    **info.model_dump(),
                ))

        # ---- steps ---------------------------------------------------
        defined = (meta.get(node) or {}).get("defined_steps") or []
        if setup_failure:
            absent = f"Not executed: test setup failed before any step ran ({test_errors[0].message if test_errors else 'setup error'})"
        elif test_failure and test_errors:
            absent = f"Not executed: test stopped after an unexpected error ({test_errors[0].message})"
        else:
            absent = "Not executed: the test finished before reaching this step"

        steps: list[Step] = []
        if defined:
            for d in defined:
                number = d["number"]
                idx = [i for i, e in enumerate(step_events) if e.get("step") == number]
                steps.append(_step_from_events(node, number, d, [built[i] for i in idx], [step_events[i] for i in idx], absent))
            # Recorded events that map to no defined step stay visible as steps.
            known = {d["number"] for d in defined}
            extra = [i for i, e in enumerate(step_events) if e.get("step") not in known]
            next_number = max(known) + 1
            for i in extra:
                steps.append(_step_from_events(node, next_number, None, [built[i]], [step_events[i]], absent))
                next_number += 1
        else:
            for position, (ev, evt) in enumerate(zip(step_events, built), start=1):
                steps.append(_step_from_events(node, position, None, [evt], [ev], absent))

        pages = _group_pages(built, len(all_pages))
        page_errors = [e for p in pages for e in p.errors]
        errors = page_errors + test_errors

        # ---- test status & summary ---------------------------------------
        junit = tr.get("status")
        any_failed = any(s.status == "FAILED" for s in steps)
        if junit in ("FAILED", "ERROR") or any_failed:
            status = "FAILED"
        elif junit == "SKIPPED":
            status = "SKIPPED"
        else:
            status = "PASSED"

        starts = sorted(e.startTime for e in built if e.startTime)
        ends = sorted(e.completionTime for e in built if e.completionTime)
        duration_ms = _ms(tr.get("duration"))
        if status == "FAILED":
            failed_steps = [s for s in steps if s.status == "FAILED"]
            actual = (
                f"{len(failed_steps)} step(s) failed. First failure: step {failed_steps[0].stepNumber} "
                f"({failed_steps[0].stepName}): {failed_steps[0].error.message}"
                if failed_steps and failed_steps[0].error
                else (test_errors[0].message if test_errors else "The test failed.")
            )
        elif status == "SKIPPED":
            actual = "The test was skipped."
        else:
            actual = f"All {sum(1 for s in steps if s.attempted)} attempted step(s) passed." if steps else "The test passed."

        tests.append(TestReport(
            testId=node, testName=tr.get("method_name") or node, status=status,
            startTime=starts[0] if starts else None, completionTime=ends[-1] if ends else None,
            executionTimeMs=duration_ms, actualOutcome=actual,
            summary=summarize(steps, pages, errors),
        ))
        all_steps.extend(steps)
        all_pages.extend(pages)
        all_errors.extend(errors)

    # Events recorded for tests pytest never reported on stay out of the
    # report (never fabricating a test), but are surfaced as a run-level error.
    orphan = sorted({n for n in by_node if n and n not in node_ids and any(e.get("action") not in _MARKER_ACTIONS for e in by_node[n])})
    for n in orphan:
        run_level_errors.append(f"Recorded step events for '{n}' but pytest reported no result for it.")
    if not test_results:
        run_level_errors.append("No test results were captured for this run.")

    run_errors = [
        PageError(type="RunError", message=redact(m)[:_MAX_MESSAGE_CHARS], category="RUN_ERROR", timestamp=iso_from_datetime(generated))
        for m in run_level_errors
    ]
    all_errors.extend(run_errors)

    # ---- overall -----------------------------------------------------------
    if run_errors or any(t.status == "FAILED" for t in tests) or not tests:
        overall = "FAILED"
    elif all(t.status == "SKIPPED" for t in tests):
        overall = "SKIPPED"
    else:
        overall = "PASSED"

    summary = summarize(all_steps, all_pages, all_errors)
    continuation = (meta.get("__run__") or {}).get("continuationEnabled")
    summary.continuationEnabled = continuation if isinstance(continuation, bool) else None
    for t in tests:
        t.summary.continuationEnabled = summary.continuationEnabled
    starts = sorted(t.startTime for t in tests if t.startTime)
    start_iso = iso_from_datetime(run_started_at) or (starts[0] if starts else None)
    completion_iso = iso_from_datetime(generated)
    duration = None
    if start_iso and completion_iso:
        duration = max(0, int((_parse(completion_iso) - _parse(start_iso)).total_seconds() * 1000))

    if overall == "FAILED":
        first_bad = next((t for t in tests if t.status == "FAILED"), None)
        actual_overall = first_bad.actualOutcome if first_bad else (run_errors[0].message if run_errors else "The run failed.")
    elif overall == "SKIPPED":
        actual_overall = "All tests were skipped."
    else:
        actual_overall = f"All {summary.attemptedSteps} attempted step(s) passed." if summary.attemptedSteps else "The run passed."

    single = tests[0] if len(tests) == 1 else None
    screenshots = sorted({s.screenshot.split("/", 1)[1] for s in all_steps if s.screenshot} |
                         {e.screenshot.split("/", 1)[1] for p in all_pages for e in p.events if e.screenshot} |
                         {e.screenshot.split("/", 1)[1] for e in all_errors if e.screenshot})

    log: list[LogEntry] = []
    for page in all_pages:
        for e in page.events:
            level = "ERROR" if e.status == "FAILED" else "WARNING" if e.status == "SKIPPED" else "INFO"
            detail = e.error.message if e.error else e.skipReason
            log.append(LogEntry(
                timestamp=e.completionTime or e.startTime, level=level, testId=e.testId, stepNumber=e.stepNumber,
                message=f"{e.stepName}: {e.status}" + (f" - {detail}" if detail else ""),
            ))
    test_files = sorted({(t.get("file_name") or "") for t in test_results if t.get("file_name")})

    return ExecutionReport(
        reportType="automation_execution",
        metadata=ReportMetadata(
            projectId=project_id, projectName=project_name, runId=run_id,
            testFile=", ".join(test_files) or None, executionEngine="selenium",
            startedAt=start_iso, finishedAt=completion_iso, durationMs=duration,
        ),
        environment=EnvironmentInfo(browser=browser),
        executionLog=log,
        reportVersion=REPORT_VERSION,
        executionId=f"RUN-{run_id}",
        testcaseId=single.testId if single else None,
        testcaseName=single.testName if single else (f"{len(tests)} tests" if tests else None),
        browser=browser,
        status=overall,
        startTime=start_iso,
        completionTime=completion_iso,
        executionTimeMs=duration,
        expectedOutcome=None,  # tests do not declare an expected outcome
        actualOutcome=actual_overall,
        summary=summary,
        tests=tests,
        steps=all_steps,
        pages=all_pages,
        errors=all_errors,
        attempts=[Attempt(attemptNumber=1, status=overall, startTime=start_iso, completionTime=completion_iso, executionTimeMs=duration)],
        artifacts=Artifacts(jsonReport="report/json", screenshotsDirectory="screenshots", screenshots=screenshots),
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def report_to_json(report: ExecutionReport, *, indent: int | None = 2) -> str:
    return report.model_dump_json(indent=indent)


def report_from_json(text: str) -> ExecutionReport:
    """Parses AND validates a stored report (raises pydantic.ValidationError)."""
    return ExecutionReport.model_validate_json(text)


def is_safe_screenshot_name(name: str) -> bool:
    return bool(_SAFE_SCREENSHOT_RE.match(name or ""))
