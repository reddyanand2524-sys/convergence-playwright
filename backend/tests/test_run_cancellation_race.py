

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import execution_service


class FakeRun:
    def __init__(self, cancel_requested=False, run_id=1, browser="chrome"):
        self.id = run_id
        self.browser = browser
        self.cancel_requested = cancel_requested
        self.status = "RUNNING"
        self.completed_at = None


class FakeDB:
    """Stands in for the SQLAlchemy session used by _execute_pytest.

    `refresh(run)` simulates re-reading the Run row from the DB. Tests
    control what a "concurrent" cancel endpoint commit would look like by
    either setting `run.cancel_requested` directly before calling
    _execute_pytest (simulating "already committed by the time we check"),
    or by supplying `cancel_after_n_refreshes` to flip it mid-poll-loop
    (simulating the cancel endpoint's commit landing concurrently).
    """

    def __init__(self, cancel_after_n_refreshes=None):
        self.commits = 0
        self.refreshes = 0
        self.cancel_after_n_refreshes = cancel_after_n_refreshes

    def refresh(self, run):
        self.refreshes += 1
        if (
            self.cancel_after_n_refreshes is not None
            and self.refreshes >= self.cancel_after_n_refreshes
        ):
            run.cancel_requested = True

    def commit(self):
        self.commits += 1


class FakeProc:
    """Stands in for subprocess.Popen. `poll_sequence` is the list of
    values proc.poll() returns on successive calls (None = still running,
    int = exited with that return code); the last value is held once the
    sequence is exhausted."""

    def __init__(self, poll_sequence, pid=4242):
        self._poll_sequence = list(poll_sequence)
        self.pid = pid
        self.returncode = None

    def poll(self):
        if self._poll_sequence:
            value = self._poll_sequence.pop(0)
        else:
            value = self.returncode
        if value is not None:
            self.returncode = value
        return value

    def communicate(self):
        return ("stdout", "stderr")

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # The poll loop's 0.5s tick would otherwise make every test here slow
    # for no reason -- none of these tests depend on real wall-clock time,
    # only on the sequence of poll()/cancel_requested observations.
    monkeypatch.setattr(execution_service.time, "sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def _no_real_persistence(monkeypatch):
    # COMPLETED/FAILED paths call persist_run_results, which expects a real
    # JUnit report on disk. These tests only exercise status-resolution
    # logic, so persistence is stubbed to a no-op.
    monkeypatch.setattr(execution_service, "persist_run_results", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_step_summary", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_failure_diagnostics", lambda *a, **k: None)


def _run_execute_pytest(tmp_path, run, db, proc, monkeypatch, terminate_side_effect=None):
    monkeypatch.setattr(execution_service.subprocess, "Popen", lambda *a, **k: proc)

    if terminate_side_effect is not None:
        def _fake_terminate(p):
            terminate_side_effect(p)

        monkeypatch.setattr(execution_service, "_terminate_process_tree", _fake_terminate)
    else:
        # Default fake terminate: simulate taskkill actually killing the
        # process (poll()/returncode becomes nonzero) without needing a
        # real OS process.
        def _fake_terminate(p):
            p.returncode = -9

        monkeypatch.setattr(execution_service, "_terminate_process_tree", _fake_terminate)

    execution_service._execute_pytest(db, run, tmp_path)


# ---------------------------------------------------------------------------
# 3. Simulated "cancelled subprocess returns nonzero" race -> CANCELLED
# ---------------------------------------------------------------------------

def test_poll_loop_detects_already_killed_process_as_cancelled(tmp_path, monkeypatch):
    """Reproduces the exact race: by the time our loop's first proc.poll()
    runs, the OTHER thread has already (a) committed cancel_requested=True
    to the DB and (b) killed the subprocess (taskkill), so poll() returns a
    nonzero code on the very first check -- the top-of-loop break path that
    used to never re-check cancel_requested at all."""

    run = FakeRun(cancel_requested=True)  # already committed by "the other thread"
    db = FakeDB()
    proc = FakeProc(poll_sequence=[1])  # already dead with nonzero code on first poll()

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "CANCELLED"
    assert run.completed_at is not None


def test_cancel_commit_lands_between_poll_ticks(tmp_path, monkeypatch):
    """A softer variant of the same race: the process is still alive for a
    couple of ticks, then the concurrent cancel commit lands (simulated via
    cancel_after_n_refreshes) in the same tick the process dies."""

    run = FakeRun(cancel_requested=False)
    db = FakeDB(cancel_after_n_refreshes=2)
    # Still running for two ticks, then observed dead (killed) on the third.
    proc = FakeProc(poll_sequence=[None, None, 1])

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "CANCELLED"


# ---------------------------------------------------------------------------
# 4. Termination raises an exception -> CANCELLED, not FAILED
# ---------------------------------------------------------------------------

def test_termination_exception_still_resolves_to_cancelled(tmp_path, monkeypatch):
    run = FakeRun(cancel_requested=False)
    db = FakeDB()
    proc = FakeProc(poll_sequence=[None])  # alive on first check, loop detects cancel next

    def _raise(_proc):
        raise RuntimeError("taskkill blew up")

    # cancel_requested flips true right as the loop checks it (inside the
    # loop's own db.refresh), then _terminate_process_tree raises.
    db.cancel_after_n_refreshes = 1

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch, terminate_side_effect=_raise)

    assert run.status == "CANCELLED"


