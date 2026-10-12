"""Result capture for runs whose uploaded file is not named test_*.py, and actionable
results for runs that record no test cases.

Reported bug: an IDE export uploaded as "tes(1).py" produced "No test results were
captured for this run". pytest's directory discovery only collects test_*.py and
*_test.py, so it exited with code 5 ("no tests ran") before importing the script - the
Selenium compatibility layer was never reached. These tests drive the engine's real code
path (real pytest subprocess, real persistence, the results API); only the browser is
faked.
"""
import hashlib
import json
from pathlib import Path

import pytest

from app.models.run_result import RunResult
from app.services import execution_service
from app.services import result_persistence_service
from app.services.run_issue_service import describe_run_issue
from app.services.test_file_selection import matches_default_pattern, select_test_files
from tests.conftest import make_project_file, make_run
from tests.test_run_cancellation_race import FakeDB, FakeRun
from tests.test_runner_legacy_init import LEGACY_BAD, LEGACY_OK

IDE_EXPORT = '''
class TestDefaultSuite():
  def setup_method(self, method):
    self.x = 1
  def test_ana(self):
    assert self.x == 1
'''


# ---------------------------------------------------------------------------
# Which files are collected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("test_login.py", True), ("login_test.py", True), ("tes(1).py", False), ("tes.py", False),
    ("login.py", False), ("test.py", False), ("test_defaultSuite (1).py", True),
])
def test_default_pytest_name_patterns(name, expected):
    assert matches_default_pattern(name) is expected


def test_files_that_define_tests_are_selected_whatever_their_name(tmp_path):
    (tmp_path / "tes(1).py").write_text(IDE_EXPORT)                                       # the reported file name
    (tmp_path / "plain_function.py").write_text("def test_it():\n    assert True\n")
    (tmp_path / "unit.py").write_text("import unittest\nclass Case(unittest.TestCase):\n    def test_a(self):\n        pass\n")
    (tmp_path / "test_normal.py").write_text(IDE_EXPORT)
    selected, by_content = select_test_files(tmp_path)
    assert [p.name for p in selected] == ["plain_function.py", "tes(1).py", "test_normal.py", "unit.py"]
    assert sorted(p.name for p in by_content) == ["plain_function.py", "tes(1).py", "unit.py"]


def test_helpers_conftest_and_unparsable_files_are_left_alone(tmp_path):
    (tmp_path / "conftest.py").write_text("def pytest_sessionfinish(session, exitstatus):\n    pass\n")
    (tmp_path / "helpers.py").write_text("def helper():\n    return 1\n\nclass Page:\n    def open(self):\n        pass\n")
    (tmp_path / "data.py").write_text("TestData = {'a': 1}\n")                           # a Test* name without test methods
    (tmp_path / "broken.py").write_text("def test_x(:\n")                                   # unparsable and not named test_*.py
    selected, by_content = select_test_files(tmp_path)
    assert selected == [] and by_content == []


def test_ordinary_uploads_keep_the_plain_directory_run(tmp_path):
    (tmp_path / "test_login.py").write_text(IDE_EXPORT)
    (tmp_path / "helpers.py").write_text("def helper():\n    return 1\n")
    assert execution_service._pytest_targets(1, tmp_path) == [str(tmp_path)]


def test_unusual_names_are_passed_to_pytest_explicitly(tmp_path):
    (tmp_path / "tes(1).py").write_text(IDE_EXPORT)
    (tmp_path / "test_other.py").write_text(IDE_EXPORT)
    (tmp_path / "helpers.py").write_text("def helper():\n    return 1\n")
    assert execution_service._pytest_targets(1, tmp_path) == [str(tmp_path / "tes(1).py"), str(tmp_path / "test_other.py")]


# ---------------------------------------------------------------------------
# The reported failure, through the real engine path
# ---------------------------------------------------------------------------

def execute_and_persist(db_session, project, tmp_path, files: dict[str, str], monkeypatch):
    """Runs the engine's _execute_pytest (real pytest subprocess, real command) on `files`,
    then persists the outcome with the real persistence service. Returns (run, hashes)."""
    run = make_run(db_session, project, status="RUNNING")
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    before = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in files}
    execution_service._write_conftest(tmp_path, run.id)

    captured = {}
    monkeypatch.setattr(execution_service, "persist_run_results", lambda db, r, **kw: captured.update(kw))
    fake_run = FakeRun(run_id=run.id, browser="chrome")
    execution_service._execute_pytest(FakeDB(), fake_run, tmp_path)

    result_persistence_service.persist_run_results(db_session, run, **captured)
    db_session.refresh(run)
    after = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in files}
    assert after == before, "an uploaded source file was modified by the run"
    return run, fake_run, before


