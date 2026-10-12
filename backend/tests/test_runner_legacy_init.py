"""Legacy Selenium initialization compatibility in selenium_compat_runner.

Selenium IDE exports such as
    webdriver.Remote(command_executor='http://localhost:4444/wd/hub',
                     desired_capabilities=DesiredCapabilities.CHROME)
fail on Selenium 4.10+ ("unexpected keyword argument 'desired_capabilities'"). The runner
adapts the call when it happens; the uploaded source is never rewritten.

Scenario tests run in a subprocess with the browser constructors replaced by recorders (no
browser, no network, no Grid). The end-to-end tests run real pytest with the runner as
conftest.py and an IDE-style legacy test class.
"""
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.test_screenshot_redaction import COMPAT

PRELUDE = r'''
import json, sys
from selenium import webdriver
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities as DC
from selenium.webdriver.chrome.options import Options as CO
from selenium.webdriver.firefox.options import Options as FO
from selenium.webdriver.edge.options import Options as EO

calls, out = [], {}

class Drv:
    def implicitly_wait(self, seconds):
        pass

def selected(*a, **k):
    calls.append(("selected", a, k)); return Drv()

def remote(*a, **k):
    calls.append(("remote", a, k)); return Drv()

mod._CTORS["selected"] = selected
mod._CTORS["remote"] = remote
state = {"listening": False}
mod._listening = lambda host, port, timeout=0.5: state["listening"]

def describe(value):
    if hasattr(value, "to_capabilities"):
        experimental = getattr(value, "experimental_options", None)
        return {"cls": type(value).__name__, "caps": value.to_capabilities(),
                "args": list(getattr(value, "arguments", [])),
                "prefs": (experimental or {}).get("prefs") if experimental is not None else getattr(value, "preferences", None)}
    return repr(value)

def attempt(name, fn):
    del calls[:]
    del mod._NOTICES[:]
    error = None
    try:
        fn()
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc),
                 "cause": type(exc.__cause__).__name__ if exc.__cause__ is not None else None}
    out[name] = {"error": error, "notices": list(mod._NOTICES),
                 "calls": [{"which": w, "args": [describe(x) for x in a], "kwargs": {k: describe(v) for k, v in kw.items()}}
                           for (w, a, kw) in calls]}
'''

