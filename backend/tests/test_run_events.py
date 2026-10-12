"""Backend delivery of live run events (Phase 2): tailing events.jsonl, the bounded
per-run buffer, run lifecycle events, replay, the SSE endpoint and its authorization.

No browser or subprocess is started: the runner's output is simulated by writing
events.jsonl lines directly, and the pytest subprocess by the same FakeProc/FakeDB
used by the cancellation tests.
"""
import asyncio
import json
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.core.security import create_access_token, hash_password
from app.models.project import Project
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.models.user import User
from app.services import execution_service, run_event_service as svc
from tests.conftest import make_run
from tests.test_run_cancellation_race import FakeDB, FakeProc, FakeRun

NODE = "test_login.py::test_login"


@pytest.fixture(autouse=True)
def _clean_registry():
    svc.reset_registry()
    yield
    svc.reset_registry()


def line(**fields) -> str:
    base = {"nodeid": NODE, "timestamp": "2026-10-12T10:31:07.412Z"}
    base.update(fields)
    return json.dumps(base) + "\n"


def started(index, action="click"):
    return line(type="step_started", step_id=f"{NODE}#{index}", step_index=index, step_number=index + 1,
                action=action, target="button", status="RUNNING")


def passed(index, action="click", duration=0.25):
    return line(type="step_passed", step_id=f"{NODE}#{index}", step_index=index, step_number=index + 1,
                action=action, target="button", status="PASSED", duration=duration)


