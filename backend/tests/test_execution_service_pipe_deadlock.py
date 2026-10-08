

import time
from pathlib import Path

import pytest

from app.services import execution_service


class FakeRun:
    def __init__(self, run_id=9001, browser="chrome"):
        self.id = run_id
        self.browser = browser
        self.cancel_requested = False
        self.status = "RUNNING"
        self.completed_at = None


class FakeDB:
    def refresh(self, run):
        pass

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def _no_real_persistence(monkeypatch):
    # This test only cares about whether execution finishes promptly, not
    # about result persistence (that is covered by other tests).
    monkeypatch.setattr(execution_service, "persist_run_results", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_step_summary", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_failure_diagnostics", lambda *a, **k: None)


def test_large_stdout_does_not_deadlock_the_poll_loop(tmp_path, monkeypatch):
    # A generous but bounded timeout: large enough that a real (non-hung)
    # failing test run has no trouble finishing inside it, small enough
    # that if the pipe-deadlock bug were still present, this test would
    # fail fast via timeout instead of silently taking 120s+.
    monkeypatch.setattr(execution_service.settings, "test_execution_timeout_seconds", 20)

    # A failing test whose --tb=long assertion message is ~300KB of text --
    # far larger than any realistic OS pipe buffer (commonly 4-64KB), to
    # reliably trigger the deadlock if stdout/stderr were still piped
    # instead of redirected to disk files.
    test_file = tmp_path / "test_overflow.py"
    test_file.write_text(
        "def test_overflow():\n"
        "    big = 'X' * 300_000\n"
        "    assert False, big\n",
        encoding="utf-8",
    )

    run = FakeRun()
    db = FakeDB()

    start = time.monotonic()
    execution_service._execute_pytest(db, run, tmp_path)
    elapsed = time.monotonic() - start

    assert run.status == "FAILED"
    assert elapsed < 15, (
        f"_execute_pytest took {elapsed:.1f}s -- expected a quick failure, "
        f"not a hang anywhere near the {20}s configured timeout "
        f"(pipe-buffer deadlock regression)"
    )