# ---------------------------------------------------------------------------
# 5. Genuine failure, no cancellation requested -> still FAILED
# ---------------------------------------------------------------------------

def test_genuine_failure_without_cancellation_is_failed(tmp_path, monkeypatch):
    run = FakeRun(cancel_requested=False)
    db = FakeDB()  # cancel_requested never flips
    proc = FakeProc(poll_sequence=[1])  # exits nonzero on its own, no cancel involved

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "FAILED"


def test_genuine_success_without_cancellation_is_completed(tmp_path, monkeypatch):
    run = FakeRun(cancel_requested=False)
    db = FakeDB()
    proc = FakeProc(poll_sequence=[0])

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "COMPLETED"


# ---------------------------------------------------------------------------
# 6/7. Cancel racing with natural completion / a run that would have failed
# anyway -> documented rule: cancellation wins whenever cancel_requested is
# observed true by the time this code's final check runs.
# ---------------------------------------------------------------------------

def test_cancel_racing_natural_completion_cancellation_wins(tmp_path, monkeypatch):
    """Process would have completed successfully (returncode 0) on its own,
    but cancel_requested is already true by the time the loop/finalizer
    observes the exit. Per the documented rule in _execute_pytest, this
    resolves to CANCELLED, never a fabricated COMPLETED/FAILED result for a
    run the caller explicitly tried to stop."""

    run = FakeRun(cancel_requested=True)
    db = FakeDB()
    proc = FakeProc(poll_sequence=[0])  # would have been a clean success

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "CANCELLED"


def test_cancel_racing_a_run_that_would_have_failed_anyway(tmp_path, monkeypatch):
    run = FakeRun(cancel_requested=True)
    db = FakeDB()
    proc = FakeProc(poll_sequence=[1])  # would have been a genuine failure

    _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

    assert run.status == "CANCELLED"


# ---------------------------------------------------------------------------
# 2. Repeated/multiple cancellations in sequence -> always CANCELLED
# ---------------------------------------------------------------------------

def test_repeated_cancellation_always_resolves_cancelled(tmp_path, monkeypatch):
    for i in range(10):
        run = FakeRun(cancel_requested=True, run_id=i)
        db = FakeDB()
        proc = FakeProc(poll_sequence=[1 if i % 2 == 0 else 0])

        _run_execute_pytest(tmp_path, run, db, proc, monkeypatch)

        assert run.status == "CANCELLED", f"iteration {i} resolved to {run.status}"