SCENARIOS = r'''
URL = "http://localhost:4444/wd/hub"
RAW_CHROME, RAW_FIREFOX = dict(DC.CHROME), dict(DC.FIREFOX)

attempt("legacy_remote", lambda: webdriver.Remote(command_executor=URL, desired_capabilities=DC.CHROME))
attempt("legacy_remote_firefox_caps", lambda: webdriver.Remote(command_executor=URL, desired_capabilities=DC.FIREFOX))
attempt("legacy_remote_edge_caps", lambda: webdriver.Remote(command_executor=URL, desired_capabilities=DC.EDGE))
attempt("legacy_remote_positional", lambda: webdriver.Remote(URL, DC.CHROME))
attempt("legacy_local_ctor", lambda: webdriver.Chrome(desired_capabilities=DC.CHROME))
attempt("capabilities_keyword", lambda: webdriver.Remote(command_executor=URL, capabilities=DC.CHROME))

# modern Selenium 4: untouched
modern = CO(); modern.add_argument("--window-size=1,1")
attempt("modern_no_args", lambda: webdriver.Chrome())
attempt("modern_options", lambda: webdriver.Chrome(options=modern))
out["modern_options"]["same_object"] = bool(calls) and calls[0][2].get("options") is modern
remote_options = CO()
attempt("modern_remote", lambda: webdriver.Remote(command_executor="http://selenium:4444", options=remote_options))
out["modern_remote"]["same_object"] = bool(calls) and calls[0][2].get("options") is remote_options

# endpoint preservation
state["listening"] = True
attempt("grid_answers_on_default", lambda: webdriver.Remote(command_executor=URL, desired_capabilities=DC.CHROME))
state["listening"] = False
attempt("custom_endpoint", lambda: webdriver.Remote(command_executor="http://grid.internal:4444/wd/hub", desired_capabilities=DC.CHROME))
attempt("custom_endpoint_modern", lambda: webdriver.Remote(command_executor="http://selenium:4444/", options=CO()))
connection = object()
attempt("connection_object", lambda: webdriver.Remote(command_executor=connection, desired_capabilities=DC.CHROME))
out["connection_object"]["same_object"] = bool(calls) and calls[0][2].get("command_executor") is connection
attempt("no_executor_given", lambda: webdriver.Remote(desired_capabilities=DC.CHROME))

# browser selection consistency
attempt("caps_for_other_browser", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={"browserName": "SELECT_OTHER"}))
attempt("unsupported_browser_name", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={"browserName": "safari"}))

# vendor options
attempt("vendor_chrome", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={
    "browserName": "chrome", "goog:chromeOptions": {"args": ["--window-size=800,600", "--disable-gpu"], "prefs": {"a": 1}, "binary": "/x/chrome"}}))
attempt("vendor_firefox", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={
    "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"], "prefs": {"x": 1}}}))
attempt("vendor_edge", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={
    "browserName": "MicrosoftEdge", "ms:edgeOptions": {"args": ["--inprivate"]}}))
attempt("legacy_names_and_noops", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={
    "browserName": "chrome", "javascriptEnabled": True, "takesScreenshot": True, "acceptSslCerts": True, "platform": "ANY", "version": "120"}))

# aliases
legacy_opts = CO(); legacy_opts.add_argument("--legacy")
attempt("chrome_options_alias", lambda: webdriver.Chrome(chrome_options=legacy_opts))
out["chrome_options_alias"]["same_object"] = bool(calls) and calls[0][2].get("options") is legacy_opts

# conflicts and duplicates (names only, never values)
secure = CO(); secure.set_capability("acceptInsecureCerts", False)
attempt("conflict_with_options", lambda: webdriver.Remote(command_executor=URL, options=secure, desired_capabilities={"browserName": "chrome", "acceptInsecureCerts": True}))
same = CO(); same.set_capability("acceptInsecureCerts", True)
attempt("same_value_is_fine", lambda: webdriver.Remote(command_executor=URL, options=same, desired_capabilities={"browserName": "chrome", "acceptInsecureCerts": True}))
attempt("duplicate_alias", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={"browserName": "chrome", "acceptSslCerts": True, "acceptInsecureCerts": False}))
attempt("both_caps_keywords", lambda: webdriver.Remote(command_executor=URL, desired_capabilities=DC.CHROME, capabilities=DC.CHROME))
attempt("options_given_twice", lambda: webdriver.Chrome(chrome_options=CO(), options=CO()))
prefs_opts = CO(); prefs_opts.add_experimental_option("prefs", {"a": 1})
attempt("pref_conflict", lambda: webdriver.Remote(command_executor=URL, options=prefs_opts, desired_capabilities={"browserName": "chrome", "goog:chromeOptions": {"prefs": {"a": 2}}}))
attempt("caps_with_other_browsers_options", lambda: webdriver.Remote(command_executor=URL, options=OTHER_OPTIONS(), desired_capabilities=DC.CHROME))

# unsupported / invalid
attempt("executable_path_keyword", lambda: webdriver.Chrome(executable_path="/dev/chromedriver"))
attempt("executable_path_positional", lambda: webdriver.Chrome("/dev/chromedriver"))
attempt("service_args", lambda: webdriver.Chrome(service_args=["--x"]))
attempt("unknown_capability", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={"browserName": "chrome", "fooBar": "SECRET-VALUE-1"}))
attempt("caps_not_a_dict", lambda: webdriver.Remote(command_executor=URL, desired_capabilities="chrome"))
attempt("remote_extra_positionals", lambda: webdriver.Remote(URL, DC.CHROME, "profile"))
attempt("marionette_false", lambda: webdriver.Remote(command_executor=URL, desired_capabilities={"browserName": "firefox", "marionette": False}))

def signature_error(*a, **k):
    raise TypeError("__init__() got an unexpected keyword argument 'zzz'")
mod._CTORS["selected"] = signature_error
attempt("signature_typeerror_wrapped", lambda: webdriver.Chrome(zzz=1))
def startup_failure(*a, **k):
    raise RuntimeError("session not created: genuine start-up failure")
mod._CTORS["selected"] = startup_failure
attempt("genuine_failure_untouched", lambda: webdriver.Chrome())
def type_error_elsewhere(*a, **k):
    raise TypeError("'NoneType' object is not callable")
mod._CTORS["selected"] = type_error_elsewhere
attempt("unrelated_typeerror_untouched", lambda: webdriver.Chrome())

out["_unchanged"] = {"chrome": DC.CHROME == RAW_CHROME, "firefox": DC.FIREFOX == RAW_FIREFOX}
print(json.dumps(out))
'''


