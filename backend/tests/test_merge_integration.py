"""Behaviour that only exists because two branches were merged.

`anand` (live events, failed-step records, secret hardening, legacy-init compatibility) and
`feature/abhi-future-work` (continue-after-failure step guard, SKIP steps, JSON execution
reports) touch the same runner and persistence code. These tests pin the places where they
meet: a failure must be recorded once and as the right step, a typed secret must reach no
record or report, and a report is built from the same sanitized facts as the stored results.
"""
import json
import os

import pytest

from app.services import run_event_service as svc
from app.services import result_persistence_service as rps
from tests.test_run_result_capture import execute_and_persist, results_json
from tests.test_runner_failed_steps import FAKES, HEADER, IDE_MOUSE_OVER, line_of
from tests.test_runner_legacy_init import LEGACY_BAD
from tests.test_screenshot_redaction import COMPAT, run_script as run_compat_module

PASSWORD = "Fixture-Pw-7731"
EMAIL = "demo.user@example.test"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setenv("SELENIUM_FIND_TIMEOUT", "0.3")
    monkeypatch.setenv("SELENIUM_IMPLICIT_WAIT_SECONDS", "0")
    svc.reset_registry()


def run_fake(db_session, project, tmp_path, monkeypatch, source, extra_modules=None):
    """Real pytest subprocess through the engine path; the browser and any fixture module live
    outside the run directory (on PYTHONPATH), like library code."""
    library = tmp_path.parent / f"{tmp_path.name}_selenium_fakes"
    library.mkdir(exist_ok=True)
    (library / "fakebrowser.py").write_text(FAKES, encoding="utf-8")
    for name, content in (extra_modules or {}).items():
        (library / name).write_text(content, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(library) + (os.pathsep + existing if existing else ""))
    return execute_and_persist(db_session, project, tmp_path, {"test_defaute.py": source}, monkeypatch)


def events_of(tmp_path):
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def stored_report(db_session, run):
    if not rps.report_column_available():
        pytest.skip("run_results.report_json is not available in this database")
    raw = rps.load_report_json(db_session, run.id)
    assert raw, "a JSON execution report was stored"
    return raw


# ---------------------------------------------------------------------------
# One step_for_line, both call styles
# ---------------------------------------------------------------------------

def test_step_for_line_has_one_definition_and_both_call_styles_work(tmp_path):
    script = tmp_path / "ide.py"
    script.write_text("# 1 | open | / |\nx = 1\n# 2 | assertText | id=a | hello |\ny = 2\n", encoding="utf-8")
    result, _ = run_compat_module(f'''
import ast, pathlib
source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
defs = [n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)]
print(json.dumps({{
    "definitions": defs.count("_step_for_line"),
    "nearest": mod._step_for_line({str(script)!r}, 4)["number"],
    "with_action": mod._step_for_line({str(script)!r}, 4, "open")["number"],
    "unknown_action_falls_back": mod._step_for_line({str(script)!r}, 4, "click")["number"],
    "before_any_step": mod._step_for_line({str(script)!r}, 0),
}}))
''', tmp_path)
    assert result == {"definitions": 1, "nearest": 2, "with_action": 1, "unknown_action_falls_back": 2, "before_any_step": None}


# ---------------------------------------------------------------------------
# The step guard keeps going, and our failed-step records still say what failed
# ---------------------------------------------------------------------------

CONTINUES = HEADER + '''
  def test_continues(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | click | linkText=Home |
    self.driver.find_element(By.LINK_TEXT, "Home").click()
    # 3 | mouseOver | linkText=Shop Now → |
    element = self.driver.find_element(By.LINK_TEXT, "Shop Now →")
    # 4 | click | linkText=Home |
    self.driver.find_element(By.LINK_TEXT, "Home").click()
'''


def test_the_test_continues_after_a_failed_lookup_and_the_lookup_is_the_failed_step(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, fake_run, _ = run_fake(db_session, test_project, tmp_path, monkeypatch, CONTINUES)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]

    assert test["status"] == "FAILED" and fake_run.status == "FAILED"            # still failed at the end
    assert [(s["step_number"], s["action"], s["status"]) for s in test["steps"]] == [
        (1, "open", "PASS"), (2, "click", "PASS"), (3, "find", "FAIL"), (4, "click", "PASS"),   # step 4 ran after the failure
    ]
    assert test["failure"]["source_line_number"] == line_of(CONTINUES, 'element = self.driver.find_element')
    assert test["failure"]["exception_type"].endswith("NoSuchElementException")
    failed = [t for t in test["analysis"]["timeline"] if t["role"] == "failed"]
    assert [(t["step_number"], t["action"]) for t in failed] == [(3, "find")]    # one failed step, same as the steps list


