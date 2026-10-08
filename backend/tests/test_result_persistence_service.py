

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.failure_diagnostic import FailureDiagnostic
from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.services.result_persistence_service import persist_run_results
from tests.conftest import make_project_file, make_run

_PASSED_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0">
<testcase classname="test_login" name="test_valid_login" time="1.230" file="test_login.py"/>
</testsuite></testsuites>
"""

_FAILED_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1" failures="1" errors="0" skipped="0">
<testcase classname="test_login" name="test_invalid_login" time="0.500" file="test_login.py">
<failure message="AssertionError">test_login.py:42: in test_invalid_login
    assert False
E   assert False
</failure>
</testcase>
</testsuite></testsuites>
"""

_MULTI_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
<testcase classname="test_a" name="test_one" time="0.1" file="test_a.py"/>
<testcase classname="test_b" name="test_two" time="0.2" file="test_b.py">
<failure message="boom">test_b.py:7: in test_two
    raise ValueError("boom")
E   ValueError: boom
</failure>
</testcase>
</testsuite></testsuites>
"""

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_persist_passed_test(db_session, test_project, tmp_path):
    make_project_file(db_session, test_project, "test_login.py", "# stub")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _PASSED_JUNIT)
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([
        {
            "nodeid": "test_login.py::test_valid_login",
            "step": 1,
            "action": "open",
            "target": "/login",
            "value": "",
            "status": "PASS",
            "duration": 0.1,
            "error": "",
            "screenshot": "",
            "timestamp": 1700000000.0,
        }
    ]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"

    persist_run_results(
        db_session, run,
        exit_code=0, stdout="ok", stderr="",
        junit_report_path=junit_path,
        steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )

    test_result = db_session.scalar(select(TestResult).where(TestResult.run_id == run.id))
    assert test_result is not None
    assert test_result.status == "PASSED"
    assert test_result.nodeid == "test_login.py::test_valid_login"
    assert test_result.project_file_id is not None

    steps = db_session.scalars(select(StepResult).where(StepResult.test_result_id == test_result.id)).all()
    assert len(steps) == 1
    assert steps[0].status == "PASS"
    assert steps[0].sequence_index == 0

    run_result = db_session.scalar(select(RunResult).where(RunResult.run_id == run.id))
    assert run_result.total_tests == 1
    assert run_result.passed_count == 1
    assert run_result.exit_code == 0


def test_persist_passed_test_with_all_mode_screenshots(db_session, test_project, tmp_path):
    """SELENIUM_COMPAT_SCREENSHOT_MODE=all captures a screenshot on every
    passing step too (not just failures) — regression test for the bug
    where a PASSED Selenium test produced no screenshots at all because
    execution_service never set the screenshot mode, so it silently
    defaulted to "failed"."""

    make_project_file(db_session, test_project, "test_login.py", "# stub")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _PASSED_JUNIT)
    shot_1 = "0001_step_1_1700000000000.png"
    shot_2 = "0002_step_2_1700000000100.png"
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([
        {
            "nodeid": "test_login.py::test_valid_login",
            "step": 1,
            "action": "open",
            "target": "/login",
            "value": "",
            "status": "PASS",
            "duration": 0.1,
            "error": "",
            "screenshot": shot_1,
            "timestamp": 1700000000.0,
        },
        {
            "nodeid": "test_login.py::test_valid_login",
            "step": 2,
            "action": "click",
            "target": "#submit",
            "value": "",
            "status": "PASS",
            "duration": 0.2,
            "error": "",
            "screenshot": shot_2,
            "timestamp": 1700000000.5,
        },
    ]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / shot_1).write_bytes(_PNG_MAGIC)
    (screenshots_dir / shot_2).write_bytes(_PNG_MAGIC)

    persist_run_results(
        db_session, run,
        exit_code=0, stdout="ok", stderr="",
        junit_report_path=junit_path,
        steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )

    test_result = db_session.scalar(select(TestResult).where(TestResult.run_id == run.id))
    assert test_result.status == "PASSED"

    steps = db_session.scalars(
        select(StepResult).where(StepResult.test_result_id == test_result.id).order_by(StepResult.sequence_index)
    ).all()
    assert len(steps) == 2

    screenshots = db_session.scalars(
        select(Screenshot).where(Screenshot.test_result_id == test_result.id)
    ).all()
    assert len(screenshots) == 2

    shots_by_name = {s.file_name: s for s in screenshots}
    # Each screenshot must be linked to the correct step, not left orphaned.
    assert shots_by_name[shot_1].step_result_id == steps[0].id
    assert shots_by_name[shot_2].step_result_id == steps[1].id
    for s in screenshots:
        assert s.capture_reason == "ALL_MODE"
        assert s.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert s.size_bytes == len(_PNG_MAGIC)


def test_persist_failed_test_with_step_failure_and_screenshot_and_diagnostic(db_session, test_project, tmp_path):
    make_project_file(db_session, test_project, "test_login.py", "# stub")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _FAILED_JUNIT)
    screenshot_name = "0001_step_1_1700000000000.png"
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([
        {
            "nodeid": "test_login.py::test_invalid_login",
            "step": 1,
            "action": "click",
            "target": "#submit",
            "value": "",
            "status": "FAIL",
            "duration": 0.3,
            "error": "ElementClickInterceptedException",
            "screenshot": screenshot_name,
            "timestamp": 1700000000.0,
        }
    ]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / screenshot_name).write_bytes(_PNG_MAGIC)

    persist_run_results(
        db_session, run,
        exit_code=1, stdout="fail", stderr="",
        junit_report_path=junit_path,
        steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )

    test_result = db_session.scalar(select(TestResult).where(TestResult.run_id == run.id))
    assert test_result.status == "FAILED"

    step = db_session.scalar(select(StepResult).where(StepResult.test_result_id == test_result.id))
    assert step.status == "FAIL"

    screenshot = db_session.scalar(select(Screenshot).where(Screenshot.test_result_id == test_result.id))
    assert screenshot is not None
    assert screenshot.step_result_id == step.id
    assert screenshot.capture_reason == "STEP_FAILURE"
    assert screenshot.content == _PNG_MAGIC
    assert screenshot.size_bytes == len(_PNG_MAGIC)
    assert screenshot.content[:8] == b"\x89PNG\r\n\x1a\n"

    diagnostic = db_session.scalar(select(FailureDiagnostic).where(FailureDiagnostic.test_result_id == test_result.id))
    assert diagnostic is not None
    assert diagnostic.exception_type == "assert False" or diagnostic.exception_message is not None
    assert diagnostic.source_line_number == 42


def test_persist_multiple_files_multiple_tests(db_session, test_project, tmp_path):
    make_project_file(db_session, test_project, "test_a.py", "# a")
    make_project_file(db_session, test_project, "test_b.py", "# b")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _MULTI_JUNIT)

    persist_run_results(
        db_session, run,
        exit_code=1, stdout="", stderr="",
        junit_report_path=junit_path,
        steps_json_path=tmp_path / "steps.json",
        screenshots_dir=tmp_path / "screenshots",
    )

    tests = db_session.scalars(select(TestResult).where(TestResult.run_id == run.id)).all()
    assert len(tests) == 2
    by_name = {t.file_name: t for t in tests}
    assert by_name["test_a.py"].status == "PASSED"
    assert by_name["test_b.py"].status == "FAILED"

    run_result = db_session.scalar(select(RunResult).where(RunResult.run_id == run.id))
    assert run_result.total_tests == 2
    assert run_result.passed_count == 1
    assert run_result.failed_count == 1


def test_missing_junit_and_steps_json_is_not_an_error(db_session, test_project, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")

    persist_run_results(
        db_session, run,
        exit_code=1, stdout="", stderr="",
        junit_report_path=tmp_path / "does_not_exist.xml",
        steps_json_path=tmp_path / "does_not_exist.json",
        screenshots_dir=tmp_path / "does_not_exist",
    )

    run_result = db_session.scalar(select(RunResult).where(RunResult.run_id == run.id))
    assert run_result is not None
    assert run_result.total_tests == 0

    tests = db_session.scalars(select(TestResult).where(TestResult.run_id == run.id)).all()
    assert tests == []


def test_cascade_delete_removes_children(db_session, test_project, tmp_path):
    make_project_file(db_session, test_project, "test_login.py", "# stub")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _FAILED_JUNIT)
    screenshot_name = "0001_step_1_1700000000000.png"
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([
        {
            "nodeid": "test_login.py::test_invalid_login",
            "step": 1, "action": "click", "target": "#submit", "value": "",
            "status": "FAIL", "duration": 0.3, "error": "boom",
            "screenshot": screenshot_name, "timestamp": 1700000000.0,
        }
    ]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / screenshot_name).write_bytes(_PNG_MAGIC)

    persist_run_results(
        db_session, run, exit_code=1, stdout="", stderr="",
        junit_report_path=junit_path, steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )

    test_result = db_session.scalar(select(TestResult).where(TestResult.run_id == run.id))
    test_result_id = test_result.id

    db_session.delete(test_result)
    db_session.commit()

    assert db_session.scalar(select(StepResult).where(StepResult.test_result_id == test_result_id)) is None
    assert db_session.scalar(select(Screenshot).where(Screenshot.test_result_id == test_result_id)) is None
    assert db_session.scalar(select(FailureDiagnostic).where(FailureDiagnostic.test_result_id == test_result_id)) is None


def test_mid_transaction_failure_rolls_back_everything(db_session, test_project, tmp_path, monkeypatch):
    make_project_file(db_session, test_project, "test_login.py", "# stub")
    run = make_run(db_session, test_project, status="RUNNING")

    junit_path = _write(tmp_path, "report.xml", _PASSED_JUNIT)

    import app.services.result_persistence_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure parsing diagnostics")

    monkeypatch.setattr(svc, "parse_junit_report", _boom)

    run_id = run.id  # capture before rollback expires/detaches the instance

    with pytest.raises(RuntimeError):
        persist_run_results(
            db_session, run, exit_code=0, stdout="", stderr="",
            junit_report_path=junit_path,
            steps_json_path=tmp_path / "steps.json",
            screenshots_dir=tmp_path / "screenshots",
        )

    # Rolled back: zero rows committed for this run, even though TestResult
    # rows were added+flushed earlier in the same call.
    assert db_session.scalar(select(TestResult).where(TestResult.run_id == run_id)) is None
    assert db_session.scalar(select(RunResult).where(RunResult.run_id == run_id)) is None
