"""Phase 5: descriptive, safe event metadata written by selenium_compat_runner.

A real pytest subprocess runs the runner (as conftest.py) against selenium's own
WebDriver/WebElement classes with the transport faked, so the real wrappers run. Each
scenario is one test in SAMPLE; the events.jsonl of the whole run is parsed once.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_screenshot_redaction import COMPAT

TYPED = "TYPED-VALUE-9f3"
PASSWORD = "TYPED-PASSWORD-123"
MESSAGE_SECRET = "message-secret-77"

SAMPLE = '''
import pytest
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.locator_converter import LocatorConverter
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class D(WebDriver):
    _is_remote = False
    def __init__(self):
        self.locator_converter = LocatorConverter()
    def execute(self, command, params=None):
        if command == Command.FIND_ELEMENT:
            if "missing" in str((params or {}).get("value")):  # the id locator arrives converted to css
                raise NoSuchElementException("no element: MESSAGE_SECRET")
            return {"value": E(self, {"tag": "div"})}  # what the real execute() returns: a WebElement
        if command == Command.FIND_ELEMENTS:
            return {"value": []}
        return {"value": None}
    def execute_script(self, script, *args):
        if args and isinstance(args[0], E):
            return args[0].facts
        return None
    def save_screenshot(self, path):
        open(path, "wb").write(b"png")
        return True


class E(WebElement):
    def __init__(self, parent, facts=None, locator=None):
        self._parent = parent
        self._id = "e"
        self.facts = facts
        if locator:
            self._selenium_compat_locator = locator
    @property
    def tag_name(self):
        return (self.facts or {}).get("tag", "input")
    def _execute(self, command, params=None):
        return {"value": None}


def test_named_button():
    d = D()
    E(d, {"tag": "button", "text": "Sign In"}, ("id", "login")).click()


def test_unnamed_anchor():
    E(D(), {"tag": "a"}).click()


def test_type_normal():
    d = D()
    E(d, {"tag": "input", "type": "email", "label": "Email address", "placeholder": "Enter your email"}, ("id", "email")).send_keys("TYPED-VALUE-9f3")


def test_type_password():
    d = D()
    E(d, {"tag": "input", "type": "password", "label": "Password", "placeholder": "Your password here", "id": "pw"}).send_keys("TYPED-PASSWORD-123")


def test_sensitive_by_name():
    E(D(), {"tag": "input", "type": "text", "name": "api_token", "label": "Access", "placeholder": "paste it"}).send_keys("TYPED-VALUE-9f3")


def test_clear():
    E(D(), {"tag": "input", "type": "text", "label": "Search"}, ("name", "q")).clear()


def test_missing_metadata():
    E(D()).click()


def test_unsafe_label_and_locator():
    facts = {"tag": "button", "text": "abcdefghijklmnopqrstuvwxyz0123456789", "label": "4111 1111 1111 1111", "title": "mail me@example.test"}
    E(D(), facts, ("xpath", "//a[@id='x']/../token=abc12345678901234567890123456789")).click()


def test_open_page():
    D().get("https://user:pw@example.test/reset/ABCDEFGHIJKLMNOPQRSTUV123/confirm?token=SECRETQ#frag")


def test_descriptions():
    d = D()
    # STEP: Open login page
    d.get("https://example.test/login")

    # STEP: First of two stacked comments
    # STEP: The closest comment wins
    E(d, {"tag": "button"}).click()

    # STEP:
    E(d, {"tag": "button"}).click()

    # STEP: password=hunter2 for the account
    E(d, {"tag": "button"}).click()

    E(d, {"tag": "button"}).click()

    # STEP: Multi-line statement
    E(d, {"tag": "input"}).send_keys(
        "x"
    )

    # STEP: Repeated description
    E(d, {"tag": "button"}).click()
    # STEP: Repeated description
    E(d, {"tag": "button"}).click()


def test_wait_passes():
    WebDriverWait(D(), 1).until(lambda driver: True)


def test_wait_condition_with_locator():
    WebDriverWait(D(), 3).until(EC.presence_of_element_located(("id", "go")))


def test_wait_times_out():
    with pytest.raises(TimeoutException):
        WebDriverWait(D(), 0.3, poll_frequency=0.05).until(lambda driver: False)


def test_find_missing():
    with pytest.raises(NoSuchElementException):
        D().find_element("id", "missing")


def test_assert_fails():
    D().get("https://example.test/")
    secret_expression = "MESSAGE_SECRET"
    assert secret_expression == "different", "MESSAGE_SECRET"


def test_window_size():
    D().set_window_size(1366, 768)
'''.replace("MESSAGE_SECRET", MESSAGE_SECRET)


@pytest.fixture(scope="module")
def run_events(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("meta_run")
    (tmp / "conftest.py").write_text(COMPAT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    env = os.environ.copy()
    env.update({"SELENIUM_COMPAT_RUN_DIR": str(tmp), "SELENIUM_COMPAT_SCREENSHOT_MODE": "failed",
                "SELENIUM_FIND_TIMEOUT": "0.3"})
    with open(tmp / "_out.txt", "w", encoding="utf-8") as out:
        proc = subprocess.run([sys.executable, "-m", "pytest", str(tmp), "-q", "-p", "no:cacheprovider", "--tb=short"],
                              cwd=str(tmp), env=env, stdout=out, stderr=subprocess.STDOUT, timeout=120)
    raw = (tmp / "events.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]
    by_test = {}
    for e in events:
        by_test.setdefault(e["nodeid"].split("::")[-1], []).append(e)
    output = (tmp / "_out.txt").read_text(encoding="utf-8", errors="replace")
    return SimpleNamespace(tmp=tmp, raw=raw, events=events, by_test=by_test, returncode=proc.returncode, output=output)


from types import SimpleNamespace  # noqa: E402


def steps(run_events, name, kind="step_started"):
    return [e for e in run_events.by_test[name] if e["type"] == kind]


def started(run_events, name):
    return steps(run_events, name)


def test_click_on_a_named_button(run_events):
    (start,) = started(run_events, "test_named_button")
    assert start["element_label"] == "Sign In" and start["element_role"] == "button" and start["target"] == "button"
    assert (start["locator_by"], start["locator_value"]) == ("id", "login")
    end = steps(run_events, "test_named_button", "step_passed")[0]
    assert end["step_id"] == start["step_id"] and end["element_label"] == "Sign In"   # same row, same facts


def test_click_on_an_unnamed_element_has_no_invented_label(run_events):
    (start,) = started(run_events, "test_unnamed_anchor")
    assert start["element_role"] == "link" and "element_label" not in start and "locator_by" not in start


def test_typing_into_a_normal_input_gives_label_and_hint_but_never_the_value(run_events):
    (start,) = started(run_events, "test_type_normal")
    assert start["element_label"] == "Email address" and start["element_hint"] == "Enter your email"
    assert start["element_role"] == "textbox" and "sensitive" not in start
    assert (start["locator_by"], start["locator_value"]) == ("id", "email")


def test_password_field_is_marked_sensitive_and_its_placeholder_is_dropped(run_events):
    (start,) = started(run_events, "test_type_password")
    assert start["sensitive"] is True and start["element_label"] == "Password"
    assert "element_hint" not in start
    assert (start["locator_by"], start["locator_value"]) == ("id", "pw")    # falls back to the element's id


def test_field_with_a_credential_like_name_is_sensitive(run_events):
    (start,) = started(run_events, "test_sensitive_by_name")
    assert start["sensitive"] is True and "element_hint" not in start and start["element_label"] == "Access"


def test_clearing_a_field(run_events):
    (start,) = started(run_events, "test_clear")
    assert start["action"] == "clear" and start["element_label"] == "Search"
    assert (start["locator_by"], start["locator_value"]) == ("name", "q")


def test_missing_element_metadata_keeps_the_phase_1_fallback(run_events):
    (start,) = started(run_events, "test_missing_metadata")
    assert start["action"] == "click" and start["target"] == "input"
    assert not {"element_role", "element_label", "element_hint", "sensitive", "locator_by"} & set(start)


def test_unsafe_labels_and_locators_are_dropped_not_shown(run_events):
    (start,) = started(run_events, "test_unsafe_label_and_locator")
    assert "element_label" not in start and "locator_value" not in start
    assert start["element_role"] == "button"


def test_open_shows_host_and_path_only(run_events):
    (start,) = started(run_events, "test_open_page")
    assert start["page"] == "example.test/reset/…/confirm"
    for secret in ("user", "pw@", "SECRETQ", "frag", "ABCDEFGHIJKLMNOPQRSTUV123"):
        assert secret not in json.dumps(start)


def test_step_descriptions(run_events):
    titles = [e.get("description") for e in started(run_events, "test_descriptions")]
    assert titles == [
        "Open login page",
        "The closest comment wins",     # stacked comments: only the nearest counts
        None,                           # malformed (empty) comment ignored
        None,                           # unsafe text dropped
        None,                           # no comment
        "Multi-line statement",         # the comment above a statement that spans lines
        "Repeated description",
        "Repeated description",
    ]


def test_descriptions_never_break_execution(run_events):
    assert [e["status"] for e in run_events.by_test["test_descriptions"] if e["type"] == "step_passed"] == ["PASSED"] * 8


def test_wait_that_passes(run_events):
    (start,) = started(run_events, "test_wait_passes")
    assert start["action"] == "wait" and start["wait_condition"] == "custom"
    assert start["wait_timeout"] == 1.0 and "wait_until_not" not in start
    assert steps(run_events, "test_wait_passes", "step_passed")[0]["step_id"] == start["step_id"]


def test_expected_condition_wait_reports_condition_and_locator_without_nested_steps(run_events):
    wait_steps = started(run_events, "test_wait_condition_with_locator")
    assert len(wait_steps) == 1                                  # the find_element inside is not a second step
    assert wait_steps[0]["wait_condition"] == "presence_of_element_located"
    assert (wait_steps[0]["locator_by"], wait_steps[0]["locator_value"]) == ("id", "go")


def test_wait_timeout_is_a_failed_step_with_only_the_class_name(run_events):
    (end,) = steps(run_events, "test_wait_times_out", "step_failed")
    assert end["action"] == "wait" and end["error_category"] == "TimeoutException"
    assert end["duration"] >= 0.25                                # the original timeout still applied


def test_element_not_found_is_one_failed_step(run_events):
    failures = steps(run_events, "test_find_missing", "step_failed")
    assert len(failures) == 1
    assert failures[0]["action"] == "find" and failures[0]["error_category"] == "NoSuchElementException"
    assert (failures[0]["locator_by"], failures[0]["locator_value"]) == ("id", "missing")
    assert "duration" not in failures[0] and not steps(run_events, "test_find_missing")


def test_failed_assertion_reports_only_its_line(run_events):
    failures = steps(run_events, "test_assert_fails", "step_failed")
    assert len(failures) == 1
    assert failures[0]["action"] == "assert" and failures[0]["error_category"] == "AssertionError"
    assert isinstance(failures[0]["source_line"], int) and failures[0]["source_line"] > 0
    assert failures[0]["step_index"] == 1                          # after the open step, never a duplicate


def test_window_size(run_events):
    (start,) = started(run_events, "test_window_size")
    assert start["window_size"] == "1366x768"


def test_identifiers_ordering_and_nothing_sensitive_anywhere(run_events):
    for name, events in run_events.by_test.items():
        indexes = [e["step_index"] for e in events if e["type"] == "step_started"]
        assert indexes == sorted(indexes) and len(set(indexes)) == len(indexes), name
        for e in events:
            if e["type"] in ("step_started", "step_passed", "step_failed"):
                assert e["step_id"] == f"{e['nodeid']}#{e['step_index']}"
    for secret in (TYPED, PASSWORD, MESSAGE_SECRET, "hunter2", "SECRETQ", "user:pw", "Traceback"):
        assert secret not in run_events.raw


def test_the_run_itself_behaved_normally(run_events):
    # exactly the two intentionally failing tests (assert, wait is caught by pytest.raises -> passes)
    assert run_events.returncode == 1
    steps_json = json.loads((run_events.tmp / "steps.json").read_text(encoding="utf-8"))
    assert set(steps_json[0]) == {"nodeid", "step", "action", "target", "value", "runtime_value",
                                  "status", "duration", "error", "screenshot", "timestamp"}
    assert "1 failed" in run_events.output
