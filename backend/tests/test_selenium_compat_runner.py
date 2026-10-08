

import json
import subprocess
import sys
from pathlib import Path

import pytest


COMPAT_MODULE_PATH = Path(__file__).parent.parent / "app" / "services" / "selenium_compat_runner.py"


def _run_script(script: str, env_overrides: dict[str, str] | None = None) -> dict:
    import os

    env = os.environ.copy()
    env.update(env_overrides or {})

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )

    assert result.returncode == 0, (
        f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    return json.loads(result.stdout.strip().splitlines()[-1])


def test_compat_module_import_has_no_syntax_or_import_errors():
    script = f"""
import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
import json
print(json.dumps({{"ok": True}}))
"""
    result = _run_script(script)
    assert result == {"ok": True}


def test_browser_constructor_patching_redirects_every_constructor_to_selected_browser():
    """A Selenium IDE file hardcoding webdriver.Chrome() must open the
    Run's selected browser (Edge here), not Chrome — this is the proven
    compatibility behavior ported from the reference implementation."""

    script = f"""
import json
import selenium.webdriver as webdriver

class _FakeDriverBase:
    def __init__(self, name):
        self.name = name
        self.waits = []

    def implicitly_wait(self, seconds):
        self.waits.append(seconds)


def _make_fake(name):
    class _Fake(_FakeDriverBase):
        def __init__(self, *a, **k):
            super().__init__(name)
    _Fake.__name__ = name
    return _Fake


# Replace the real constructors with fakes BEFORE the compat module
# captures "original" constructors, so no real browser is ever launched.
webdriver.Chrome = _make_fake("Chrome")
webdriver.Edge = _make_fake("Edge")
webdriver.Firefox = _make_fake("Firefox")

import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Uploaded Selenium IDE source always calls webdriver.Chrome().
driver = webdriver.Chrome()
print(json.dumps({{"driver_name": driver.name, "implicit_waits": driver.waits}}))
"""
    result = _run_script(script, env_overrides={"SELENIUM_BROWSER": "edge"})

    assert result["driver_name"] == "Edge"
    assert result["implicit_waits"] == [10.0]


def test_browser_constructor_patching_defaults_to_chrome_when_unset():
    script = f"""
import json
import selenium.webdriver as webdriver

class _FakeDriverBase:
    def __init__(self, name):
        self.name = name
        self.waits = []

    def implicitly_wait(self, seconds):
        self.waits.append(seconds)


def _make_fake(name):
    class _Fake(_FakeDriverBase):
        def __init__(self, *a, **k):
            super().__init__(name)
    _Fake.__name__ = name
    return _Fake


webdriver.Chrome = _make_fake("Chrome")
webdriver.Edge = _make_fake("Edge")
webdriver.Firefox = _make_fake("Firefox")

import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

driver = webdriver.Firefox()
print(json.dumps({{"driver_name": driver.name}}))
"""
    result = _run_script(script, env_overrides={})
    assert result["driver_name"] == "Chrome"


def test_actionchains_legacy_move_to_element_with_offset_compatibility():
    """Selenium IDE's older Python export emits
    actions.move_to_element(element, x, y); modern Selenium only exposes
    that as move_to_element_with_offset. Both call styles must work
    without editing the uploaded test."""

    script = f"""
import json
import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

calls = []

# _compatible_move_to_element dispatches to the module's captured
# "original" ActionChains methods, not to methods on the passed-in chain —
# replace those captured originals to observe the dispatch decision itself.
mod._original_move_to_element = lambda self, element: calls.append(("plain", element))
mod._original_move_to_element_with_offset = lambda self, element, xoffset, yoffset: calls.append(
    ("with_offset", element, xoffset, yoffset)
)

mod._compatible_move_to_element(object(), "my-element", 10, 20)
print(json.dumps({{"calls": calls}}))
"""
    result = _run_script(script)
    assert result["calls"] == [["with_offset", "my-element", 10, 20]]


def test_actionchains_modern_single_argument_call_is_unaffected():
    script = f"""
import json
import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

calls = []

mod._original_move_to_element = lambda self, element: calls.append(("plain", element))
mod._original_move_to_element_with_offset = lambda self, element, xoffset, yoffset: calls.append(
    ("with_offset", element, xoffset, yoffset)
)

mod._compatible_move_to_element(object(), "my-element")
print(json.dumps({{"calls": calls}}))
"""
    result = _run_script(script)
    assert result["calls"] == [["plain", "my-element"]]


def test_step_comment_parsing_matches_selenium_ide_export_format(tmp_path: Path):
    """Selenium IDE's Python export writes
    "# N | action | target | value" immediately above each command. The
    step-correlation layer must read that format from the uploaded file
    as-is, without requiring any edits to it."""

    uploaded_test = tmp_path / "test_corlink.py"
    uploaded_test.write_text(
        "class TestDefaultSuite:\n"
        "    def test_corlink(self):\n"
        "        # 1 | open | / | \n"
        "        self.driver.get('https://example.com')\n"
        "        # 2 | click | id=name | \n"
        "        self.driver.find_element('id', 'name').click()\n",
        encoding="utf-8",
    )

    script = f"""
import json
import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

steps = mod._load_step_comments({str(uploaded_test)!r})
print(json.dumps({{"steps": steps}}))
"""
    result = _run_script(script)

    assert result["steps"] == [
        {"line": 3, "number": 1, "action": "open", "target": "/", "value": ""},
        {"line": 5, "number": 2, "action": "click", "target": "id=name", "value": ""},
    ]


def test_screenshot_modes_control_whether_a_screenshot_is_attempted(tmp_path: Path):
    """mode=none must never call save_screenshot; mode=all/failed must."""

    script_template = f"""
import json
import importlib.util
spec = importlib.util.spec_from_file_location("compat_under_test", {str(COMPAT_MODULE_PATH)!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FakeDriver:
    def __init__(self):
        self.saved = []
    def save_screenshot(self, path):
        self.saved.append(path)

driver = FakeDriver()
result = mod.shot(driver, "some_step")
print(json.dumps({{"result": result, "saved_count": len(driver.saved)}}))
"""

    none_result = _run_script(
        script_template,
        env_overrides={"SELENIUM_COMPAT_SCREENSHOT_MODE": "none", "SELENIUM_COMPAT_RUN_DIR": str(tmp_path)},
    )
    assert none_result == {"result": "", "saved_count": 0}

    failed_result = _run_script(
        script_template,
        env_overrides={"SELENIUM_COMPAT_SCREENSHOT_MODE": "failed", "SELENIUM_COMPAT_RUN_DIR": str(tmp_path)},
    )
    assert failed_result["saved_count"] == 1
    assert failed_result["result"] != ""

    all_result = _run_script(
        script_template,
        env_overrides={"SELENIUM_COMPAT_SCREENSHOT_MODE": "all", "SELENIUM_COMPAT_RUN_DIR": str(tmp_path)},
    )
    assert all_result["saved_count"] == 1
