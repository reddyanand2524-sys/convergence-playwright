"""The action that actually ends a test must appear as the failed step.

Reported bug (run 16079): an unmodified Selenium IDE script failed at

    # 4 | mouseOver | linkText=Shop Now -> |
    element = self.driver.find_element(By.LINK_TEXT, "Shop Now ->")        # NoSuchElementException

The failure analysis was right (exception, locator, line) but the stored steps stopped at
the last PASS: element-not-found, wait-timeout and assertion failures were reported only
to the live event stream, while stored results are built from steps.json. Now the failure is
remembered when it happens and written to steps.json if - and only if - that same exception
ends the test, so a lookup the test itself catches never becomes a false failed step.

Everything runs through the real engine path (real pytest subprocess, real persistence, the
results API); only the browser is faked.
"""
import json
import os

import pytest

from app.services import run_event_service as svc
from tests.test_run_result_capture import execute_and_persist, results_json

FAKES = '''
from pathlib import Path
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.locator_converter import LocatorConverter
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
import conftest


class E(WebElement):
    def __init__(self, parent):
        self._parent = parent
        self._id = "e"
    @property
    def tag_name(self):
        return "a"
    def _execute(self, command, params=None):
        return {"value": None}


class D(WebDriver):
    _is_remote = False
    def __init__(self):
        self.locator_converter = LocatorConverter()
    def execute(self, command, params=None):
        if command == Command.FIND_ELEMENT:
            value = str((params or {}).get("value", ""))
            if "Shop Now" in value or "missing" in value:
                raise NoSuchElementException("no such element: Unable to locate element: " + value)
            return {"value": E(self)}
        if command == Command.FIND_ELEMENTS:
            return {"value": []}
        return {"value": None}
    def execute_script(self, *a, **k):
        return None
    def save_screenshot(self, path):
        Path(path).write_bytes(b"png")
        return True
    def quit(self):
        pass


conftest._CTORS["selected"] = lambda *a, **k: D()
conftest._listening = lambda *a, **k: False
'''

# The fake browser is a module OUTSIDE the run directory (on PYTHONPATH), so - exactly as in a
# real run - the exception originates in library code and not in an uploaded file.
HEADER = '''
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.wait import WebDriverWait
import fakebrowser


class TestDefaultSuite():
  def setup_method(self, method):
    self.driver = webdriver.Remote(command_executor='http://localhost:4444/wd/hub', desired_capabilities=DesiredCapabilities.CHROME)

  def teardown_method(self, method):
    self.driver.quit()

'''

IDE_MOUSE_OVER = HEADER + '''
  def test_ana(self):
    # Test name: ana
    # Step # | name | target | value
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | setWindowSize | 1061x804 |
    self.driver.set_window_size(1061, 804)
    # 3 | click | linkText=Home |
    self.driver.find_element(By.LINK_TEXT, "Home").click()
    # 4 | mouseOver | linkText=Shop Now → |
    element = self.driver.find_element(By.LINK_TEXT, "Shop Now →")
    actions = ActionChains(self.driver)
    actions.move_to_element(element).perform()
'''


def line_of(source: str, needle: str) -> int:
    return next(i for i, text in enumerate(source.splitlines(), 1) if needle in text)


@pytest.fixture(autouse=True)
def _fast_lookups(monkeypatch):
    monkeypatch.setenv("SELENIUM_FIND_TIMEOUT", "0.3")          # the real retry loop, just shorter
    monkeypatch.setenv("SELENIUM_IMPLICIT_WAIT_SECONDS", "0")
    svc.reset_registry()