def run_scenarios(tmp_path: Path, browser: str) -> dict:
    other = {"chrome": "FO", "firefox": "CO", "edge": "CO"}[browser]
    script = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('compat_under_test', {str(COMPAT)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n" + PRELUDE + "\n"
        f"OTHER_OPTIONS = {other}\n" + SCENARIOS.replace("SELECT_OTHER", {"chrome": "firefox", "firefox": "chrome", "edge": "firefox"}[browser])
    )
    env = os.environ.copy()
    env.update({"SELENIUM_BROWSER": browser, "SELENIUM_COMPAT_RUN_DIR": str(tmp_path), "SELENIUM_COMPAT_SCREENSHOT_MODE": "none"})
    out_path, err_path = tmp_path / "_out.txt", tmp_path / "_err.txt"
    with open(out_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
        completed = subprocess.run([sys.executable, "-c", script], stdout=out, stderr=err, timeout=120, env=env)
    stdout = out_path.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, err_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    return json.loads(stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def chrome(tmp_path_factory):
    return run_scenarios(tmp_path_factory.mktemp("compat_chrome"), "chrome")


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    return run_scenarios(tmp_path_factory.mktemp("compat_firefox"), "firefox")


@pytest.fixture(scope="module")
def edge(tmp_path_factory):
    return run_scenarios(tmp_path_factory.mktemp("compat_edge"), "edge")


def only_call(result):
    assert result["error"] is None, result["error"]
    assert len(result["calls"]) == 1, result["calls"]
    return result["calls"][0]


# ---------------------------------------------------------------------------
# Legacy desired_capabilities -> Options of the Run's browser
# ---------------------------------------------------------------------------

def test_legacy_chrome_capabilities_start_the_runs_chrome_with_chrome_options(chrome):
    for name in ("legacy_remote", "legacy_remote_positional", "capabilities_keyword", "no_executor_given"):
        call = only_call(chrome[name])
        assert call["which"] == "selected", name                 # no Grid answers: the Run's browser, locally
        assert call["kwargs"]["options"]["cls"] == "Options"
        assert call["kwargs"]["options"]["caps"]["browserName"] == "chrome"
        assert "desired_capabilities" not in call["kwargs"] and "capabilities" not in call["kwargs"]
        assert "command_executor" not in call["kwargs"]
    assert any("No Selenium server answers" in n for n in chrome["legacy_remote"]["notices"])


def test_legacy_firefox_capabilities_build_firefox_options(firefox):
    call = only_call(firefox["legacy_remote_firefox_caps"])
    caps = call["kwargs"]["options"]["caps"]
    assert caps["browserName"] == "firefox" and caps["acceptInsecureCerts"] is True
    assert caps.get("moz:debuggerAddress") is True               # Selenium's own FIREFOX defaults carry over
    assert firefox["legacy_remote_firefox_caps"]["notices"][0].startswith("No Selenium server")


def test_legacy_edge_capabilities_build_edge_options(edge):
    call = only_call(edge["legacy_remote_edge_caps"])
    assert call["kwargs"]["options"]["caps"]["browserName"] == "MicrosoftEdge"


def test_legacy_capabilities_on_a_local_constructor(chrome):
    call = only_call(chrome["legacy_local_ctor"])
    assert call["which"] == "selected" and call["kwargs"]["options"]["caps"]["browserName"] == "chrome"


def test_vendor_options_are_applied_to_the_matching_browser(chrome, firefox, edge):
    c = only_call(chrome["vendor_chrome"])["kwargs"]["options"]
    assert "--window-size=800,600" in c["args"] and "--disable-gpu" in c["args"] and c["prefs"] == {"a": 1}
    assert any("refers to files" in n for n in chrome["vendor_chrome"]["notices"])      # binary path: not applied, said so
    f = only_call(firefox["vendor_firefox"])["kwargs"]["options"]
    assert "-headless" in f["args"] and f["prefs"]["x"] == 1      # (Firefox Options has its own default prefs too)
    e = only_call(edge["vendor_edge"])["kwargs"]["options"]
    assert "--inprivate" in e["args"]


def test_legacy_aliases_and_noop_capabilities(chrome):
    result = chrome["legacy_names_and_noops"]
    caps = only_call(result)["kwargs"]["options"]["caps"]
    assert caps["acceptInsecureCerts"] is True and caps["browserVersion"] == "120"      # acceptSslCerts, version
    assert "platformName" not in caps                                                     # platform=ANY means nothing
    assert any("javascriptEnabled" in n for n in result["notices"])


def test_the_scripts_own_capability_dicts_are_not_mutated(chrome):
    assert chrome["_unchanged"] == {"chrome": True, "firefox": True}


# ---------------------------------------------------------------------------
# Modern Selenium 4 initialization is left alone
# ---------------------------------------------------------------------------

def test_modern_constructor_calls_pass_through_unchanged(chrome):
    assert only_call(chrome["modern_no_args"]) == {"which": "selected", "args": [], "kwargs": {}}
    call = only_call(chrome["modern_options"])
    assert call["kwargs"].keys() == {"options"} and chrome["modern_options"]["same_object"]   # the very same Options object
    assert chrome["modern_options"]["notices"] == []


def test_chrome_options_keyword_becomes_options(chrome):
    call = only_call(chrome["chrome_options_alias"])
    assert call["kwargs"].keys() == {"options"} and chrome["chrome_options_alias"]["same_object"]


# ---------------------------------------------------------------------------
# Remote endpoints are preserved
# ---------------------------------------------------------------------------

def test_a_grid_that_answers_on_the_ide_default_endpoint_is_used_as_given(chrome):
    call = only_call(chrome["grid_answers_on_default"])
    assert call["which"] == "remote"
    assert call["kwargs"]["command_executor"] == "'http://localhost:4444/wd/hub'"       # not normalized, not replaced
    assert call["kwargs"]["options"]["caps"]["browserName"] == "chrome"


def test_other_endpoints_are_never_probed_or_replaced(chrome):
    for name, expected in (("custom_endpoint", "'http://grid.internal:4444/wd/hub'"),
                           ("custom_endpoint_modern", "'http://selenium:4444/'")):
        call = only_call(chrome[name])
        assert call["which"] == "remote" and call["kwargs"]["command_executor"] == expected
        assert chrome[name]["notices"] == []
    call = only_call(chrome["modern_remote"])
    assert call["which"] == "remote" and chrome["modern_remote"]["same_object"]
    assert chrome["connection_object"]["same_object"] and only_call(chrome["connection_object"])["which"] == "remote"


# ---------------------------------------------------------------------------
# Browser selection consistency: the Run's browser decides, and a mismatch is said out loud
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture,browser,other", [("chrome", "chrome", "firefox"), ("firefox", "firefox", "chrome"), ("edge", "edge", "firefox")])
def test_capabilities_for_another_browser_never_change_the_runs_browser(request, fixture, browser, other):
    result = request.getfixturevalue(fixture)["caps_for_other_browser"]
    call = only_call(result)
    expected = {"chrome": "chrome", "firefox": "firefox", "edge": "MicrosoftEdge"}[browser]
    assert call["kwargs"]["options"]["caps"]["browserName"] == expected
    assert any(other in n and browser in n for n in result["notices"])


def test_vendor_options_of_another_browser_are_not_applied(firefox):
    # the chrome-only goog:chromeOptions in a script must not leak into a Firefox Run
    assert firefox["caps_for_other_browser"]["error"] is None
    assert firefox["vendor_chrome"]["error"] is None
    assert any("goog:chromeOptions" in n for n in firefox["vendor_chrome"]["notices"])
    assert only_call(firefox["vendor_chrome"])["kwargs"]["options"]["args"] == []


def test_an_unsupported_browser_name_is_a_clear_setup_error(chrome):
    error = chrome["unsupported_browser_name"]["error"]
    assert error["type"] == "ConvergenceCompatibilityError" and "safari" in error["message"]
    assert chrome["unsupported_browser_name"]["calls"] == []


# ---------------------------------------------------------------------------
# Conflicts and duplicates are explicit errors (names only, never values)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,needle", [
    ("conflict_with_options", "acceptInsecureCerts"),
    ("duplicate_alias", "acceptInsecureCerts"),
    ("both_caps_keywords", "desired_capabilities and capabilities"),
    ("options_given_twice", "more than once"),
    ("pref_conflict", "preference 'a'"),
    ("caps_with_other_browsers_options", "different browser"),
])
def test_conflicting_or_duplicate_configuration_is_reported_not_overridden(chrome, name, needle):
    result = chrome[name]
    assert result["error"]["type"] == "ConvergenceCompatibilityError", result
    assert needle in result["error"]["message"]
    assert result["calls"] == []                                  # nothing was started


def test_identical_duplicate_values_are_not_a_conflict(chrome):
    assert only_call(chrome["same_value_is_fine"])["kwargs"]["options"]["caps"]["acceptInsecureCerts"] is True


# ---------------------------------------------------------------------------
# Unsupported initialization: actionable diagnostics
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,needle", [
    ("executable_path_keyword", "executable_path"),
    ("executable_path_positional", "driver path"),
    ("service_args", "service_args"),
    ("unknown_capability", "fooBar"),
    ("caps_not_a_dict", "must be a dict"),
    ("remote_extra_positionals", "positional arguments"),
    ("marionette_false", "marionette"),
])
def test_unsupported_initialization_gets_a_clear_message(chrome, name, needle):
    result = chrome[name]
    error = result["error"]
    assert error["type"] == "ConvergenceCompatibilityError", result
    assert needle in error["message"] and "(script line" in error["message"]
    assert "SECRET-VALUE-1" not in error["message"]                # a capability's value is never echoed
    assert result["calls"] == []


