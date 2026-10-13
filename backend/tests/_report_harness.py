"""Shared helper for the execution-report tests: runs a Selenium-IDE-style
test file through a REAL pytest subprocess with the engine's generated
conftest.py (selenium_compat_runner.py) and a fake in-memory WebDriver -- no
browser, network or database -- then builds the JSON report from the very
files the engine would persist."""

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

from app.services.execution_report import build_execution_report

COMPAT = Path(__file__).parent.parent / "app" / "services" / "selenium_compat_runner.py"

FAKE_DRIVER = '''
import os
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.webdriver import LocatorConverter, WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.common.exceptions import NoSuchElementException, SessionNotCreatedException


class FakeDriver(WebDriver):
    """In-memory WebDriver: ids containing "missing" do not exist."""

    def __init__(self):
        import conftest
        self.locator_converter = LocatorConverter()
        self._is_remote = False
        self.url = None
        self.session_id = "fake"
        conftest.CUR.driver = self

    def execute(self, command, params=None):
        if command == Command.GET:
            self.url = params["url"]
            return {"value": None}
        if command == Command.GET_CURRENT_URL:
            return {"value": self.url}
        if command == Command.GET_TITLE:
            return {"value": "Title of " + str(self.url)}
        if command == Command.FIND_ELEMENT:
            if "missing" in params["value"]:
                raise NoSuchElementException("no such element: " + params["value"])
            return {"value": WebElement(self, params["value"])}
        return {"value": None}

    def execute_script(self, *a, **k):
        return None

    def save_screenshot(self, path):
        if os.environ.get("FAKE_SHOT_FAIL") == "1":
            raise OSError("disk full")
        open(path, "wb").write(b"\\x89PNG\\r\\n\\x1a\\n")
        return True

    def quit(self):
        pass
'''


def run_scenario(tmp_path: Path, test_body: str, *, env: dict | None = None,
                 setup: str = "self.driver = FakeDriver()", run_id: int = 1):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "conftest.py").write_text(COMPAT.read_text(encoding="utf-8"), encoding="utf-8")
    source = (
        FAKE_DRIVER
        + "\n\nclass TestScenario:\n    def setup_method(self, method):\n        "
        + setup
        + "\n\n    def test_scenario(self):\n"
        + "".join("        " + line + "\n" for line in test_body.strip("\n").splitlines())
    )
    (tmp_path / "test_scenario.py").write_text(source, encoding="utf-8")

    child_env = os.environ.copy()
    child_env.update({
        "SELENIUM_COMPAT_RUN_DIR": str(tmp_path),
        "SELENIUM_COMPAT_SCREENSHOT_DIR": str(tmp_path / "screenshots"),
        "SELENIUM_COMPAT_SCREENSHOT_MODE": "all",
        "SELENIUM_FIND_TIMEOUT": "0.3",
    })
    child_env.update(env or {})
    junit = tmp_path / "junit.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=tmp_path, env=child_env, capture_output=True, text=True, timeout=120,
    )

    steps_file = tmp_path / "steps.json"
    meta_file = tmp_path / "steps_meta.json"
    events = json.loads(steps_file.read_text(encoding="utf-8")) if steps_file.exists() else []
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    shots_dir = tmp_path / "screenshots"
    shots = {p.name for p in shots_dir.glob("*.png")} if shots_dir.exists() else set()
    report = build_execution_report(
        run_id=run_id, test_results=parse_junit(junit), events=events, meta=meta,
        available_screenshots=shots, browser="chrome", exit_code=proc.returncode,
    )
    return report, events, shots, proc


def parse_junit(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for tc in ET.parse(path).getroot().iter("testcase"):
        status = "PASSED"
        if tc.find("skipped") is not None:
            status = "SKIPPED"
        elif tc.find("failure") is not None:
            status = "FAILED"
        elif tc.find("error") is not None:
            status = "ERROR"
        name = tc.get("name")
        cls = (tc.get("classname") or "").split(".")[-1]
        out.append({
            "nodeid": f"test_scenario.py::{cls}::{name}", "file_name": "test_scenario.py",
            "class_name": cls, "method_name": name, "status": status, "duration": float(tc.get("time") or 0),
        })
    return out