def test_a_failed_assertion_is_reported_live_in_guard_mode_too(tmp_path, db_session, test_project, monkeypatch):
    source = HEADER + '''
  def test_assertion(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | assertText | id=title | Welcome-EXPECTED |
    assert self.driver.title == "Welcome-EXPECTED", "message-secret-9"
'''
    run_fake(db_session, test_project, tmp_path, monkeypatch, source)
    live = [e for e in events_of(tmp_path) if e["type"] == "step_failed"]
    assert [(e["action"], e["step_number"], e["error_category"]) for e in live] == [("assert", 2, "AssertionError")]
    assert live[0]["source_line"] == line_of(source, "assert self.driver.title")
    assert "message-secret-9" not in json.dumps(events_of(tmp_path)) and "Welcome-EXPECTED" not in json.dumps(events_of(tmp_path))
    steps = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    failed = [s for s in steps if s["status"] == "FAIL" and s["action"] != "TEST_FAILURE"]
    assert [(s["action"], s["error"], s["value"]) for s in failed] == [("assertText", "AssertionError()", "")]


# ---------------------------------------------------------------------------
# A typed secret in a sensitive field reaches no record, report, message or output
# ---------------------------------------------------------------------------

LOGIN = HEADER + '''
  def test_login(self):
    import creds
    # 1 | open | http://example.test/login?token=URLSECRET9 |
    self.driver.get("http://example.test/login?token=URLSECRET9")
    # 2 | type | id=email | ignored |
    self.driver.find_element(By.ID, "email").send_keys(creds.EMAIL)
    # 3 | type | id=password | ignored |
    self.driver.find_element(By.ID, "password").send_keys(creds.PASSWORD)
    # 4 | click | linkText=Home |
    raise RuntimeError("login rejected for " + creds.PASSWORD + " / " + creds.EMAIL)
'''
CREDS = f'PASSWORD = "{PASSWORD}"\nEMAIL = "{EMAIL}"\n'


def test_a_typed_password_is_withheld_from_steps_json_but_masked_everywhere(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_fake(db_session, test_project, tmp_path, monkeypatch, LOGIN, {"creds.py": CREDS})

    # steps.json holds no password (the incoming report work relies on that); the private file does
    steps_text = (tmp_path / "steps.json").read_text(encoding="utf-8")
    assert PASSWORD not in steps_text
    assert json.loads((tmp_path / "typed_values.json").read_text(encoding="utf-8")) == [PASSWORD]
    assert PASSWORD not in json.dumps(events_of(tmp_path))

    # ...and because the backend still knows it, it is masked wherever it could surface
    data = results_json(client, test_project, run, auth_headers)
    api_text = json.dumps(data)
    assert PASSWORD not in api_text
    assert "login rejected for" in api_text                                      # the message is there, the secret is not
    # a non-sensitive typed value keeps the existing contract: the real value is available to mask, then never shown
    assert EMAIL in steps_text and EMAIL not in api_text

    report = stored_report(db_session, run)
    assert PASSWORD not in report and EMAIL not in report and "URLSECRET9" not in report


def test_the_json_report_is_built_from_sanitized_facts(tmp_path):
    events = [{
        "nodeid": "t.py::test_x[admin-Hunter2Pw]", "step": 1, "action": "type", "target": "id=email",
        "value": "demo@example.test", "runtime_value": "demo@example.test", "status": "FAIL",
        "error": "RuntimeError('Hunter2Pw rejected for Hunter2Pw')", "url": "https://x.test/cb?token=ABC123&q=1",
        "title": "Welcome password=Hunter2Pw", "locator_type": "id", "locator_value": "email",
        "skip_reason": "Depends on Hunter2Pw",
    }, "not-a-dict"]
    meta = {"t.py::test_x[admin-Hunter2Pw]": {"defined_steps": [
        {"number": 1, "action": "type", "target": "id=email", "value": "demo@example.test"}]}, "__run__": {"continuationEnabled": True}}
    testcases = [{"nodeid": "t.py::test_x[admin-Hunter2Pw]", "method_name": "test_x[admin-Hunter2Pw]", "file_name": "t.py"}]

    tests, safe_events, safe_meta = rps._safe_report_inputs(testcases, events, meta, ("Hunter2Pw",))

    dumped = json.dumps([tests, safe_events, safe_meta])
    for secret in ("Hunter2Pw", "demo@example.test", "token=ABC123"):
        assert secret not in dumped, secret
    assert all("runtime_value" not in e for e in safe_events) and len(safe_events) == 1       # internal field dropped, junk skipped
    assert safe_events[0]["nodeid"] == tests[0]["nodeid"] == next(k for k in safe_meta if k != "__run__")   # still correlates
    assert safe_meta["__run__"] == {"continuationEnabled": True}


# ---------------------------------------------------------------------------
# A setup failure is a setup error, in the results and in the report
# ---------------------------------------------------------------------------

def test_unsupported_initialization_is_recorded_once_as_a_setup_error(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = execute_and_persist(db_session, test_project, tmp_path, {"tes(1).py": LEGACY_BAD}, monkeypatch)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    assert test["status"] == "ERROR" and test["steps"] == []                    # the marker is not a step
    steps = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    assert [s["action"] for s in steps] == ["TEST_SETUP_FAILURE"]               # recorded exactly once
    report = json.loads(stored_report(db_session, run))
    dumped = json.dumps(report)
    assert "fooBar" in dumped                                                   # the cause is in the report
    assert "SECRET-VALUE-1" not in dumped                                       # a capability's value never is
