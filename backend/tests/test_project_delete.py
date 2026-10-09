"""DELETE /api/projects/{project_id} - permanent project deletion.

Uses the shared SAVEPOINT-rollback db_session fixture, so nothing here is ever
committed to the real database.
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, func, select

from app.core.security import create_access_token, hash_password
from app.database import engine
from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.schedule import Schedule
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.models.user import User
from app.services import scheduler_service
from app.services.result_persistence_service import persist_run_results
from tests.conftest import make_project_file, make_run

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _persist(db_session, run, tmp_path, *, fail: bool):
    """Persists one result (with a step + screenshot, and a diagnostic when
    `fail`) for test_login.py, the way a real Run would."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    shot = f"0001_step_{run.id}.png"
    failure = (
        '<failure message="x">test_login.py:10: in test_ok\n    assert False\nE   assert False\n</failure>'
        if fail else ""
    )
    junit = tmp_path / "report.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="1">'
        f'<testcase classname="test_login" name="test_ok" time="0.1" file="test_login.py">{failure}</testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps([{
        "nodeid": "test_login.py::test_ok", "step": 1, "action": "open", "target": "/",
        "value": "", "status": "FAIL" if fail else "PASS", "duration": 0.1,
        "error": "boom" if fail else "", "screenshot": shot, "timestamp": 1700000000.0,
    }]), encoding="utf-8")
    shots_dir = tmp_path / "screenshots"
    shots_dir.mkdir(exist_ok=True)
    (shots_dir / shot).write_bytes(PNG)
    persist_run_results(
        db_session, run, exit_code=1 if fail else 0, stdout="", stderr="",
        junit_report_path=junit, steps_json_path=steps, screenshots_dir=shots_dir,
    )
    run.status = "FAILED" if fail else "COMPLETED"
    run.completed_at = datetime.utcnow()
    db_session.commit()


def _schedule(db_session, project, *, last_run=None, due=False, enabled=True):
    when = datetime.utcnow() - timedelta(minutes=5) if due else datetime.utcnow() + timedelta(days=1)
    schedule = Schedule(
        project_id=project.id, name="sched", browser="chrome", frequency="INTERVAL",
        interval_value=60, start_at=when, next_run_at=when, enabled=enabled,
        last_run_id=last_run.id if last_run else None,
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)
    return schedule


def build_project_data(db_session, project, tmp_path, tag="a"):
    """2 files, a COMPLETED and a FAILED run (results/steps/screenshots/
    diagnostics) and a schedule pointing at one of the runs."""
    make_project_file(db_session, project, "test_login.py", f"# {tag} login")
    make_project_file(db_session, project, "test_other.py", f"# {tag} other")
    ok = make_run(db_session, project, status="RUNNING")
    _persist(db_session, ok, tmp_path / f"{tag}-ok", fail=False)
    bad = make_run(db_session, project, status="RUNNING")
    _persist(db_session, bad, tmp_path / f"{tag}-bad", fail=True)
    _schedule(db_session, project, last_run=bad)
    return ok, bad


def counts(db_session, project_id):
    runs = select(Run.id).where(Run.project_id == project_id)
    tests = select(TestResult.id).where(TestResult.run_id.in_(runs))

    def n(stmt):
        return db_session.scalar(select(func.count()).select_from(stmt.subquery()))

    return {
        "project": n(select(Project.id).where(Project.id == project_id)),
        "files": n(select(ProjectFile.id).where(ProjectFile.project_id == project_id)),
        "runs": n(runs),
        "run_results": n(select(RunResult.id).where(RunResult.run_id.in_(runs))),
        "test_results": n(tests),
        "step_results": n(select(StepResult.id).where(StepResult.test_result_id.in_(tests))),
        "screenshots": n(select(Screenshot.id).where(Screenshot.test_result_id.in_(tests))),
        "diagnostics": n(select(FailureDiagnostic.id).where(FailureDiagnostic.test_result_id.in_(tests))),
        "schedules": n(select(Schedule.id).where(Schedule.project_id == project_id)),
    }


