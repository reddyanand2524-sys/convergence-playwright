"""DELETE /api/projects/{project_id}/files/{file_id}.

Regression: deleting a file that a past Run had executed returned HTTP 500,
because test_results.project_file_id is a foreign key to project_files.id and
the DELETE violated it (SQL Server error 547). Deleting such a file must now
succeed while keeping every historical result.
"""
import json

from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models.project_file import ProjectFile
from app.models.test_result import TestResult
from app.models.user import User
from app.services.result_persistence_service import persist_run_results
from tests.conftest import make_project_file, make_run


def _persist(db_session, run, tmp_path, file_names):
    """Persists a PASSED result per file name, the way a real Run would."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cases, steps = "", []
    for name in file_names:
        stem = name[: -len(".py")]
        cases += f'<testcase classname="{stem}" name="test_ok" time="0.1" file="{name}"/>'
        steps.append({
            "nodeid": f"{name}::test_ok", "step": 1, "action": "open", "target": "/",
            "value": "", "status": "PASS", "duration": 0.1, "error": "",
            "screenshot": "", "timestamp": 1700000000.0,
        })
    junit_path = tmp_path / "report.xml"
    junit_path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite tests="{len(file_names)}">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps(steps), encoding="utf-8")
    persist_run_results(
        db_session, run, exit_code=0, stdout="ok", stderr="",
        junit_report_path=junit_path, steps_json_path=steps_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    run.status = "COMPLETED"
    db_session.commit()


def _results_for(db_session, run):
    return db_session.scalars(select(TestResult).where(TestResult.run_id == run.id)).all()


def _other_user(db_session):
    user = User(
        full_name="Other User", email="other.filedelete@example.com",
        mobile_number="9000000077", password_hash=hash_password("x"), is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _url(project, file):
    return f"/api/projects/{project.id}/files/{file.id}"


def test_delete_never_executed_file(client, db_session, test_project, auth_headers):
    target = make_project_file(db_session, test_project, "test_login.py", "# a")
    keep = make_project_file(db_session, test_project, "test_other.py", "# b")

    assert client.delete(_url(test_project, target), headers=auth_headers).status_code == 204

    remaining = db_session.scalars(
        select(ProjectFile).where(ProjectFile.project_id == test_project.id)
    ).all()
    assert [f.id for f in remaining] == [keep.id]


def test_delete_file_referenced_by_past_results_succeeds(
    client, db_session, test_project, auth_headers, tmp_path
):
    """The reported bug: this used to return 500 (FK violation)."""
    target = make_project_file(db_session, test_project, "test_login.py", "# a")
    keep = make_project_file(db_session, test_project, "test_other.py", "# b")
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, run, tmp_path, ["test_login.py", "test_other.py"])

    before = {r.file_name: r for r in _results_for(db_session, run)}
    assert before["test_login.py"].project_file_id == target.id  # precondition: really linked

    resp = client.delete(_url(test_project, target), headers=auth_headers)
    assert resp.status_code == 204

    db_session.expire_all()
    # File row gone, the other file untouched.
    assert db_session.get(ProjectFile, target.id) is None
    assert db_session.get(ProjectFile, keep.id) is not None
    # History kept: same number of results, deleted file's result only lost its link.
    after = {r.file_name: r for r in _results_for(db_session, run)}
    assert set(after) == {"test_login.py", "test_other.py"}
    assert after["test_login.py"].project_file_id is None
    assert after["test_login.py"].status == before["test_login.py"].status
    assert after["test_other.py"].project_file_id == keep.id
    # The historical results API still works for that run.
    results = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert results.status_code == 200
    assert {t["file_name"] for t in results.json()["tests"]} == {"test_login.py", "test_other.py"}


def test_remaining_files_still_execute_after_delete(
    client, db_session, test_project, auth_headers, tmp_path
):
    target = make_project_file(db_session, test_project, "test_login.py", "# a")
    keep = make_project_file(db_session, test_project, "test_other.py", "# b")
    first = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, first, tmp_path / "first", ["test_login.py", "test_other.py"])
    assert client.delete(_url(test_project, target), headers=auth_headers).status_code == 204

    # A later run only sees the remaining file and links its result to it.
    second = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, second, tmp_path / "second", ["test_other.py"])
    db_session.expire_all()
    results = _results_for(db_session, second)
    assert [(r.file_name, r.project_file_id) for r in results] == [("test_other.py", keep.id)]

    # Deleting it a second time is a clean 404, not an error.
    assert client.delete(_url(test_project, target), headers=auth_headers).status_code == 404


def test_delete_nonexistent_file_returns_404(client, db_session, test_project, auth_headers):
    keep = make_project_file(db_session, test_project, "test_other.py", "# b")
    resp = client.delete(f"/api/projects/{test_project.id}/files/999999999", headers=auth_headers)
    assert resp.status_code == 404
    assert db_session.get(ProjectFile, keep.id) is not None


def test_delete_file_in_nonexistent_project_returns_404(client, auth_headers):
    assert client.delete("/api/projects/999999999/files/1", headers=auth_headers).status_code == 404


def test_delete_file_from_another_users_project_is_denied(
    client, db_session, test_project, auth_headers, tmp_path
):
    target = make_project_file(db_session, test_project, "test_login.py", "# a")
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, run, tmp_path, ["test_login.py"])

    resp = client.delete(_url(test_project, target), headers=_other_user(db_session))
    assert resp.status_code == 404

    db_session.expire_all()
    assert db_session.get(ProjectFile, target.id) is not None
    assert _results_for(db_session, run)[0].project_file_id == target.id  # link untouched


def test_delete_file_requires_authentication(client, db_session, test_project):
    target = make_project_file(db_session, test_project, "test_login.py", "# a")
    assert client.delete(_url(test_project, target)).status_code in (401, 403)
    assert db_session.get(ProjectFile, target.id) is not None


def test_delete_file_wrong_project_for_file_returns_404(client, db_session, test_user, test_project, auth_headers):
    """A file id that belongs to a different project of the same user must not be deletable via this project."""
    from app.models.project import Project

    other_project = Project(
        name="Second Project", frequency="manual", browser="chrome",
        owner_id=test_user.id, is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)
    foreign = make_project_file(db_session, other_project, "test_login.py", "# a")

    resp = client.delete(f"/api/projects/{test_project.id}/files/{foreign.id}", headers=auth_headers)
    assert resp.status_code == 404
    assert db_session.get(ProjectFile, foreign.id) is not None