def run_script(db_session, project, tmp_path, monkeypatch, source, name="test_defaute.py"):
    fakes = tmp_path.parent / f"{tmp_path.name}_selenium_fakes"   # "selenium" in the path: library code, as in a real run
    fakes.mkdir(exist_ok=True)
    (fakes / "fakebrowser.py").write_text(FAKES, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", str(fakes) + (os.pathsep + existing if existing else ""))
    return execute_and_persist(db_session, project, tmp_path, {name: source}, monkeypatch)


def events_of(tmp_path):
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------------
# The reported scenario
# ---------------------------------------------------------------------------

def test_failing_lookup_before_a_mouse_over_is_the_failed_step_in_the_results(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, fake_run, _ = run_script(db_session, test_project, tmp_path, monkeypatch, IDE_MOUSE_OVER)
    data = results_json(client, test_project, run, auth_headers)
    (test,) = data["tests"]
    failing_line = line_of(IDE_MOUSE_OVER, 'element = self.driver.find_element(By.LINK_TEXT, "Shop Now')

    assert test["status"] == "FAILED"
    steps = test["steps"]
    # The step guard keeps going after a failed statement: the mouse-over that depends on the element
    # that was never found is recorded as SKIPPED (it did not run), not as passed.
    assert [(s["step_number"], s["action"], s["status"]) for s in steps] == [
        (1, "open", "PASS"), (2, "setWindowSize", "PASS"), (3, "click", "PASS"), (4, "find", "FAIL"),
        (4, "mouseOver", "SKIP"),
    ]
    failed = steps[3]
    assert "Shop Now" in failed["target"]                                  # the locator of the failing lookup
    assert failed["error"].startswith("NoSuchElementException: ")          # type and sanitized message
    assert "Unable to locate element" in failed["error"] and "Stacktrace" not in failed["error"]
    assert failed["screenshots"], "the failed step has its screenshot"

    # the failure diagnostic and the analysis name the same action
    assert test["failure"]["exception_type"].endswith("NoSuchElementException")
    assert test["failure"]["source_line_number"] == failing_line
    assert test["analysis"]["failure_category"] == "ELEMENT_NOT_FOUND"
    # the analysis' own timeline marks the very same step as the failed one
    failed_in_analysis = [t for t in test["analysis"]["timeline"] if t["role"] == "failed"]
    assert [(t["step_number"], t["action"]) for t in failed_in_analysis] == [(4, "find")]
    assert failed_in_analysis[0]["screenshot_ids"], "and the analysis can show that step's screenshot"


def test_the_live_event_for_the_same_failure_carries_step_number_line_and_locator(tmp_path, db_session, test_project, monkeypatch):
    run_script(db_session, test_project, tmp_path, monkeypatch, IDE_MOUSE_OVER)
    failing_line = line_of(IDE_MOUSE_OVER, 'element = self.driver.find_element(By.LINK_TEXT, "Shop Now')
    failed = [e for e in events_of(tmp_path) if e["type"] == "step_failed"]
    assert len(failed) == 1                                               # one failed step, not one per layer
    event = failed[0]
    assert (event["action"], event["step_number"], event["source_line"]) == ("find", 4, failing_line)
    assert (event["locator_by"], event["locator_value"]) == ("link text", "Shop Now →")
    assert event["error_category"] == "NoSuchElementException"

    # and the backend publishes it unchanged for the timeline
    path = tmp_path / "events.jsonl"
    svc.RunEventTailer(7, path).drain()
    public = [e for e in svc.get_buffer(7).read_after(0)[0] if e["type"] == "step_failed"]
    assert [(e["action"], e["step_number"], e["source_line"]) for e in public] == [("find", 4, failing_line)]


def test_selenium_behaviour_is_untouched_no_retry_skip_or_changed_exception(tmp_path, db_session, test_project, monkeypatch):
    run, fake_run, _ = run_script(db_session, test_project, tmp_path, monkeypatch, IDE_MOUSE_OVER)
    assert fake_run.status == "FAILED"                                    # the test still fails, honestly
    output = (tmp_path / "_pytest_stdout.log").read_text(encoding="utf-8", errors="replace")
    assert "NoSuchElementException" in output and "Unable to locate element" in output
    assert "ActionChains" not in [e.get("action") for e in events_of(tmp_path)]   # the mouse-over after it never ran
    steps = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    assert [s["status"] for s in steps if s["action"] != "TEST_FAILURE"] == ["PASS", "PASS", "PASS", "FAIL", "SKIP"]
    skipped = [s for s in steps if s["status"] == "SKIP"][0]
    assert skipped["action"] == "mouseOver" and "element" in skipped["skip_reason"]   # skipped, with its reason


# ---------------------------------------------------------------------------
# A lookup that fails but is handled by the test is not a failed step
# ---------------------------------------------------------------------------

CAUGHT = HEADER + '''
  def test_caught(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    try:
        self.driver.find_element(By.ID, "missing-banner")
    except NoSuchElementException:
        pass
    # 2 | click | linkText=Home |
    self.driver.find_element(By.LINK_TEXT, "Home").click()
'''

CAUGHT_THEN_OTHER_FAILURE = HEADER + '''
  def test_other(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    try:
        self.driver.find_element(By.ID, "missing-banner")
    except NoSuchElementException:
        pass
    raise RuntimeError("something else entirely")
'''


def test_a_lookup_the_test_catches_does_not_become_a_failed_step(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, CAUGHT)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    assert test["status"] == "PASSED"
    assert [s["status"] for s in test["steps"]] == ["PASS", "PASS"]
    # the live stream did see that lookup fail (it did), which is accurate and does not end the run
    assert any(e["type"] == "step_failed" and e["action"] == "find" for e in events_of(tmp_path))


def test_a_caught_lookup_is_not_blamed_for_a_later_unrelated_failure(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, CAUGHT_THEN_OTHER_FAILURE)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    assert test["status"] == "FAILED"
    # The plain statement that raised is attributed to its IDE step (1); the earlier lookup that the
    # test caught itself is not recorded as a failed step at all.
    assert [(s["action"], s["status"]) for s in test["steps"]] == [("open", "PASS"), ("open", "FAIL")]
    assert "find" not in [s["action"] for s in test["steps"]]
    assert test["steps"][-1]["error"].startswith("RuntimeError")
    assert test["failure"]["exception_type"].endswith("RuntimeError")


# ---------------------------------------------------------------------------
# Same agreement for the other failures the live timeline reports
# ---------------------------------------------------------------------------

PLAIN_SCRIPT = HEADER + '''
  def test_plain(self):
    self.driver.get("http://example.test/")
    self.driver.find_element(By.LINK_TEXT, "Shop Now →")
'''

WAIT_TIMEOUT = HEADER + '''
  def test_wait(self):
    self.driver.get("http://example.test/")
    WebDriverWait(self.driver, 0.3, poll_frequency=0.05).until(
        expected_conditions.visibility_of_element_located((By.ID, "missing-panel")))
'''

ASSERTION = HEADER + '''
  def test_assertion(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | assertText | id=title | Welcome-SECRET-EXPECTED |
    assert self.driver.title == "Welcome-SECRET-EXPECTED", "message-secret-9"
'''

TYPED_BEFORE_FAILURE = HEADER + '''
  def test_typed(self):
    # 1 | open | / |
    self.driver.get("http://example.test/")
    # 2 | type | id=missing-field | TYPED-SECRET-5 |
    field = self.driver.find_element(By.ID, "missing-field")
    field.send_keys("TYPED-SECRET-5")
'''


def test_without_ide_comments_the_failed_lookup_is_still_recorded_with_its_locator(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, PLAIN_SCRIPT)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    last = test["steps"][-1]
    assert (last["action"], last["status"], last["step_number"]) == ("find", "FAIL", None)
    assert "link text=Shop Now" in last["target"]
    assert test["failure"]["source_line_number"] == line_of(PLAIN_SCRIPT, "Shop Now")


def test_a_wait_that_times_out_is_the_failed_step(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, WAIT_TIMEOUT)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    assert [(s["action"], s["status"]) for s in test["steps"]] == [("open", "PASS"), ("wait", "FAIL")]
    assert test["steps"][-1]["target"] == "id=missing-panel" and "TimeoutException" in test["steps"][-1]["error"]
    live = [e for e in events_of(tmp_path) if e["type"] == "step_failed"]
    assert [e["action"] for e in live] == ["wait"]                      # the lookups inside the wait are not extra steps
    assert test["failure"]["exception_type"].endswith("TimeoutException")


def test_a_failed_assertion_is_a_failed_step_without_its_expression_or_message(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, ASSERTION)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    last = test["steps"][-1]
    # recorded under the Selenium IDE step it belongs to (its own action name), class name only
    assert (last["action"], last["status"], last["step_number"], last["error"]) == ("assertText", "FAIL", 2, "AssertionError()")
    assert last["value"] in (None, "")                                   # the IDE step's expected text is never stored
    steps_json = json.dumps(test["steps"]) + json.dumps(events_of(tmp_path))
    assert "message-secret-9" not in steps_json and "Welcome-SECRET-EXPECTED" not in steps_json


def test_typed_text_from_the_ide_step_is_never_stored_with_a_failed_lookup(client, db_session, test_project, auth_headers, tmp_path, monkeypatch):
    run, _, _ = run_script(db_session, test_project, tmp_path, monkeypatch, TYPED_BEFORE_FAILURE)
    (test,) = results_json(client, test_project, run, auth_headers)["tests"]
    last = [s for s in test["steps"] if s["status"] == "FAIL"][0]
    assert (last["action"], last["status"], last["step_number"]) == ("find", "FAIL", 2)
    assert last["value"] in (None, "")
    skipped = [s for s in test["steps"] if s["status"] == "SKIP"]            # the dependent type step did not run
    assert [(s["action"], s["step_number"]) for s in skipped] == [("type", 2)]
    assert skipped[0]["value"] in (None, "", "***")                          # and its typed text is masked
    persisted = (tmp_path / "steps.json").read_text(encoding="utf-8")
    failed_record = [s for s in json.loads(persisted) if s["status"] == "FAIL" and s["action"] == "find"][0]
    assert failed_record["value"] == "" and failed_record["runtime_value"] == ""
    assert "TYPED-SECRET-5" not in json.dumps(test["steps"]) + json.dumps(events_of(tmp_path))
