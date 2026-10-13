"""Unit tests for services/execution_report.py (pure: no DB, no browser)."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.execution_report import (
    ExecutionReport,
    Summary,
    build_execution_report,
    classify_error,
    is_safe_screenshot_name,
    parse_error,
    redact,
    report_from_json,
    report_to_json,
)

NODE = "test_login.py::TestLogin::test_login"
T0 = 1_760_000_000.0


def ev(step, action, status="PASS", *, start=0.0, dur=0.5, url="https://x.test/", error="", shot="", **extra):
    return {
        "nodeid": NODE, "step": step, "action": action, "target": extra.pop("target", ""), "value": extra.pop("value", ""),
        "status": status, "duration": dur, "error": error, "screenshot": shot, "timestamp": T0 + start + dur,
        "started_at": T0 + start, "url": url, "title": "T", "attempted": status != "SKIP",
        "skip_reason": extra.pop("skip_reason", None), **extra,
    }


def tr(status="PASSED", node=NODE):
    return {"nodeid": node, "file_name": "test_login.py", "class_name": "TestLogin",
            "method_name": node.split("::")[-1], "status": status, "duration": 2.0}


def meta(n):
    return {NODE: {"defined_steps": [{"number": i, "action": "click", "target": f"id=b{i}", "value": ""} for i in range(1, n + 1)]}}


def build(events, status="PASSED", n=None, run_id=1, **kw):
    return build_execution_report(
        run_id=run_id, test_results=[tr(status)], events=events,
        meta=meta(n) if n else {}, generated_at=datetime(2026, 10, 12, 9, 0, 5, tzinfo=timezone.utc), **kw,
    )


def test_all_steps_pass_report():
    report = build([ev(1, "click", start=0), ev(2, "click", start=1)], n=2)
    assert report.status == "PASSED" and report.reportVersion == "1.0"
    assert report.executionId == "RUN-1"
    assert (report.summary.totalSteps, report.summary.passedSteps, report.summary.failedSteps) == (2, 2, 0)
    assert report.steps[0].startTime == "2025-10-09T08:53:20.000Z"
    assert report.steps[0].executionTimeMs == 500


def test_one_failed_step_makes_the_report_failed_even_if_pytest_said_passed():
    report = build(
        [ev(1, "click"), ev(2, "click", "FAIL", error="NoSuchElementException('Message: no such element: x\\nStacktrace:\\n#0 abc')")],
        status="PASSED", n=2,
    )
    assert report.status == "FAILED" and report.tests[0].status == "FAILED"
    error = report.steps[1].error
    assert (error.type, error.category, error.message) == ("NoSuchElementException", "LOCATOR_ERROR", "no such element: x")
    assert report.errors[0].stepNumber == 2


def test_summary_counts_always_match_records():
    report = build(
        [ev(1, "click"), ev(2, "click", "FAIL", error="TimeoutException('t')"),
         ev(3, "click", "SKIP", skip_reason="depends on step 2")], status="FAILED", n=4,
    )
    s = report.summary
    assert (s.totalSteps, s.attemptedSteps, s.passedSteps, s.failedSteps, s.skippedSteps) == (4, 2, 1, 1, 2)
    assert s.totalErrors == len(report.errors) == 1
    assert s.totalPages == len(report.pages)
    assert s.executedSteps == s.attemptedSteps
    assert report.steps[3].skipReason.startswith("Not executed")  # step 4 had no events at all


def test_tampered_summary_fails_validation():
    data = json.loads(report_to_json(build([ev(1, "click")], n=1)))
    data["summary"]["passedSteps"] = 5
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(data)


@pytest.mark.parametrize("field,value", [("status", "PASS"), ("completionTime", "yesterday"), ("executionTimeMs", -5)])
def test_schema_rejects_invalid_values(field, value):
    data = json.loads(report_to_json(build([ev(1, "click")], n=1)))
    data[field] = value
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(data)


def test_json_roundtrip_is_serializable_and_valid():
    report = build([ev(1, "click"), ev(2, "click", "FAIL", error="AssertionError('x')")], n=2, browser="chrome")
    text = report_to_json(report)
    assert json.loads(text)["summary"]["failedSteps"] == 1          # plain JSON
    assert report_from_json(text) == report                          # validated round trip


def test_execution_ids_are_unique_per_run_and_reports_are_independent():
    a = build([ev(1, "click")], n=1, run_id=11)
    b = build([ev(1, "click", "FAIL", error="TimeoutException('x')")], status="FAILED", n=1, run_id=12)
    assert a.executionId != b.executionId
    assert a.status == "PASSED" and b.status == "FAILED"            # one never overwrites the other


def test_attempt_information_is_present_and_extensible():
    report = build([ev(1, "click")], n=1)
    assert [a.attemptNumber for a in report.attempts] == [1]
    assert report.attempts[0].status == report.status
    # Retry creates a brand-new Run in this tool, so each report holds exactly its own attempt.


def test_multiple_pages_are_split_on_url_change():
    report = build([ev(1, "click", url="https://a/"), ev(2, "click", url="https://b/"), ev(3, "click", url="https://b/")], n=3)
    assert [p.url for p in report.pages] == ["https://a/", "https://b/"]
    assert [len(p.events) for p in report.pages] == [1, 2]


def test_no_tests_is_a_failed_report_with_a_run_level_error():
    report = build_execution_report(run_id=3, test_results=[], events=[], run_level_errors=["pytest exited with code 2"])
    assert report.status == "FAILED" and report.summary.totalSteps == 0
    assert any(e.category == "RUN_ERROR" for e in report.errors)


def test_two_tests_in_one_run_keep_their_own_steps():
    other = "test_login.py::TestLogin::test_other"
    events = [ev(1, "click"), {**ev(1, "click", "FAIL", error="TimeoutException('x')"), "nodeid": other}]
    report = build_execution_report(
        run_id=4, test_results=[tr("PASSED"), tr("FAILED", other)], events=events, meta={},
    )
    assert [t.status for t in report.tests] == ["PASSED", "FAILED"]
    assert report.status == "FAILED" and report.testcaseId is None
    assert report.summary.totalSteps == 2


# ---- redaction & artifact safety --------------------------------------------------
def test_redaction_masks_credentials_in_messages():
    text = redact("login failed password=Hunter2 token: abc123 Authorization: Bearer eyJhbGci.x.y")
    assert "Hunter2" not in text and "abc123" not in text and "eyJhbGci" not in text
    info = parse_error("TimeoutException('Message: waiting with password=Hunter2 failed')")
    assert "Hunter2" not in info.message and info.category == "TIMEOUT"


def test_password_step_values_are_redacted_in_the_report():
    events = [ev(1, "type", target="id=password", value="Sup3r!")]
    meta_ = {NODE: {"defined_steps": [{"number": 1, "action": "type", "target": "id=password", "value": "Sup3r!"}]}}
    report = build_execution_report(run_id=5, test_results=[tr()], events=events, meta=meta_)
    assert "Sup3r!" not in report_to_json(report)


@pytest.mark.parametrize("name", ["../secret.png", "a/b.png", "C:\\x.png", "x.txt", "", ".png", "a\x00.png", "x.png/../../y.png"])
def test_unsafe_screenshot_names_are_rejected(name):
    assert not is_safe_screenshot_name(name)


def test_screenshot_paths_only_reference_files_that_exist():
    events = [ev(1, "click", shot="0001_step_1_1.png"), ev(2, "click", "FAIL", error="TimeoutException('x')", shot="0002_gone.png"),
              ev(3, "click", shot="../../etc/passwd.png")]
    report = build(events, n=3, available_screenshots={"0001_step_1_1.png"})
    assert [s.screenshot for s in report.steps] == ["screenshots/0001_step_1_1.png", None, None]
    assert report.artifacts.screenshots == ["0001_step_1_1.png"]
    assert report.artifacts.executionLog is None and report.artifacts.video is None   # nothing falsely claimed


@pytest.mark.parametrize("type_,msg,expected", [
    ("NoSuchElementException", "", "LOCATOR_ERROR"), ("TimeoutException", "", "TIMEOUT"),
    ("InvalidSelectorException", "", "INVALID_SELECTOR"), ("ElementClickInterceptedException", "", "CLICK_INTERCEPTED"),
    ("StaleElementReferenceException", "", "STALE_ELEMENT"), ("NoSuchFrameException", "", "FRAME_ERROR"),
    ("InvalidSessionIdException", "", "BROWSER_CRASH"), ("SessionNotCreatedException", "", "BROWSER_INIT_ERROR"),
    ("WebDriverException", "chrome not reachable", "BROWSER_CRASH"), ("AssertionError", "", "ASSERTION_FAILURE"),
    ("ValueError", "boom", "UNEXPECTED_ERROR"),
])
def test_error_classification(type_, msg, expected):
    assert classify_error(type_, msg) == expected


def test_summary_model_rejects_negative_counts():
    with pytest.raises(ValidationError):
        Summary(totalSteps=-1, attemptedSteps=0, executedSteps=0, passedSteps=0, failedSteps=0,
                skippedSteps=0, totalErrors=0, totalPages=0)


# ---- additive fields (schema 1.0 stays backward compatible) ------------------------
NEW_TOP = ("reportType", "metadata", "environment", "executionLog")
NEW_SUMMARY = ("notRunSteps", "failureCount", "continuationEnabled", "warningCount")
NEW_STEP = ("stepId", "name", "action", "locator", "expected", "attempt")


def _legacy(report) -> str:
    """The report exactly as a pre-additive 1.0 build stored it."""
    data = json.loads(report_to_json(report))
    for key in NEW_TOP:
        data.pop(key)
    for key in NEW_SUMMARY:
        data["summary"].pop(key)
    for test in data["tests"]:
        for key in NEW_SUMMARY:
            test["summary"].pop(key)
    for step in data["steps"]:
        for key in NEW_STEP:
            step.pop(key)
    return json.dumps(data)


def test_stored_legacy_1_0_report_still_validates():
    report = build([ev(1, "click"), ev(2, "click", "FAIL", error="TimeoutException('x')")], status="FAILED", n=3)
    legacy = report_from_json(_legacy(report))
    assert legacy.reportType is None and legacy.metadata is None and legacy.summary.notRunSteps is None
    assert legacy.steps[0].stepId is None
    assert (legacy.summary.totalSteps, legacy.summary.failedSteps, legacy.status) == (3, 1, "FAILED")
    assert legacy.executionId == "RUN-1"


def test_legacy_report_with_wrong_counts_is_still_rejected():
    data = json.loads(_legacy(build([ev(1, "click")], n=1)))
    data["summary"]["passedSteps"] = 9
    with pytest.raises(ValidationError):
        ExecutionReport.model_validate(data)


def test_additive_fields_are_populated_from_real_data():
    events = [ev(1, "click", target="id=a"), ev(2, "assertTitle", "FAIL", value="Welcome", error="AssertionError('t')"),
              ev(3, "click", "SKIP", skip_reason="Depends on 'x', which is unavailable because step 2 failed")]
    m = {NODE: {"defined_steps": [
        {"number": 1, "action": "click", "target": "id=a", "value": ""},
        {"number": 2, "action": "assertTitle", "target": "", "value": "Welcome"},
        {"number": 3, "action": "click", "target": "id=b", "value": ""},
        {"number": 4, "action": "click", "target": "id=c", "value": ""}]},
        "__run__": {"continuationEnabled": True}}
    report = build_execution_report(run_id=9, test_results=[tr("FAILED")], events=events, meta=m, browser="chrome",
                                    project_id=3, project_name="Shop")
    assert report.reportType == "automation_execution"
    assert (report.metadata.projectId, report.metadata.projectName, report.metadata.runId) == (3, "Shop", 9)
    assert report.metadata.executionEngine == "selenium" and report.metadata.testFile == "test_login.py"
    assert report.metadata.triggeredBy is None and report.metadata.runName is None
    assert report.environment.browser == "chrome" and report.environment.browserVersion is None
    s = report.summary
    assert (s.notRunSteps, s.failureCount, s.continuationEnabled, s.warningCount) == (1, 1, True, None)
    assert (s.skippedSteps, s.attemptedSteps) == (2, 2)            # step 3 dependency-skipped, step 4 not run

    one, two, three, four = report.steps
    assert one.stepId == f"{NODE}#step-1" and one.locator.strategy == "id" and one.locator.value == "a"
    assert (one.name, one.action, one.attempt) == (one.stepName, one.command, 1)
    assert two.expected == "Welcome" and one.expected is None      # expected only for assert*/verify*
    assert three.attempt is None and three.attempted is False and not three.skipReason.startswith("Not executed")
    assert four.status == "SKIPPED" and four.skipReason.startswith("Not executed")

    levels = [(e.stepNumber, e.level) for e in report.executionLog]
    assert levels == [(1, "INFO"), (2, "ERROR"), (3, "WARNING")]     # one entry per real event, nothing invented


def test_continuation_flag_is_null_when_runner_did_not_report_it():
    assert build([ev(1, "click")], n=1).summary.continuationEnabled is None
