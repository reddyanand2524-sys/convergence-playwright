

import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.services import execution_service


class FakeDb:
    """Stand-in for a SQLAlchemy Session: this test only exercises the
    subprocess-env construction in _execute_pytest, not real persistence
    (that's covered separately by test_result_persistence_service.py and
    the live end-to-end verification against the real DB), so persistence
    failing against a fake db is expected and harmless here — it's caught
    by _execute_pytest's own except-block, which just forces status FAILED."""

    def commit(self):
        pass

    def rollback(self):
        pass

    def refresh(self, obj):
        pass


class FakeCompletedProcess:
    """Stand-in for a subprocess.Popen that exits immediately with
    returncode 0 and no output, so the poll loop in _execute_pytest exits
    on its very first iteration."""

    def __init__(self, command, cwd, env, stdout, stderr, text):
        self.pid = 999
        self.returncode = 0
        self.args = command
        junit_path = Path(cwd) / "_diagnostics_report.xml"
        junit_path.write_text(
            '<?xml version="1.0"?><testsuites>'
            '<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>'
            "</testsuites>",
            encoding="utf-8",
        )

    def poll(self):
        return self.returncode

    def communicate(self):
        return "", ""


def _make_fake_popen(captured_env):
    def fake_popen(command, cwd, env, stdout, stderr, text):
        captured_env.update(env)
        return FakeCompletedProcess(command, cwd, env, stdout, stderr, text)

    return fake_popen


def test_execute_pytest_sets_screenshot_mode_to_all_by_default(tmp_path, monkeypatch):
    captured_env = {}

    monkeypatch.setattr(execution_service.subprocess, "Popen", _make_fake_popen(captured_env))

    run = SimpleNamespace(id=999999, browser="chrome", cancel_requested=False, status="RUNNING")
    execution_service._execute_pytest(db=FakeDb(), run=run, tmp_path=tmp_path)

    assert captured_env.get("SELENIUM_COMPAT_SCREENSHOT_MODE") == "all"


def test_execute_pytest_does_not_override_an_explicit_mode(tmp_path, monkeypatch):
    """setdefault() must not clobber an operator-provided override (e.g. for
    local debugging with SELENIUM_COMPAT_SCREENSHOT_MODE=none already set in
    the environment)."""

    captured_env = {}

    monkeypatch.setattr(execution_service.subprocess, "Popen", _make_fake_popen(captured_env))
    monkeypatch.setenv("SELENIUM_COMPAT_SCREENSHOT_MODE", "none")

    run = SimpleNamespace(id=999998, browser="chrome", cancel_requested=False, status="RUNNING")
    execution_service._execute_pytest(db=FakeDb(), run=run, tmp_path=tmp_path)

    assert captured_env.get("SELENIUM_COMPAT_SCREENSHOT_MODE") == "none"
