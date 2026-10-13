"""Security properties of the merged runner / persistence / report pipeline.

Typed values of sensitive fields must reach no public artefact: not steps.json, not an event
file, not a stored step, not the JSON report, not an API response, not a log line. They live only
in a private file in the run's own temporary directory, which exists so the backend can still
mask them, and which disappears with that directory.
"""
import json
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import execution_service
from app.services import result_persistence_service as rps
from tests.test_merge_integration import HEADER, LOGIN, CREDS, PASSWORD, run_fake, stored_report
from tests.test_run_result_capture import results_json
from tests.test_screenshot_redaction import run_script as run_compat_module


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("SELENIUM_FIND_TIMEOUT", "0.3")
    monkeypatch.setenv("SELENIUM_IMPLICIT_WAIT_SECONDS", "0")


# ---------------------------------------------------------------------------
# Detection: which fields are treated as sensitive
# ---------------------------------------------------------------------------

SENSITIVE = [("id", "password"), ("id", "api_token"), ("id", "login-pin"), ("name", "ssn"),
             ("id", "card number"), ("id", "verification-code"), ("name", "client_secret"), ("id", "otp")]


def test_credential_looking_fields_are_withheld_and_ordinary_fields_are_not(tmp_path):
    result, _ = run_compat_module(f'''
cases = {SENSITIVE!r}
for index, locator in enumerate(cases):
    mod._record("type", "input", "Typed-%d-Value" % index, 0.01, "PASS", locator=tuple(locator))
mod._record("type", "input", "ordinary-typed-text", 0.01, "PASS", locator=("id", "email"))
mod._record("type", "input", "from-password-input", 0.01, "PASS", locator=("id", "x"), sensitive=True)
private = json.loads(open(mod.RUN_DIR / "typed_values.json", encoding="utf-8").read())
print(json.dumps({{"events": mod.EVENTS, "private": private}}))
''', tmp_path)

    events = result["events"]
    for index in range(len(SENSITIVE)):
        event = events[index]
        assert event["value"] == "***REDACTED***" and event["runtime_value"] == "", SENSITIVE[index]
        assert f"Typed-{index}-Value" in result["private"]
    ordinary = events[len(SENSITIVE)]
    assert ordinary["runtime_value"] == "ordinary-typed-text" and "ordinary-typed-text" not in result["private"]
    assert events[-1]["runtime_value"] == "" and "from-password-input" in result["private"]     # a password input
    public = json.dumps(events)
    assert all(f"Typed-{i}-Value" not in public for i in range(len(SENSITIVE))) and "from-password-input" not in public


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits (checked inside the Docker container instead)")
def test_the_private_file_is_owner_only(tmp_path):
    result, _ = run_compat_module('''
mod._record("type", "input", "Typed-secret-1", 0.01, "PASS", locator=("id", "password"))
print(json.dumps({"mode": oct(__import__("stat").S_IMODE(mod.RUN_DIR.joinpath("typed_values.json").stat().st_mode))}))
''', tmp_path)
    assert result["mode"] == "0o600"


# ---------------------------------------------------------------------------
# Lifetime: the private file goes with the run's temporary directory
# ---------------------------------------------------------------------------

def test_the_private_file_is_removed_with_its_directory_on_success_and_on_failure(monkeypatch):
    seen = []

    def finishes(db, run, tmp_path):
        seen.append(tmp_path)
        (tmp_path / "typed_values.json").write_text('["private"]', encoding="utf-8")

    def crashes(db, run, tmp_path):
        seen.append(tmp_path)
        (tmp_path / "typed_values.json").write_text('["private"]', encoding="utf-8")
        raise RuntimeError("the run blew up")

    files = [SimpleNamespace(file_name="test_a.py", file_content="def test_a():\n    pass\n")]
    run = SimpleNamespace(id=1, browser="chrome")

    monkeypatch.setattr(execution_service, "_execute_pytest", finishes)
    execution_service._run_with_temp_directory(None, run, files)
    monkeypatch.setattr(execution_service, "_execute_pytest", crashes)
    with pytest.raises(RuntimeError):
        execution_service._run_with_temp_directory(None, run, files)

    assert len(seen) == 2 and not any(path.exists() for path in seen)         # gone either way (cancel/timeout use the same block)