def results_json(client, project, run, headers):
    run.status = "FAILED"
    response = client.get(f"/api/projects/{project.id}/runs/{run.id}/results", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_reported_file_name_is_collected_run_and_recorded(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, fake_run, hashes = execute_and_persist(db_session, test_project, tmp_path, {"tes(1).py": LEGACY_OK}, monkeypatch)

    assert fake_run.status == "FAILED"                           # one of the two tests fails on purpose
    data = results_json(client, test_project, run, auth_headers)
    assert data["issue"] is None                                 # results exist: nothing to explain
    assert data["summary"]["exit_code"] == 1 and data["summary"]["total_tests"] == 2
    tests = {t["method_name"]: t for t in data["tests"]}
    assert set(tests) == {"test_defaute", "test_fails_later"}
    assert tests["test_defaute"]["nodeid"] == "tes(1).py::TestDefaute::test_defaute"
    assert tests["test_defaute"]["status"] == "PASSED" and tests["test_fails_later"]["status"] == "FAILED"
    assert [s["action"] for s in tests["test_defaute"]["steps"]] == ["open", "click"]      # steps captured through the compat layer
    # the legacy Remote(... desired_capabilities=...) setup was adapted, not rewritten
    assert b"desired_capabilities=DesiredCapabilities.CHROME" in (tmp_path / "tes(1).py").read_bytes()
    assert hashes["tes(1).py"] == hashlib.sha256(LEGACY_OK.replace("\n", "\r\n" if b"\r\n" in (tmp_path / "tes(1).py").read_bytes() else "\n").encode()).hexdigest()


def test_the_same_script_under_the_old_discovery_collects_nothing(tmp_path):
    # Reproduces the bug itself: the plain directory run that the engine used before.
    import subprocess
    import sys
    (tmp_path / "tes(1).py").write_text(IDE_EXPORT)
    done = subprocess.run([sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider"],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 5 and "no tests ran" in done.stdout


# ---------------------------------------------------------------------------
# Runs that record no tests: an honest, specific explanation (never a fabricated result)
# ---------------------------------------------------------------------------

def test_a_file_with_no_tests_is_explained_with_the_real_exit_code_and_output(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    make_project_file(db_session, test_project, "helpers.py", "def helper():\n    return 1\n")
    run, fake_run, _ = execute_and_persist(db_session, test_project, tmp_path, {"helpers.py": "def helper():\n    return 1\n"}, monkeypatch)

    data = results_json(client, test_project, run, auth_headers)
    assert data["tests"] == []                                    # no test result was invented
    assert data["summary"]["total_tests"] == 0 and data["summary"]["exit_code"] == 5
    issue = data["issue"]
    assert issue["code"] == "NO_TESTS_COLLECTED" and issue["exit_code"] == 5
    assert "helpers.py" in issue["detail"] and "no tests ran" in issue["output_tail"]
    assert any("test_*.py" in hint for hint in issue["hints"])


def test_a_collection_error_is_a_recorded_error_not_an_empty_run(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, fake_run, _ = execute_and_persist(db_session, test_project, tmp_path, {"test_broken.py": "def test_x(:\n    pass\n"}, monkeypatch)
    data = results_json(client, test_project, run, auth_headers)
    assert data["summary"]["exit_code"] == 2
    assert data["issue"] is None and [t["status"] for t in data["tests"]] == ["ERROR"]


@pytest.mark.parametrize("exit_code,code", [
    (2, "COLLECTION_ERROR"), (3, "PYTEST_INTERNAL_ERROR"), (4, "PYTEST_USAGE_ERROR"), (1, "RESULT_REPORT_UNREADABLE"),
    (0, "NO_TEST_CASES_RECORDED"), (-9, "PROCESS_ENDED_ABNORMALLY"), (139, "PROCESS_ENDED_ABNORMALLY"),
    (None, "PROCESS_ENDED_ABNORMALLY"),
])
def test_every_exit_code_gets_its_own_explanation_through_the_api(client, db_session, test_project, auth_headers, exit_code, code):
    run = make_run(db_session, test_project, status="FAILED")
    db_session.add(RunResult(run_id=run.id, exit_code=exit_code, total_tests=0, passed_count=0, failed_count=0,
                             skipped_count=0, stdout="collected 0 items\nSOME-OUTPUT", stderr=""))
    db_session.commit()
    data = results_json(client, test_project, run, auth_headers)
    assert data["tests"] == [] and data["issue"]["code"] == code
    assert data["issue"]["exit_code"] == exit_code
    assert "SOME-OUTPUT" in (data["issue"]["output_tail"] or "")


def test_a_run_without_any_result_record_says_so(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    data = results_json(client, test_project, run, auth_headers)
    assert data["summary"] is None and data["tests"] == []
    assert data["issue"]["code"] == "RESULTS_NOT_RECORDED" and data["issue"]["exit_code"] is None


def test_issue_output_is_sanitized_and_bounded():
    issue = describe_run_issue(
        run_status="FAILED", has_run_result=True, exit_code=2,
        stdout="x" * 5000 + "\npassword=hunter2 and token=abc123\nKNOWN-LITERAL-77", stderr="",
        file_names=[f"f{i}.py" for i in range(30)], secrets=("KNOWN-LITERAL-77",),
    )
    assert len(issue.output_tail) < 2100
    assert "hunter2" not in issue.output_tail and "KNOWN-LITERAL-77" not in issue.output_tail
    nothing = describe_run_issue(run_status="FAILED", has_run_result=True, exit_code=5, stdout="", stderr="", file_names=[])
    assert nothing.output_tail is None and "none" in nothing.detail
    many = describe_run_issue(run_status="FAILED", has_run_result=True, exit_code=5, stdout="", stderr="",
                              file_names=[f"f{i}.py" for i in range(30)])
    assert "(+20 more)" in many.detail


# ---------------------------------------------------------------------------
# Browser initialization failures are captured as setup errors, not assertion failures
# ---------------------------------------------------------------------------

def test_unsupported_browser_setup_is_recorded_as_an_error_with_its_cause(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, fake_run, _ = execute_and_persist(db_session, test_project, tmp_path, {"tes(1).py": LEGACY_BAD}, monkeypatch)

    data = results_json(client, test_project, run, auth_headers)
    assert data["issue"] is None and len(data["tests"]) == 1
    test = data["tests"][0]
    assert test["status"] == "ERROR"                              # a setup error, not FAILED (an assertion/test failure)
    dumped = json.dumps(test)
    assert "ConvergenceCompatibilityError" in dumped and "fooBar" in dumped
    # The runner's own message names the problem but never echoes a capability's value. (The traceback
    # still shows the user's own source line, as it does for any failing test.)
    failure = test["failure"]
    assert failure["exception_type"].endswith("ConvergenceCompatibilityError")  # module-qualified, like selenium.common...*
    assert "fooBar" in failure["exception_message"] and "SECRET-VALUE-1" not in failure["exception_message"]
    assert failure["source_file"] == "tes(1).py" and failure["source_line_number"] > 0       # the script line that made the call
    messages = [e["value"] for e in test["analysis"]["evidence"] if "message" in e["label"].lower()]
    assert all("SECRET-VALUE-1" not in message for message in messages)
    assert test["analysis"]["failure_category"] == "BROWSER_CONFIGURATION_ERROR"
    assert any(e["label"] == "Test status" and "setup or teardown" in e["value"] for e in test["analysis"]["evidence"])
    assert [e for e in test["steps"] if e["status"] == "PASS"] == []                      # no steps ran: partial evidence only


# ---------------------------------------------------------------------------
# Damaged side files never lose the recorded tests
# ---------------------------------------------------------------------------

def test_malformed_steps_file_and_missing_event_file_still_record_the_tests(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run = make_run(db_session, test_project, status="RUNNING")
    (tmp_path / "tes(1).py").write_text(LEGACY_OK, encoding="utf-8")
    execution_service._write_conftest(tmp_path, run.id)
    captured = {}
    monkeypatch.setattr(execution_service, "persist_run_results", lambda db, r, **kw: captured.update(kw))
    execution_service._execute_pytest(FakeDB(), FakeRun(run_id=run.id, browser="chrome"), tmp_path)

    (tmp_path / "steps.json").write_text("{not json", encoding="utf-8")                     # malformed
    (tmp_path / "events.jsonl").unlink()                                                    # missing
    result_persistence_service.persist_run_results(db_session, run, **captured)

    data = results_json(client, test_project, run, auth_headers)
    assert data["issue"] is None and data["summary"]["total_tests"] == 2
    assert {t["status"] for t in data["tests"]} == {"PASSED", "FAILED"}
    assert all(t["steps"] == [] for t in data["tests"])                                     # no steps invented from a bad file