def append(path, text, mode="a"):
    with open(path, mode, encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def types(run_id):
    return [e["type"] for e in svc.get_buffer(run_id).read_after(0)[0]]


# ---------------------------------------------------------------------------
# Tailing
# ---------------------------------------------------------------------------

def test_tailer_publishes_appended_events_in_order_without_duplicates(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(7, path)
    assert tailer.poll() == 0                                  # no file yet: nothing, no error

    append(path, line(type="test_started", status="RUNNING") + started(0))
    assert tailer.poll() == 2
    append(path, passed(0) + started(1) + passed(1))
    assert tailer.poll() == 3
    assert tailer.poll() == 0                                  # nothing new: no re-read, no duplicates

    events, terminal, first = svc.get_buffer(7).read_after(0)
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]       # backend-assigned, contiguous
    assert [e["type"] for e in events] == ["test_started", "step_started", "step_passed", "step_started", "step_passed"]
    assert all(e["run_id"] == 7 for e in events) and not terminal and first == 1


def test_incomplete_trailing_line_is_kept_until_its_newline_arrives(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    whole = started(0)
    append(path, passed(0) + whole[:30])                        # a record cut mid-way
    assert tailer.poll() == 1                                   # only the complete one
    append(path, whole[30:-1])                                  # still no newline
    assert tailer.poll() == 0
    append(path, "\n")
    assert tailer.poll() == 1
    assert types(1) == ["step_passed", "step_started"]


def test_malformed_lines_are_skipped_without_stopping_the_stream(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    append(path, "{not json\n" + "[1,2]\n" + "\n" + '"text"\n' + line(type="mystery") + started(0) + "x" * 70000 + "\n" + passed(0))
    assert tailer.poll() == 2
    assert types(1) == ["step_started", "step_passed"]
    assert tailer.malformed >= 5


def test_drain_reads_everything_complete_and_drops_the_unfinished_tail(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    append(path, "".join(started(i) + passed(i) for i in range(50)) + started(50)[:20])
    assert tailer.drain() == 100
    assert tailer.drain() == 0
    assert len(svc.get_buffer(1)) == 100


def test_truncated_file_is_never_replayed_from_the_start(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    append(path, started(0) + passed(0))
    tailer.poll()
    append(path, started(1), mode="w")                          # replaced by something shorter
    assert tailer.poll() == 0
    assert types(1) == ["step_started", "step_passed"]


def test_symlinked_events_file_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    append(outside / "target.jsonl", started(0))
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    try:
        os.symlink(outside / "target.jsonl", run_dir / "events.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    tailer = svc.RunEventTailer(1, run_dir / "events.jsonl")
    assert tailer.poll() == 0 and len(svc.get_buffer(1)) == 0


def test_two_runs_never_mix_events(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = svc.RunEventTailer(1, tmp_path / "a" / "events.jsonl")
    b = svc.RunEventTailer(2, tmp_path / "b" / "events.jsonl")
    append(tmp_path / "a" / "events.jsonl", started(0) + passed(0))
    append(tmp_path / "b" / "events.jsonl", started(0))
    a.poll()
    b.poll()
    assert types(1) == ["step_started", "step_passed"] and types(2) == ["step_started"]
    assert {e["run_id"] for e in svc.get_buffer(1).read_after(0)[0]} == {1}
    assert svc.get_buffer(2).read_after(0)[0][0]["seq"] == 1     # sequences are per run


# ---------------------------------------------------------------------------
# Validation and sanitization
# ---------------------------------------------------------------------------

def test_public_events_carry_only_whitelisted_fields_and_no_raw_nodeid(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    hostile = json.loads(passed(0))
    hostile.update({
        "value": "TYPED-PASSWORD-123", "url": "https://x.test/?token=SECRETTOK", "runtime_value": "TYPED-PASSWORD-123",
        "error": "Traceback (most recent call last): password=hunter2", "env": {"DATABASE_URL": "mssql://u:p@h/db"},
    })
    append(path, json.dumps(hostile) + "\n")
    tailer.poll()
    event = svc.get_buffer(1).read_after(0)[0][0]
    assert set(event) == {"type", "test_id", "test", "step_id", "step_index", "step_number", "action", "target",
                          "status", "timestamp", "duration", "seq", "run_id"}
    dumped = json.dumps(event)
    for secret in ("TYPED-PASSWORD-123", "SECRETTOK", "hunter2", "DATABASE_URL", "Traceback"):
        assert secret not in dumped
    assert "nodeid" not in event and event["step_id"] == "t1-s0"


def test_untrusted_field_values_are_replaced_not_forwarded(tmp_path):
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path)
    record = json.loads(passed(0))
    record.update({"action": "type password=hunter2 now!", "target": "input password=hunter2", "timestamp": "yesterday",
                   "duration": -5})
    failed = json.loads(passed(1))
    failed.update({"type": "step_failed", "error_category": "boom: token=abc123", "duration": float("nan")})
    append(path, json.dumps(record) + "\n" + json.dumps(failed).replace("NaN", "NaN") + "\n")
    tailer.poll()
    first, second = svc.get_buffer(1).read_after(0)[0]
    assert first["action"] == "step" and first["target"] == "" and "duration" not in first
    assert first["timestamp"].endswith("Z") and first["timestamp"] != "yesterday"
    assert second["error_category"] == "Error" and "duration" not in second


def test_parametrized_test_names_are_sanitized_but_joined_by_private_key(tmp_path):
    secrets = ("AAAA-1111", "BBBB-2222")
    path = tmp_path / "events.jsonl"
    tailer = svc.RunEventTailer(1, path, lambda: secrets)
    node_a, node_b = "t.py::test_login[AAAA-1111]", "t.py::test_login[BBBB-2222]"
    append(path, "".join(
        line(type=t, nodeid=n, step_id=f"{n}#0", step_index=0, step_number=None, action="open", target="",
             status="X", duration=0.1)
        for n in (node_a, node_b) for t in ("step_started", "step_passed")
    ))
    tailer.poll()
    events = svc.get_buffer(1).read_after(0)[0]
    dumped = json.dumps(events)
    assert "AAAA-1111" not in dumped and "BBBB-2222" not in dumped
    assert events[0]["test"] == "t.py::test_login[…]"            # parametrize ids are never shown
    # even if both sanitized names are identical, correlation uses the private node id
    assert [e["step_id"] for e in events] == ["t1-s0", "t1-s0", "t2-s0", "t2-s0"]
    assert events[0]["test_id"] != events[2]["test_id"]


# ---------------------------------------------------------------------------
# Buffer, registry, retention
# ---------------------------------------------------------------------------

def test_buffer_is_bounded_and_keeps_the_newest_events(monkeypatch):
    monkeypatch.setattr(svc, "MAX_EVENTS_PER_RUN", 10)
    buf = svc.RunEventBuffer(1)
    for i in range(25):
        buf.publish({"type": "step_started", "n": i})
    events, _, first = buf.read_after(0)
    assert len(events) == 10 and first == 16 and events[-1]["seq"] == 25


def test_terminal_event_is_published_exactly_once_and_nothing_follows_it():
    buf = svc.RunEventBuffer(1)
    assert buf.publish_terminal("CANCELLED") is True
    assert buf.publish_terminal("COMPLETED") is False           # a racing second terminal is ignored
    assert buf.publish({"type": "step_started"}) == 0           # nothing after the end
    events, terminal, _ = buf.read_after(0)
    assert [e["type"] for e in events] == ["run_cancelled"] and terminal


def test_finished_buffers_expire_and_the_registry_is_capped(monkeypatch):
    svc.open_run(1)
    svc.finish_run(1, "COMPLETED")
    svc._REGISTRY[1]._finished_at -= svc.TERMINAL_RETENTION_SECONDS + 1
    assert svc.get_buffer(1) is None                            # retention elapsed

    monkeypatch.setattr(svc, "MAX_RETAINED_RUNS", 5)
    for run_id in range(100, 110):
        svc.finish_run(run_id, "COMPLETED")
    svc.open_run(999)                                           # unfinished: never evicted by count
    assert len(svc._REGISTRY) <= 6 and 999 in svc._REGISTRY


# ---------------------------------------------------------------------------
# SSE streaming (async, no HTTP)
# ---------------------------------------------------------------------------

def collect(buffer, after=0, **kwargs):
    async def never():
        return False

    async def run():
        return [chunk async for chunk in svc.sse_stream(buffer, after, kwargs.pop("is_disconnected", never), **kwargs)]

    return asyncio.run(asyncio.wait_for(run(), 10))


def parse(chunks):
    out = []
    for chunk in chunks:
        if chunk.startswith(":"):
            continue
        fields = dict(part.split(": ", 1) for part in chunk.strip().split("\n"))
        out.append((fields.get("id"), fields["event"], json.loads(fields["data"])))
    return out


def test_stream_replays_from_the_cursor_and_ends_after_the_terminal_event():
    buf = svc.open_run(3)
    for i in range(3):
        buf.publish({"type": "step_started", "step_index": i})
    buf.publish_terminal("COMPLETED")
    full = parse(collect(buf))
    assert [e[0] for e in full] == ["1", "2", "3", "4"] and full[-1][1] == "run_completed"
    resumed = parse(collect(buf, after=2))                       # a reconnect: nothing twice
    assert [e[0] for e in resumed] == ["3", "4"]
    assert parse(collect(buf, after=4)) == []                    # fully caught up: closes cleanly


def test_stream_delivers_events_that_arrive_while_connected_then_closes():
    buf = svc.open_run(4)
    buf.publish({"type": "step_started"})

    async def run():
        got = []

        async def feeder():
            await asyncio.sleep(0.15)
            buf.publish({"type": "step_passed"})
            await asyncio.sleep(0.15)
            buf.publish_terminal("FAILED")

        async def never():
            return False

        task = asyncio.create_task(feeder())
        async for chunk in svc.sse_stream(buf, 0, never, poll_interval=0.02):
            got.append(chunk)
        await task
        return got

    events = parse(asyncio.run(asyncio.wait_for(run(), 10)))
    assert [e[1] for e in events] == ["step_started", "step_passed", "run_failed"]
    assert [e[0] for e in events] == ["1", "2", "3"]


def test_stream_sends_heartbeats_and_stops_when_the_client_disconnects():
    buf = svc.open_run(5)
    calls = {"n": 0}

    async def disconnected():
        calls["n"] += 1
        return calls["n"] > 8

    chunks = collect(buf, is_disconnected=disconnected, poll_interval=0.01, heartbeat_seconds=0.02)
    assert any(c.startswith(": heartbeat") for c in chunks)
    assert calls["n"] == 9                                       # no work after the disconnect


def test_stream_uses_the_database_status_when_no_terminal_event_ever_comes():
    buf = svc.open_run(6)                                        # e.g. a queued run cancelled by the API

    async def probe():
        return "CANCELLED"

    events = parse(collect(buf, status_probe=probe, probe_seconds=0, poll_interval=0.01))
    assert [e[1] for e in events] == ["run_cancelled"]
    assert svc.finish_run(6, "COMPLETED") is False               # still exactly once


def test_stream_reports_a_gap_when_the_cursor_fell_out_of_the_buffer(monkeypatch):
    monkeypatch.setattr(svc, "MAX_EVENTS_PER_RUN", 3)
    buf = svc.open_run(8)
    for i in range(6):
        buf.publish({"type": "step_started", "step_index": i})
    buf.publish_terminal("COMPLETED")
    chunks = collect(buf, after=0)
    assert any(c.startswith("event: gap") for c in chunks)


# ---------------------------------------------------------------------------
# Run lifecycle through _execute_pytest / execute_run
# ---------------------------------------------------------------------------

@pytest.fixture()
def lifecycle(monkeypatch):
    monkeypatch.setattr(execution_service.time, "sleep", lambda _s: None)
    monkeypatch.setattr(execution_service, "persist_run_results", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_step_summary", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_log_failure_diagnostics", lambda *a, **k: None)
    monkeypatch.setattr(execution_service, "_terminate_process_tree", lambda p: setattr(p, "returncode", -9))


class StatusDB(FakeDB):
    """FakeDB that also answers the final-status read execute_run's finally block does."""

    def __init__(self, run, **kw):
        super().__init__(**kw)
        self.run = run

    def scalar(self, stmt):
        return self.run.status

    def close(self):
        pass


def run_lifecycle(tmp_path, monkeypatch, run, proc, db=None):
    db = db or StatusDB(run)
    monkeypatch.setattr(execution_service.subprocess, "Popen", lambda *a, **k: proc)
    execution_service.run_event_service.open_run(run.id)
    execution_service._execute_pytest(db, run, tmp_path)
    execution_service._publish_terminal_event(db, run.id)       # execute_run's finally block
    return svc.get_buffer(run.id).read_after(0)[0]


class StreamingProc(FakeProc):
    """Appends to events.jsonl while "running", and records how many events were already
    published at each poll."""

    def __init__(self, path, plan, poll_sequence, run_id):
        super().__init__(poll_sequence)
        self._path, self._plan, self._run_id, self.calls = path, plan, run_id, 0
        self.seen = []

    def poll(self):
        self.seen.append(len(svc.get_buffer(self._run_id)))
        if self.calls < len(self._plan):
            append(self._path, self._plan[self.calls])
        self.calls += 1
        return super().poll()


def test_success_streams_events_while_running_then_completes(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=11)
    proc = StreamingProc(tmp_path / "events.jsonl", [started(0), passed(0), started(1) + passed(1)], [None, None, None, 0], 11)
    events = run_lifecycle(tmp_path, monkeypatch, run, proc)
    assert [e["type"] for e in events] == ["run_started", "step_started", "step_passed", "step_started", "step_passed", "run_completed"]
    assert proc.seen[:4] == [1, 2, 3, 5]                      # published while the process was still running
    assert events[-1]["status"] == "COMPLETED"


def test_events_written_just_before_exit_are_drained(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=12)
    proc = StreamingProc(tmp_path / "events.jsonl", [started(0) + passed(0)], [0], 12)
    events = run_lifecycle(tmp_path, monkeypatch, run, proc)
    assert [e["type"] for e in events][1:3] == ["step_started", "step_passed"]


def test_test_failure_ends_with_run_failed(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=13)
    failed = line(type="step_failed", step_id=f"{NODE}#0", step_index=0, step_number=1, action="click",
                  target="button", status="FAILED", duration=1.5, error_category="RuntimeError")
    proc = StreamingProc(tmp_path / "events.jsonl", [started(0) + failed], [1], 13)
    events = run_lifecycle(tmp_path, monkeypatch, run, proc)
    assert events[-2]["error_category"] == "RuntimeError" and events[-1]["type"] == "run_failed"


def test_cancellation_emits_run_cancelled_once(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=14)
    db = StatusDB(run, cancel_after_n_refreshes=2)
    proc = StreamingProc(tmp_path / "events.jsonl", [started(0)], [None, None, None], 14)
    events = run_lifecycle(tmp_path, monkeypatch, run, proc, db)
    assert [e["type"] for e in events if e["type"].startswith("run_")] == ["run_started", "run_cancelled"]
    assert svc.finish_run(14, "FAILED") is False


def test_timeout_emits_run_failed(tmp_path, monkeypatch, lifecycle):
    monkeypatch.setattr(execution_service.settings, "test_execution_timeout_seconds", 0)
    run = FakeRun(run_id=15)
    events = run_lifecycle(tmp_path, monkeypatch, run, FakeProc([None, None]))
    assert [e["type"] for e in events] == ["run_started", "run_failed"]


def test_subprocess_startup_error_still_ends_with_a_terminal_event(tmp_path, monkeypatch, lifecycle):
    def boom(*a, **k):
        raise OSError("cannot start")

    monkeypatch.setattr(execution_service.subprocess, "Popen", boom)
    run = FakeRun(run_id=16)
    db = StatusDB(run)
    execution_service.run_event_service.open_run(16)
    execution_service._execute_pytest(db, run, tmp_path)
    execution_service._publish_terminal_event(db, 16)
    assert types(16) == ["run_failed"]                           # no run_started: it never started


def test_run_without_step_events_has_start_and_terminal_only(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=17)
    events = run_lifecycle(tmp_path, monkeypatch, run, FakeProc([0]))
    assert [e["type"] for e in events] == ["run_started", "run_completed"]


def test_cancel_before_launch_emits_only_run_cancelled(tmp_path, monkeypatch, lifecycle):
    run = FakeRun(run_id=18, cancel_requested=True)
    events = run_lifecycle(tmp_path, monkeypatch, run, FakeProc([0]))
    assert [e["type"] for e in events] == ["run_cancelled"]


def test_execute_run_crash_path_publishes_run_failed(monkeypatch):
    run = SimpleNamespace(id=19, status="RUNNING", completed_at=None, project_id=1, browser="chrome")

    class Session:
        def scalar(self, stmt):
            return run.status if str(stmt).startswith("SELECT runs.status") else run

        def scalars(self, stmt):
            return SimpleNamespace(all=lambda: [])

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(execution_service, "SessionLocal", lambda: Session())

    def crash(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(execution_service, "_run_with_temp_directory", crash)
    execution_service.execute_run(19)
    assert run.status == "FAILED" and types(19) == ["run_failed"]


def test_a_retry_gets_its_own_stream(tmp_path, monkeypatch, lifecycle):
    first = run_lifecycle(tmp_path, monkeypatch, FakeRun(run_id=20), StreamingProc(tmp_path / "events.jsonl", [started(0)], [1], 20))
    retry_dir = tmp_path / "retry"
    retry_dir.mkdir()
    second = run_lifecycle(retry_dir, monkeypatch, FakeRun(run_id=21), StreamingProc(retry_dir / "events.jsonl", [], [0], 21))
    assert {e["run_id"] for e in first} == {20} and {e["run_id"] for e in second} == {21}
    assert [e["type"] for e in second] == ["run_started", "run_completed"]


# ---------------------------------------------------------------------------
# Endpoint: authentication, ownership, replay
# ---------------------------------------------------------------------------

def sse(text):
    return parse([c for c in text.split("\n\n") if c.strip()])


def events_url(project, run):
    return f"/api/projects/{project.id}/runs/{run.id}/events"


def test_endpoint_streams_buffered_events_with_sse_headers(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="RUNNING")
    buf = svc.open_run(run.id)
    buf.publish({"type": "run_started", "status": "RUNNING", "timestamp": "2026-10-12T10:00:00.000Z"})
    buf.publish({"type": "step_started", "step_id": "t1-s0"})
    buf.publish_terminal("COMPLETED")

    response = client.get(events_url(test_project, run), headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    assert [e[0] for e in sse(response.text)] == ["1", "2", "3"]

    after = client.get(events_url(test_project, run) + "?after=2", headers=auth_headers)
    assert [e[0] for e in sse(after.text)] == ["3"]
    resumed = client.get(events_url(test_project, run), headers={**auth_headers, "Last-Event-ID": "1"})
    assert [e[0] for e in sse(resumed.text)] == ["2", "3"]


def test_endpoint_requires_authentication(client, db_session, test_project):
    run = make_run(db_session, test_project, status="RUNNING")
    assert client.get(events_url(test_project, run)).status_code in (401, 403)
    bad = client.get(events_url(test_project, run), headers={"Authorization": "Bearer not-a-token"})
    assert bad.status_code == 401


def test_endpoint_hides_runs_of_other_users_and_unknown_runs(client, db_session, test_project):
    run = make_run(db_session, test_project, status="RUNNING")
    svc.open_run(run.id).publish_terminal("COMPLETED")
    tag = int.from_bytes(os.urandom(4), "big")
    other = User(full_name="Other", email=f"events-other-{tag}@example.com", mobile_number=f"9{tag % 10**9:09d}",
                 password_hash=hash_password("Password123!"), is_active=True)
    db_session.add(other)
    db_session.commit()
    headers = {"Authorization": f"Bearer {create_access_token(other.id)}"}

    assert client.get(events_url(test_project, run), headers=headers).status_code == 404
    mine = {"Authorization": f"Bearer {create_access_token(test_project.owner_id)}"}
    assert client.get(f"/api/projects/{test_project.id}/runs/99999999/events", headers=mine).status_code == 404


def test_endpoint_validates_the_cursor(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")
    assert client.get(events_url(test_project, run) + "?after=-1", headers=auth_headers).status_code == 422
    assert client.get(events_url(test_project, run) + "?after=abc", headers=auth_headers).status_code == 422


def test_finished_run_without_buffer_replays_from_persisted_results(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    test = TestResult(run_id=run.id, nodeid="t.py::test_a[hunter2-secret]", file_name="t.py", class_name=None,
                      method_name="test_a", status="FAILED", duration_seconds=2.0)
    db_session.add(test)
    db_session.flush()
    when = datetime(2026, 10, 12, 10, 0, 0)
    db_session.add_all([
        StepResult(test_result_id=test.id, step_number=1, action="open", target="https://x.test/?token=SECRETTOK",
                   value="v", status="PASS", duration_seconds=0.5, error=None, sequence_index=0, occurred_at=when),
        StepResult(test_result_id=test.id, step_number=2, action="click", target="button", value="",
                   status="FAIL", duration_seconds=1.0, error="RuntimeError('boom password=hunter2')",
                   sequence_index=1, occurred_at=when),
    ])
    db_session.commit()

    response = client.get(events_url(test_project, run), headers=auth_headers)
    events = sse(response.text)
    assert [e[1] for e in events] == ["test_started", "step_passed", "step_failed", "test_finished", "run_failed"]
    assert [e[0] for e in events] == ["1", "2", "3", "4", "5"]    # stable numbering, no invented step_started
    passed_event, failed_event = events[1][2], events[2][2]
    assert passed_event["target"] == "" and passed_event["duration"] == 0.5 and passed_event["status"] == "PASSED"
    assert failed_event["error_category"] == "RuntimeError" and failed_event["status"] == "FAILED"
    for secret in ("SECRETTOK", "hunter2", "boom"):
        assert secret not in response.text
    again = client.get(events_url(test_project, run) + "?after=3", headers=auth_headers)
    assert [e[0] for e in sse(again.text)] == ["4", "5"]


def test_finished_run_with_no_results_still_gets_its_terminal_event(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="CANCELLED")
    events = sse(client.get(events_url(test_project, run), headers=auth_headers).text)
    assert [e[1] for e in events] == ["run_cancelled"]


def test_symlink_guard_rejects_a_linked_events_file(tmp_path, monkeypatch):
    # The real-symlink test above is skipped where symlinks need privileges; this
    # exercises the same guard directly.
    path = tmp_path / "events.jsonl"
    append(path, started(0))
    monkeypatch.setattr(type(path), "is_symlink", lambda self: True)
    tailer = svc.RunEventTailer(1, path)
    assert tailer.poll() == 0 and len(svc.get_buffer(1)) == 0


REAL_SAMPLE = '''
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement


class D(WebDriver):
    _is_remote = False
    def __init__(self):
        pass
    def execute(self, command, params=None):
        return {"value": None}
    def execute_script(self, *a, **k):
        return None
    def save_screenshot(self, path):
        open(path, "wb").write(b"png")
        return True


class E(WebElement):
    def __init__(self, parent):
        self._parent = parent
        self._id = "e"
    @property
    def tag_name(self):
        return "input"
    def _execute(self, command, params=None):
        if command == Command.CLICK_ELEMENT:
            raise RuntimeError("boom TYPED-PASSWORD-123")
        return {"value": None}


def test_flow():
    d = D()
    d.get("https://example.test/login?token=SECRETTOK")
    e = E(d)
    e.send_keys("TYPED-PASSWORD-123")
    e.click()
'''


def test_real_pytest_subprocess_events_reach_the_buffer_through_execute_pytest(tmp_path, monkeypatch):
    """Phase 1 runner -> events.jsonl -> Phase 2 tailer, with a real pytest subprocess."""
    monkeypatch.setattr(execution_service, "persist_run_results", lambda *a, **k: None)
    (tmp_path / "test_flow.py").write_text(REAL_SAMPLE, encoding="utf-8")
    execution_service._write_conftest(tmp_path, 30)
    run = FakeRun(run_id=30)
    db = StatusDB(run)
    svc.open_run(30)
    execution_service._execute_pytest(db, run, tmp_path)
    execution_service._publish_terminal_event(db, 30)

    events = svc.get_buffer(30).read_after(0)[0]
    assert run.status == "FAILED"
    assert [e["type"] for e in events] == [
        "run_started", "test_started",
        "step_started", "step_passed",
        "step_started", "step_passed",
        "step_started", "step_failed",
        "test_finished", "run_failed",
    ]
    assert [e["seq"] for e in events] == list(range(1, 11))
    assert {e["run_id"] for e in events} == {30}
    step_ids = [e["step_id"] for e in events if e["type"].startswith("step_")]
    assert step_ids == ["t1-s0", "t1-s0", "t1-s1", "t1-s1", "t1-s2", "t1-s2"]
    failed = [e for e in events if e["type"] == "step_failed"][0]
    assert failed["error_category"] == "RuntimeError" and failed["action"] == "click"
    dumped = json.dumps(events)
    for secret in ("TYPED-PASSWORD-123", "SECRETTOK", "boom", "https://", "token="):
        assert secret not in dumped
    # Phase 5: an opened URL is shown as host + path only; its query string never is.
    assert {e["page"] for e in events if e.get("action") == "open"} == {"example.test/login"}
