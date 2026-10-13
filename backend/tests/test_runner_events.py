"""Live execution events (events.jsonl) written by selenium_compat_runner.

The compat module patches selenium globally, so everything runs in a subprocess
with the module installed as conftest.py, exactly as execution_service does. No
browser or website is involved: the "driver" and "element" are selenium's own
WebDriver / WebElement classes with the transport replaced, so the real wrappers
(_wrap, _resilient_click) run unchanged.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from tests.test_screenshot_redaction import COMPAT, run_script

TYPED = "TYPED-PASSWORD-123"
URL_SECRET = "URLTOKEN-777"
ERROR_TEXT = "boom-secret-text"

SAMPLE_TEST = f'''
import os, time
from pathlib import Path
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

HERE = Path(__file__).parent


class D(WebDriver):
    _is_remote = False
    def __init__(self):
        pass
    def execute(self, command, params=None):
        return {{"value": None}}
    def execute_script(self, *args, **kwargs):
        return None
    def save_screenshot(self, path):
        Path(path).write_bytes(b"png")
        return True


class E(WebElement):
    def __init__(self, parent, fail_click=False):
        self._parent = parent
        self._id = "e"
        self.fail_click = fail_click
    @property
    def tag_name(self):
        return "input"
    def _execute(self, command, params=None):
        if self.fail_click and command == Command.CLICK_ELEMENT:
            raise RuntimeError("{ERROR_TEXT} {TYPED}")
        return {{"value": None}}


def test_ok():
    d = D()
    d.get("https://example.test/login?token={URL_SECRET}")
    e = E(d)
    e.send_keys("{TYPED}")
    e.click()
    (HERE / "marker").write_text("done")           # the parent reads events.jsonl now
    deadline = time.time() + 60
    while not (HERE / "go").exists() and time.time() < deadline:
        time.sleep(0.05)


def test_fails():
    d = D()
    d.get("https://example.test/next")
    E(d, fail_click=True).click()
'''


def _run_pytest_with_gate(tmp_path: Path):
    """Runs the sample under pytest with the compat module as conftest.py. Returns
    (events read while pytest was still running, returncode, output)."""
    (tmp_path / "conftest.py").write_text(COMPAT.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(SAMPLE_TEST, encoding="utf-8")
    env = os.environ.copy()
    env.update({"SELENIUM_COMPAT_RUN_DIR": str(tmp_path), "SELENIUM_COMPAT_SCREENSHOT_MODE": "failed"})
    out_path = tmp_path / "_out.txt"
    with open(out_path, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            [sys.executable, "-m", "pytest", str(tmp_path), "-q", "-p", "no:cacheprovider", "--tb=short"],
            cwd=str(tmp_path), env=env, stdout=out, stderr=subprocess.STDOUT,
        )
        live_lines = None
        try:
            deadline = time.time() + 60
            while not (tmp_path / "marker").exists() and time.time() < deadline and proc.poll() is None:
                time.sleep(0.05)
            assert proc.poll() is None, "pytest exited before the gate: " + out_path.read_text(errors="replace")
            live_lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        finally:
            (tmp_path / "go").write_text("go")
            proc.wait(timeout=60)
    return live_lines, proc.returncode, out_path.read_text(encoding="utf-8", errors="replace")


def _events(tmp_path: Path) -> list[dict]:
    text = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]  # every line is one complete JSON object


def _ts(value: str) -> datetime:
    assert value.endswith("Z")  # explicit UTC
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_events_are_flushed_while_pytest_is_still_running(tmp_path):
    live, _, _ = _run_pytest_with_gate(tmp_path)
    parsed = [json.loads(line) for line in live]
    types = [e["type"] for e in parsed]
    # test_ok's three actions and the test_started were readable before the test ended.
    assert types[0] == "test_started"
    assert types.count("step_started") == 3 and types.count("step_passed") == 3
    assert "test_finished" not in types


def test_full_run_emits_ordered_events_for_success_and_failure(tmp_path):
    _, returncode, output = _run_pytest_with_gate(tmp_path)
    events = _events(tmp_path)

    assert returncode == 1                       # test_fails still fails the run
    assert "RuntimeError" in output and ERROR_TEXT in output  # original exception propagated untouched

    ok = [e for e in events if e["nodeid"].endswith("::test_ok")]
    assert [e["type"] for e in ok] == [
        "test_started",
        "step_started", "step_passed",
        "step_started", "step_passed",
        "step_started", "step_passed",
        "test_finished",
    ]
    assert [e["action"] for e in ok if e["type"] == "step_started"] == ["open", "type", "click"]
    assert [e["step_index"] for e in ok if e["type"] == "step_started"] == [0, 1, 2]
    assert ok[-1]["status"] == "PASSED"

    # start and completion share one step id; durations are sane and consistent with timestamps
    starts = {e["step_id"]: e for e in ok if e["type"] == "step_started"}
    for end in (e for e in ok if e["type"] == "step_passed"):
        start = starts[end["step_id"]]
        assert start["status"] == "RUNNING" and end["status"] == "PASSED"
        assert end["duration"] >= 0
        elapsed = (_ts(end["timestamp"]) - _ts(start["timestamp"])).total_seconds()
        assert elapsed >= 0 and abs(elapsed - end["duration"]) < 0.5
    assert all(e["target"] == "input" for e in ok if e.get("action") in ("type", "click"))

    bad = [e for e in events if e["nodeid"].endswith("::test_fails")]
    assert [e["type"] for e in bad] == [
        "test_started", "step_started", "step_passed", "step_started", "step_failed", "test_finished",
    ]
    failed = bad[4]
    assert failed["step_id"] == bad[3]["step_id"] and failed["step_index"] == 1  # numbering restarts per test
    assert failed["status"] == "FAILED" and failed["error_category"] == "RuntimeError"
    assert bad[-1]["status"] == "FAILED"


def test_events_never_contain_typed_values_urls_or_error_text(tmp_path):
    _run_pytest_with_gate(tmp_path)
    raw = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    for secret in (TYPED, URL_SECRET, ERROR_TEXT, "token=", "https://", "Traceback"):
        assert secret not in raw
    # Phase 5: only host + path of an opened URL is shown; the query string never is.
    opened = [json.loads(line) for line in raw.splitlines() if '"action":"open"' in line]
    assert opened and {e["page"] for e in opened} == {"example.test/login", "example.test/next"}


def test_steps_json_and_screenshots_are_unchanged_by_events(tmp_path):
    _run_pytest_with_gate(tmp_path)
    steps = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
    assert [s["action"] for s in steps if s["nodeid"].endswith("::test_ok")] == ["open", "type", "click"]
    # every existing key is still there (the continue-after-failure work added more: started_at, url, ...)
    assert {
        "nodeid", "step", "action", "target", "value", "runtime_value",
        "status", "duration", "error", "screenshot", "timestamp",
    } <= set(steps[0])
    # the typed value is still recorded for the backend's own masking, exactly as before
    assert any(s["runtime_value"] == TYPED for s in steps)
    failed = [s for s in steps if s["status"] == "FAIL" and s["action"] == "click"]
    assert failed and failed[0]["error"].startswith("RuntimeError")
    assert failed[0]["screenshot"] and (tmp_path / "screenshots" / failed[0]["screenshot"]).exists()
    assert not (tmp_path / "steps.json").read_text(encoding="utf-8").startswith("{")  # still a JSON array


def test_nested_wrapped_operation_does_not_emit_a_second_step(tmp_path):
    result, _ = run_script('''
from selenium.webdriver.remote.webdriver import WebDriver

class D(WebDriver):
    _is_remote = False
    def __init__(self):
        pass
    def execute(self, command, params=None):
        return {"value": None}

mod.CUR.nodeid = "t.py::t"
outer = mod._step_begin("click", "button", None)
D().get("https://example.test/inner")      # a wrapped call inside the outer step
mod._step_end(outer, "PASS")
mod._step_end(outer, "PASS")                # a second end is ignored
print(json.dumps({"depth": getattr(mod.CUR, "step_depth", None), "steps": len(mod.EVENTS)}))
''', tmp_path)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["step_started", "step_passed"]   # the outer step only
    assert events[0]["step_id"] == events[1]["step_id"] and events[0]["action"] == "click"
    assert result["depth"] == 0                                              # depth fully unwound
    assert result["steps"] == 1                                              # steps.json still records the inner call


def test_actionchains_perform_emits_a_step_and_propagates_failures(tmp_path):
    result, _ = run_script('''
class Chain:
    _driver = None

mod.CUR.nodeid = "t.py::t"
mod._original_actionchains_perform = lambda self, *a, **k: "performed"
assert mod._retry_actionchains_perform(Chain()) == "performed"

def boom(self, *a, **k):
    raise ValueError("secret chain text")
mod._original_actionchains_perform = boom
try:
    mod._retry_actionchains_perform(Chain())
    raised = None
except ValueError as exc:
    raised = str(exc)
print(json.dumps({"raised": raised}))
''', tmp_path)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [e["type"] for e in events] == ["step_started", "step_passed", "step_started", "step_failed"]
    assert [e["step_index"] for e in events] == [0, 0, 1, 1]
    assert events[3]["error_category"] == "ValueError"
    assert result["raised"] == "secret chain text"                            # original exception re-raised
    assert "secret chain text" not in (tmp_path / "events.jsonl").read_text()


def test_event_writer_failure_never_breaks_the_test(tmp_path):
    result, _ = run_script('''
from pathlib import Path
mod.EVENTS_PATH = Path(__file__ if False else ".") / "no_such_dir" / "x" / "\\0bad"   # unwritable path
mod.CUR.nodeid = "t.py::t"
token = mod._step_begin("open", "", None)
mod._step_end(token, "PASS")
print(json.dumps({"ok": True}))
''', tmp_path)
    assert result["ok"] is True