def test_a_changed_selenium_signature_is_wrapped_with_the_original_error(chrome):
    error = chrome["signature_typeerror_wrapped"]["error"]
    assert error["type"] == "ConvergenceCompatibilityError" and error["cause"] == "TypeError"
    assert "unexpected keyword argument 'zzz'" in error["message"] and "Selenium 4" in error["message"]


def test_genuine_failures_are_not_disguised_as_compatibility_errors(chrome):
    assert chrome["genuine_failure_untouched"]["error"]["type"] == "RuntimeError"
    assert chrome["unrelated_typeerror_untouched"]["error"]["type"] == "TypeError"


# ---------------------------------------------------------------------------
# End to end: real pytest, the runner as conftest.py, an IDE-style legacy script
# ---------------------------------------------------------------------------

FAKE_DRIVER = '''
from pathlib import Path
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.locator_converter import LocatorConverter
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
import conftest

HERE = Path(__file__).parent


class E(WebElement):
    def __init__(self, parent):
        self._parent = parent
        self._id = "e"
    @property
    def tag_name(self):
        return "button"
    def _execute(self, command, params=None):
        return {"value": None}


class D(WebDriver):
    _is_remote = False
    def __init__(self, options=None):
        self.locator_converter = LocatorConverter()
        self.options_seen = options
    def execute(self, command, params=None):
        if command == Command.FIND_ELEMENT:
            return {"value": E(self)}
        return {"value": None}
    def execute_script(self, *a, **k):
        return None
    def save_screenshot(self, path):
        Path(path).write_bytes(b"png")
        return True
    def quit(self):
        (HERE / "quit.marker").write_text("quit")


def fake_browser(*args, **kwargs):
    return D(kwargs.get("options"))


conftest._CTORS["selected"] = fake_browser
conftest._listening = lambda *a, **k: False
'''