def test_the_private_file_is_not_a_screenshot_or_report_artifact(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_fake(db_session, test_project, tmp_path, monkeypatch, LOGIN, {"creds.py": CREDS})
    assert (tmp_path / "typed_values.json").exists()                           # it existed during the run...
    for name in ("typed_values.json", "../typed_values.json", "steps.json"):    # ...and no public route can reach it
        response = client.get(
            f"/api/projects/{test_project.id}/runs/{run.id}/artifacts/screenshots/{name}", headers=auth_headers)
        assert response.status_code in (400, 404, 422), name
    assert PASSWORD not in stored_report(db_session, run)


# ---------------------------------------------------------------------------
# Failing safe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("content", [None, "{not json", '{"a": 1}', '[1, 2, null]', "[]"])
def test_a_missing_or_malformed_private_file_yields_nothing_and_exposes_nothing(tmp_path, caplog, content):
    if content is not None:
        (tmp_path / "typed_values.json").write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert rps.read_private_typed_values(tmp_path) == []
    assert "{not json" not in caplog.text                                       # the content is never logged
    assert ("unreadable" in caplog.text) == (content == "{not json")


def test_with_the_private_file_unreadable_steps_and_report_still_hold_no_secret(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    # The runner already scrubbed what it recorded, so losing the private file only costs the
    # by-value masking of pytest's own failure text, never a leak through steps or the report.
    captured = {}
    real = rps.persist_run_results

    def corrupting(db, run, **kw):
        (tmp_path / "typed_values.json").write_text("{corrupt", encoding="utf-8")
        captured.update(kw)

    monkeypatch.setattr(execution_service, "persist_run_results", corrupting)
    from tests.test_run_result_capture import make_run
    run = make_run(db_session, test_project, status="RUNNING")
    (tmp_path / "test_defaute.py").write_text(LOGIN, encoding="utf-8")
    library = tmp_path.parent / f"{tmp_path.name}_selenium_fakes"
    library.mkdir(exist_ok=True)
    from tests.test_runner_failed_steps import FAKES
    (library / "fakebrowser.py").write_text(FAKES, encoding="utf-8")
    (library / "creds.py").write_text(CREDS, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(library))
    execution_service._write_conftest(tmp_path, run.id)
    from tests.test_run_cancellation_race import FakeDB, FakeRun
    execution_service._execute_pytest(FakeDB(), FakeRun(run_id=run.id, browser="chrome"), tmp_path)
    real(db_session, run, **captured)

    data = json.dumps(results_json(client, test_project, run, auth_headers)["tests"][0]["steps"])
    assert PASSWORD not in data and PASSWORD not in (tmp_path / "steps.json").read_text(encoding="utf-8")
    assert PASSWORD not in stored_report(db_session, run)


# ---------------------------------------------------------------------------
# Assertions: passing keeps its expected text, a failed one does not repeat it
# ---------------------------------------------------------------------------

ASSERTS = HEADER + '''
  def test_asserts(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | assertText | id=title | Expected-OK
    assert True
    # 3 | assertText | id=title | Expected-BAD
    assert self.driver.title == "Expected-BAD", "message-secret-9"
'''


def steps_of(report):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "stepId" in node and "stepNumber" in node:  # the report's step objects (not page events)
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(report)
    return found


def test_assertion_text_is_kept_when_it_passes_and_not_repeated_when_it_fails(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_fake(db_session, test_project, tmp_path, monkeypatch, ASSERTS)

    # steps.json
    raw = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    by_step = {s["step"]: s for s in raw if s["action"] == "assertText"}
    assert by_step[2]["status"] == "PASS" and by_step[2]["value"] == "Expected-OK"
    assert (by_step[3]["status"], by_step[3]["value"], by_step[3]["error"]) == ("FAIL", "", "AssertionError()")

    # stored steps (API)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    stored = {s["step_number"]: s for s in test["steps"] if s["action"] == "assertText"}
    assert (stored[2]["status"], stored[2]["value"]) == ("PASS", "Expected-OK")
    assert (stored[3]["status"], stored[3]["value"], stored[3]["error"]) == ("FAIL", None or stored[3]["value"], "AssertionError()")
    assert stored[3]["value"] in (None, "")                                    # the action and failure status stay; the text does not
    assert "message-secret-9" not in json.dumps(test["steps"])

    # JSON execution report
    report = json.loads(stored_report(db_session, run))
    steps = {s["stepNumber"]: s for s in steps_of(report) if s["command"] == "assertText"}
    assert steps[2]["status"] == "PASSED" and steps[2]["expected"] == "Expected-OK"
    assert steps[3]["status"] == "FAILED" and steps[3]["command"] == "assertText"
    assert not steps[3].get("expected") and not steps[3].get("value")
    dumped = json.dumps(report)
    assert "Expected-BAD" not in dumped and "message-secret-9" not in dumped