def _new_user(db_session, tag):
    user = User(
        full_name=f"User {tag}", email=f"{tag}.projdelete@example.com",
        mobile_number=f"90000{abs(hash(tag)) % 100000:05d}",
        password_hash=hash_password("x"), is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _new_project(db_session, owner, name):
    project = Project(name=name, frequency="manual", browser="chrome", owner_id=owner.id, is_active=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _delete(client, project, headers):
    return client.delete(f"/api/projects/{project.id}", headers=headers)


EMPTY = {k: 0 for k in (
    "project", "files", "runs", "run_results", "test_results",
    "step_results", "screenshots", "diagnostics", "schedules",
)}


# ---------------------------------------------------------------------------
# success + scope
# ---------------------------------------------------------------------------

def test_owner_deletes_project_and_every_dependent_row(client, db_session, test_user, test_project, auth_headers, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    before = counts(db_session, pid)
    # Precondition: the whole dependency graph really exists.
    assert before == {
        "project": 1, "files": 2, "runs": 2, "run_results": 2, "test_results": 2,
        "step_results": 2, "screenshots": 2, "diagnostics": 1, "schedules": 1,
    }

    resp = _delete(client, test_project, auth_headers)

    assert resp.status_code == 204
    assert resp.content == b""
    db_session.expire_all()
    assert counts(db_session, pid) == EMPTY
    # The owner account itself is untouched.
    assert db_session.get(User, test_user.id) is not None


def test_stored_file_contents_are_removed_with_the_project(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    file_ids = list(db_session.scalars(select(ProjectFile.id).where(ProjectFile.project_id == pid)))

    assert _delete(client, test_project, auth_headers).status_code == 204

    db_session.expire_all()
    assert db_session.scalars(select(ProjectFile).where(ProjectFile.id.in_(file_ids))).all() == []


def test_other_projects_and_other_users_data_are_untouched(client, db_session, test_user, test_project, auth_headers, tmp_path):
    pid = test_project.id
    sibling = _new_project(db_session, test_user, "Sibling Project")
    build_project_data(db_session, sibling, tmp_path / "sibling", tag="sib")
    stranger, _ = _new_user(db_session, "stranger")
    strangers_project = _new_project(db_session, stranger, "Stranger Project")
    build_project_data(db_session, strangers_project, tmp_path / "stranger", tag="str")
    build_project_data(db_session, test_project, tmp_path / "target", tag="tgt")
    sibling_before = counts(db_session, sibling.id)
    stranger_before = counts(db_session, strangers_project.id)

    assert _delete(client, test_project, auth_headers).status_code == 204

    db_session.expire_all()
    assert counts(db_session, pid) == EMPTY
    assert counts(db_session, sibling.id) == sibling_before
    assert counts(db_session, strangers_project.id) == stranger_before
    assert sibling_before["runs"] == 2 and stranger_before["files"] == 2  # not vacuous


def test_historical_results_screenshots_and_diagnostics_are_deleted(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    ok, bad = build_project_data(db_session, test_project, tmp_path)
    run_ids = [ok.id, bad.id]

    assert _delete(client, test_project, auth_headers).status_code == 204

    db_session.expire_all()
    assert db_session.scalars(select(Run).where(Run.id.in_(run_ids))).all() == []
    assert db_session.scalars(select(TestResult).where(TestResult.run_id.in_(run_ids))).all() == []
    assert db_session.scalars(select(RunResult).where(RunResult.run_id.in_(run_ids))).all() == []


# ---------------------------------------------------------------------------
# authorization / not found
# ---------------------------------------------------------------------------

def test_delete_nonexistent_project_returns_404(client, auth_headers):
    assert client.delete("/api/projects/999999999", headers=auth_headers).status_code == 404


def test_delete_requires_authentication(client, db_session, test_project, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    before = counts(db_session, pid)

    assert client.delete(f"/api/projects/{pid}").status_code in (401, 403)

    assert counts(db_session, pid) == before


def test_another_user_cannot_delete_the_project(client, db_session, test_project, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    before = counts(db_session, pid)
    _, other_headers = _new_user(db_session, "intruder")

    resp = _delete(client, test_project, other_headers)

    assert resp.status_code == 404  # same convention as every other project endpoint
    db_session.expire_all()
    assert counts(db_session, pid) == before


def test_repeated_deletion_returns_404(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    assert client.delete(f"/api/projects/{pid}", headers=auth_headers).status_code == 204
    assert client.delete(f"/api/projects/{pid}", headers=auth_headers).status_code == 404


# ---------------------------------------------------------------------------
# schedules
# ---------------------------------------------------------------------------

def test_schedules_are_removed_and_cannot_create_runs_afterwards(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    due_id = _schedule(db_session, test_project, due=True).id
    other_user, _ = _new_user(db_session, "sched-owner")
    other_project = _new_project(db_session, other_user, "Unrelated")
    other_project_id = other_project.id
    unrelated_id = _schedule(db_session, other_project, due=True).id

    assert _delete(client, test_project, auth_headers).status_code == 204

    db_session.expire_all()
    assert db_session.get(Schedule, due_id) is None
    created = scheduler_service.run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert db_session.scalars(select(Run).where(Run.project_id == pid)).all() == []
    # The unrelated project's due schedule is still processed normally.
    assert any(db_session.get(Run, rid).project_id == other_project_id for rid in created)
    assert db_session.get(Schedule, unrelated_id) is not None


# ---------------------------------------------------------------------------
# active runs
# ---------------------------------------------------------------------------

def test_running_run_blocks_deletion_and_changes_nothing(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    running = make_run(db_session, test_project, status="RUNNING")
    queued = make_run(db_session, test_project, status="QUEUED")
    sched = db_session.scalars(select(Schedule).where(Schedule.project_id == pid)).one()
    before = counts(db_session, pid)

    resp = _delete(client, test_project, auth_headers)

    assert resp.status_code == 409
    assert "in progress" in resp.json()["detail"]
    db_session.expire_all()
    # Fully rolled back: rows intact, schedule still enabled, queued run still
    # queued, project still active.
    assert counts(db_session, pid) == before
    assert db_session.get(Schedule, sched.id).enabled is True
    assert db_session.get(Run, queued.id).status == "QUEUED"
    assert db_session.get(Run, running.id).status == "RUNNING"
    assert db_session.get(Project, pid).is_active is True


def test_deletion_succeeds_once_the_running_run_has_finished(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    running = make_run(db_session, test_project, status="RUNNING")
    assert _delete(client, test_project, auth_headers).status_code == 409

    running.status = "CANCELLED"  # what the run's own thread does after a cancel
    db_session.commit()

    assert _delete(client, test_project, auth_headers).status_code == 204
    db_session.expire_all()
    assert counts(db_session, pid) == EMPTY


def test_queued_runs_are_cancelled_and_deleted(client, db_session, test_project, auth_headers):
    pid = test_project.id
    make_run(db_session, test_project, status="QUEUED")
    make_run(db_session, test_project, status="QUEUED")

    assert _delete(client, test_project, auth_headers).status_code == 204

    db_session.expire_all()
    assert counts(db_session, pid) == EMPTY


# ---------------------------------------------------------------------------
# failure / rollback
# ---------------------------------------------------------------------------

def test_database_failure_rolls_everything_back(client, db_session, test_project, auth_headers, tmp_path):
    pid = test_project.id
    build_project_data(db_session, test_project, tmp_path)
    before = counts(db_session, pid)
    seen = []

    def fail_on_file_delete(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement.split()[:3])
        if statement.lstrip().upper().startswith("DELETE FROM PROJECT_FILES"):
            raise RuntimeError("simulated SQL Server failure")

    event.listen(engine, "before_cursor_execute", fail_on_file_delete)
    try:
        with pytest.raises(RuntimeError, match="simulated SQL Server failure"):
            _delete(client, test_project, auth_headers)
    finally:
        event.remove(engine, "before_cursor_execute", fail_on_file_delete)

    # Earlier deletes (screenshots ... runs) really executed before the failure,
    # and were then rolled back: nothing is partially deleted.
    assert any(s[:3] == ["DELETE", "FROM", "runs"] for s in seen)
    db_session.expire_all()
    assert counts(db_session, pid) == before
    assert db_session.get(Project, pid).is_active is True
    assert db_session.scalars(select(Schedule).where(Schedule.project_id == pid)).one().enabled is True


def test_delete_does_not_break_the_file_delete_endpoint_for_other_projects(client, db_session, test_user, test_project, auth_headers, tmp_path):
    pid = test_project.id
    sibling = _new_project(db_session, test_user, "Sibling")
    build_project_data(db_session, sibling, tmp_path / "sib", tag="sib")
    build_project_data(db_session, test_project, tmp_path / "tgt", tag="tgt")
    assert _delete(client, test_project, auth_headers).status_code == 204

    victim = db_session.scalars(select(ProjectFile).where(ProjectFile.project_id == sibling.id)).first()
    resp = client.delete(f"/api/projects/{sibling.id}/files/{victim.id}", headers=auth_headers)

    assert resp.status_code == 204  # historical-result behaviour of file deletion preserved
    db_session.expire_all()
    assert counts(db_session, sibling.id)["files"] == 1
    assert counts(db_session, sibling.id)["test_results"] == 2