LEGACY_OK = FAKE_DRIVER + '''
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities


class TestDefaute():
    def setup_method(self, method):
        self.driver = webdriver.Remote(command_executor='http://localhost:4444/wd/hub', desired_capabilities=DesiredCapabilities.CHROME)
        self.vars = {}

    def teardown_method(self, method):
        self.driver.quit()

    def test_defaute(self):
        # 1 | open | /login |
        self.driver.get("http://example.test/login")
        # 2 | click | id=login |
        self.driver.find_element(By.ID, "login").click()
        (HERE / "options.marker").write_text(type(self.driver.options_seen).__name__)

    def test_fails_later(self):
        self.driver.get("http://example.test/login")
        assert False, "a genuine test failure"
'''

LEGACY_BAD = FAKE_DRIVER + '''
from selenium import webdriver


class TestBad():
    def setup_method(self, method):
        self.driver = webdriver.Remote(command_executor='http://localhost:4444/wd/hub', desired_capabilities={"browserName": "chrome", "fooBar": "SECRET-VALUE-1"})

    def teardown_method(self, method):
        self.driver.quit()

    def test_never_runs(self):
        self.driver.get("http://example.test/")
'''


def run_pytest(tmp_path: Path, name: str, source: str, browser: str = "chrome"):
    (tmp_path / "conftest.py").write_text(COMPAT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / name).write_text(source, encoding="utf-8")
    before = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()   # the file exactly as uploaded
    env = os.environ.copy()
    env.update({"SELENIUM_BROWSER": browser, "SELENIUM_COMPAT_RUN_DIR": str(tmp_path), "SELENIUM_COMPAT_SCREENSHOT_MODE": "failed"})
    junit = tmp_path / "junit.xml"
    with open(tmp_path / "_out.txt", "w", encoding="utf-8") as out:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(tmp_path / name), "-q", "-p", "no:cacheprovider", "--tb=short", f"--junitxml={junit}"],
            cwd=str(tmp_path), env=env, stdout=out, stderr=subprocess.STDOUT, timeout=120,
        )
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    after = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    assert after == before, "the uploaded source file was modified by the run"
    return proc.returncode, (tmp_path / "_out.txt").read_text(encoding="utf-8", errors="replace"), events, junit


