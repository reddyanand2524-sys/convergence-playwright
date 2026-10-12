"""Persistence + API tests for the JSON execution report (needs the project's
database, like the other API tests)."""

import json

import pytest
from sqlalchemy import text

from app.services.execution_report import report_from_json
from app.services.result_persistence_service import load_report_json, persist_run_results, report_column_available
from tests.conftest import make_project_file, make_run

pytestmark = pytest.mark.skipif(
    not report_column_available(), reason="run_results.report_json is missing in this database (needs DBA)"
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
NODE = "test_login.py::TestLogin::test_login"


def _events():
    base = {"nodeid": NODE, "target": "", "value": "", "duration": 0.2, "error": "", "screenshot": "",
            "attempted": True, "skip_reason": None, "url": "https://x.test/", "title": "X"}
    return [
        {**base, "step": 1, "action": "open", "status": "PASS", "timestamp": 1_760_000_001.0, "started_at": 1_760_000_000.8},
        {**base, "step": 2, "action": "click", "target": "id=login", "status": "FAIL", "timestamp": 1_760_000_002.0,
         "started_at": 1_760_000_001.8, "error": "NoSuchElementException('Message: no such element')",
         "screenshot": "0002_step_2_1.png"},
        {**base, "step": 3, "action": "open", "status": "PASS", "timestamp": 1_760_000_003.0, "started_at": 1_760_000_002.8},
    ]


def _persist(db_session, project, run, tmp_path):
    make_project_file(db_session, project, "test_login.py", "# stub")
    tmp_path.mkdir(parents=True, exist_ok=True)
    junit = tmp_path / "report.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="1">'
        '<testcase classname="test_login.TestLogin" name="test_login" time="1.0" file="test_login.py">'
        '<failure message="x">test_login.py:10: in test_login\n    boom\nE   boom\n</failure></testcase>'
        "</testsuite></testsuites>", encoding="utf-8")
    (tmp_path / "steps.json").write_text(json.dumps(_events()), encoding="utf-8")
    (tmp_path / "steps_meta.json").write_text(json.dumps({NODE: {"defined_steps": [
        {"number": i, "action": a, "target": t, "value": ""}
        for i, (a, t) in enumerate([("open", ""), ("click", "id=login"), ("open", ""), ("click", "id=never")], start=1)]}}),
        encoding="utf-8")
    shots = tmp_path / "screenshots"
    shots.mkdir()
    (shots / "0002_step_2_1.png").write_bytes(PNG)
    persist_run_results(
        db_session, run, exit_code=1, stdout="", stderr="", junit_report_path=junit,
        steps_json_path=tmp_path / "steps.json", steps_meta_path=tmp_path / "steps_meta.json", screenshots_dir=shots,
    )
    run.status = "FAILED"
    db_session.commit()


def test_persistence_stores_a_valid_report_matching_the_events(db_session, test_project, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, test_project, run, tmp_path)

    stored = load_report_json(db_session, run.id)
    report = report_from_json(stored)
    s = report.summary
    assert (s.totalSteps, s.attemptedSteps, s.passedSteps, s.failedSteps, s.skippedSteps) == (4, 3, 2, 1, 1)
    assert report.status == "FAILED" and report.executionId == f"RUN-{run.id}"
    assert report.steps[3].skipReason.startswith("Not executed")


def test_report_endpoint_returns_valid_json_and_download_header(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, test_project, run, tmp_path)

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/report/json", headers=auth_headers)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/json")
    assert report_from_json(resp.text).summary.failedSteps == 1

    dl = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/report/json?download=true", headers=auth_headers)
    assert "attachment" in dl.headers["content-disposition"]


def test_report_endpoint_errors(client, db_session, test_project, auth_headers):
    queued = make_run(db_session, test_project, status="QUEUED")
    assert client.get(f"/api/projects/{test_project.id}/runs/{queued.id}/report/json", headers=auth_headers).status_code == 409

    failed_without_report = make_run(db_session, test_project, status="FAILED")
    assert client.get(f"/api/projects/{test_project.id}/runs/{failed_without_report.id}/report/json",
                      headers=auth_headers).status_code == 404

    assert client.get(f"/api/projects/{test_project.id}/runs/99999999/report/json", headers=auth_headers).status_code == 404
    assert client.get(f"/api/projects/{test_project.id}/runs/abc/report/json", headers=auth_headers).status_code == 422
    assert client.get(f"/api/projects/{test_project.id}/runs/{queued.id}/report/json").status_code in (401, 403)


def test_corrupt_stored_report_is_never_returned(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, test_project, run, tmp_path)
    db_session.execute(
        text("UPDATE run_results SET report_json = :j WHERE run_id = :r"), {"j": '{"reportVersion": "1.0"}', "r": run.id}
    )
    db_session.commit()
    assert client.get(f"/api/projects/{test_project.id}/runs/{run.id}/report/json", headers=auth_headers).status_code == 500


def test_screenshot_artifact_resolves_report_paths_and_rejects_others(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, test_project, run, tmp_path)
    base = f"/api/projects/{test_project.id}/runs/{run.id}/artifacts/screenshots"

    ok = client.get(f"{base}/0002_step_2_1.png", headers=auth_headers)
    assert ok.status_code == 200 and ok.content[:8] == PNG[:8] and ok.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"{base}/nope.png", headers=auth_headers).status_code == 404
    assert client.get(f"{base}/..%2F..%2Fsecret.png", headers=auth_headers).status_code == 404
    assert client.get(f"{base}/notpng.txt", headers=auth_headers).status_code == 404

    other = make_run(db_session, test_project, status="FAILED")
    assert client.get(
        f"/api/projects/{test_project.id}/runs/{other.id}/artifacts/screenshots/0002_step_2_1.png", headers=auth_headers
    ).status_code == 404  # another run's screenshot is not reachable through this run


def test_repeated_executions_keep_separate_reports(db_session, test_project, tmp_path):
    first = make_run(db_session, test_project, status="RUNNING")
    second = make_run(db_session, test_project, status="RUNNING")
    _persist(db_session, test_project, first, tmp_path / "a")
    _persist(db_session, test_project, second, tmp_path / "b")
    ids = {rid: report_from_json(load_report_json(db_session, rid)).executionId for rid in (first.id, second.id)}
    assert ids == {first.id: f"RUN-{first.id}", second.id: f"RUN-{second.id}"}