def test_ide_style_legacy_script_runs_unmodified_and_keeps_steps_failures_and_teardown(tmp_path):
    source_path = tmp_path / "test_defaute.py"
    returncode, output, events, junit = run_pytest(tmp_path, "test_defaute.py", LEGACY_OK)   # asserts the hash is unchanged

    # the uploaded source is what was uploaded: it still says desired_capabilities (nothing was rewritten)
    assert b"desired_capabilities=DesiredCapabilities.CHROME" in source_path.read_bytes()
    assert not [p.name for p in tmp_path.glob("*.py") if p.name not in ("conftest.py", "test_defaute.py")]   # no transformed copy

    # setup worked: the test body ran, with Options built from the legacy capabilities
    assert (tmp_path / "options.marker").read_text() == "Options"
    assert "TypeError" not in output and "desired_capabilities" not in output.split("Traceback")[0]

    # teardown ran for the tests that started a browser
    assert (tmp_path / "quit.marker").exists()

    # step events and steps.json still work, with the IDE comment numbers
    ok = [e for e in events if e["nodeid"].endswith("::TestDefaute::test_defaute")]
    assert [e["type"] for e in ok if e["type"].startswith("step_")] == ["step_started", "step_passed"] * 2
    assert [e["action"] for e in ok if e["type"] == "step_started"] == ["open", "click"]
    steps = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    assert [s["step"] for s in steps if s["nodeid"].endswith("::test_defaute")] == [1, 2]

    # a genuine failure after a successful setup is a test FAILURE, not a setup error
    assert returncode == 1
    cases = {c.get("name"): c for c in ET.parse(junit).getroot().iter("testcase")}
    assert cases["test_defaute"].find("failure") is None and cases["test_defaute"].find("error") is None
    assert cases["test_fails_later"].find("failure") is not None and cases["test_fails_later"].find("error") is None
    assert any(e["type"] == "step_failed" and e["action"] == "assert" for e in events)
    assert not any(e.get("action") == "setup" for e in events)


def test_unsupported_initialization_is_a_setup_error_with_location_and_original_context(tmp_path):
    returncode, output, events, junit = run_pytest(tmp_path, "test_bad.py", LEGACY_BAD)
    line = next(i for i, text in enumerate(LEGACY_BAD.splitlines(), 1) if "fooBar" in text)

    assert returncode == 1
    case = next(ET.parse(junit).getroot().iter("testcase"))
    assert case.find("error") is not None and case.find("failure") is None       # ERROR at setup, not a failed assertion
    assert "ConvergenceCompatibilityError" in output and "fooBar" in output
    assert f"test_bad.py:{line}" in output                                        # the script line that made the call
    # pytest itself prints the failing source line; what the runner reports must not echo a capability's value
    persisted_message = case.find("error").get("message", "") + (case.find("error").text or "").split("def setup_method")[0]
    assert "ConvergenceCompatibilityError" in persisted_message and "SECRET-VALUE-1" not in case.find("error").get("message", "")
    assert not (tmp_path / "quit.marker").exists()                                # no browser was ever started

    failed = [e for e in events if e["type"] == "step_failed"]
    assert [(e["action"], e["error_category"]) for e in failed] == [("setup", "ConvergenceCompatibilityError")]
    assert events[-1]["type"] == "test_finished" and events[-1]["status"] == "FAILED"
    assert "SECRET-VALUE-1" not in json.dumps(events)


def test_original_exception_for_the_reported_failure_is_the_real_selenium_one(tmp_path):
    # The scenario from the bug report: unmodified Selenium rejects the keyword. Proves the fixture
    # reproduces the original failure, so the passing test above is meaningful.
    code = (
        "from selenium import webdriver\n"
        "from selenium.webdriver.common.desired_capabilities import DesiredCapabilities\n"
        "try:\n"
        "    webdriver.Remote(command_executor='http://localhost:4444/wd/hub', desired_capabilities=DesiredCapabilities.CHROME)\n"
        "except TypeError as e:\n"
        "    print(e)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert "unexpected keyword argument 'desired_capabilities'" in done.stdout
