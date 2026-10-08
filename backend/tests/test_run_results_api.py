

from app.core.security import create_access_token, hash_password
from app.models.user import User
from app.services.result_persistence_service import persist_run_results
from tests.conftest import make_project_file, make_run


def _persist_passed(db_session, project, run, tmp_path):
    import json

    make_project_file(db_session, project, "test_login.py", "# stub")
    junit_path = tmp_path / "report.xml"
    junit_path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="1">'
        '<testcase classname="test_login" name="test_valid_login" time="1.0" file="test_login.py"/>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([{
        "nodeid": "test_login.py::test_valid_login", "step": 1, "action": "open",
        "target": "/login", "value": "", "status": "PASS", "duration": 0.1,
        "error": "", "screenshot": "", "timestamp": 1700000000.0,
    }]), encoding="utf-8")

    persist_run_results(
        db_session, run, exit_code=0, stdout="ok", stderr="",
        junit_report_path=junit_path, steps_json_path=steps_path,
        screenshots_dir=tmp_path / "screenshots",
    )
    run.status = "COMPLETED"
    db_session.commit()


def _persist_failed_with_screenshot(db_session, project, run, tmp_path):
    import json

    tmp_path.mkdir(parents=True, exist_ok=True)
    make_project_file(db_session, project, "test_login.py", "# stub")
    junit_path = tmp_path / "report.xml"
    junit_path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="1">'
        '<testcase classname="test_login" name="test_invalid_login" time="0.5" file="test_login.py">'
        '<failure message="x">test_login.py:10: in test_invalid_login\n    assert False\nE   assert False\n</failure>'
        '</testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    screenshot_name = "0001_step_1_1700000000000.png"
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([{
        "nodeid": "test_login.py::test_invalid_login", "step": 1, "action": "click",
        "target": "#submit", "value": "", "status": "FAIL", "duration": 0.2,
        "error": "boom", "screenshot": screenshot_name, "timestamp": 1700000000.0,
    }]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    (screenshots_dir / screenshot_name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    persist_run_results(
        db_session, run, exit_code=1, stdout="", stderr="fail",
        junit_report_path=junit_path, steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )
    run.status = "FAILED"
    db_session.commit()


def _persist_passed_with_all_mode_screenshots(db_session, project, run, tmp_path):
    """Mirrors what a real Run produces now that execution_service sets
    SELENIUM_COMPAT_SCREENSHOT_MODE=all: a PASSED test with a screenshot on
    every (passing) step, each one linked to its own step."""
    import json

    tmp_path.mkdir(parents=True, exist_ok=True)
    make_project_file(db_session, project, "test_login.py", "# stub")
    junit_path = tmp_path / "report.xml"
    junit_path.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="1">'
        '<testcase classname="test_login" name="test_valid_login" time="1.0" file="test_login.py"/>'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )
    shot_1 = "0001_step_1_1700000000000.png"
    shot_2 = "0002_step_2_1700000000100.png"
    steps_path = tmp_path / "steps.json"
    steps_path.write_text(json.dumps([
        {
            "nodeid": "test_login.py::test_valid_login", "step": 1, "action": "open",
            "target": "/login", "value": "", "status": "PASS", "duration": 0.1,
            "error": "", "screenshot": shot_1, "timestamp": 1700000000.0,
        },
        {
            "nodeid": "test_login.py::test_valid_login", "step": 2, "action": "click",
            "target": "#submit", "value": "", "status": "PASS", "duration": 0.1,
            "error": "", "screenshot": shot_2, "timestamp": 1700000000.5,
        },
    ]), encoding="utf-8")
    screenshots_dir = tmp_path / "screenshots"
    screenshots_dir.mkdir()
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (screenshots_dir / shot_1).write_bytes(png_bytes)
    (screenshots_dir / shot_2).write_bytes(png_bytes)

    persist_run_results(
        db_session, run, exit_code=0, stdout="ok", stderr="",
        junit_report_path=junit_path, steps_json_path=steps_path,
        screenshots_dir=screenshots_dir,
    )
    run.status = "COMPLETED"
    db_session.commit()


def test_get_results_passed_run_includes_screenshots_per_step(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist_passed_with_all_mode_screenshots(db_session, test_project, run, tmp_path)

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    test = body["tests"][0]
    assert test["status"] == "PASSED"
    assert len(test["steps"]) == 2
    # Each step gets its own screenshot, correctly associated (not both
    # pointing at the same step, not orphaned under the test only).
    assert len(test["steps"][0]["screenshots"]) == 1
    assert len(test["steps"][1]["screenshots"]) == 1
    assert test["steps"][0]["screenshots"][0]["id"] != test["steps"][1]["screenshots"][0]["id"]
    for step in test["steps"]:
        shot = step["screenshots"][0]
        assert shot["capture_reason"] == "ALL_MODE"
        assert shot["mime_type"] == "image/png"

    screenshot_id = test["steps"][0]["screenshots"][0]["id"]
    shot_resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}/screenshots/{screenshot_id}",
        headers=auth_headers,
    )
    assert shot_resp.status_code == 200
    assert shot_resp.headers["content-type"] == "image/png"
    assert shot_resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_results_valid_completed_run(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist_passed(db_session, test_project, run, tmp_path)

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["id"] == run.id
    assert body["summary"]["total_tests"] == 1
    assert body["summary"]["passed_count"] == 1
    assert len(body["tests"]) == 1
    assert body["tests"][0]["status"] == "PASSED"
    assert len(body["tests"][0]["steps"]) == 1


def test_get_results_failed_run_with_diagnostics(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist_failed_with_screenshot(db_session, test_project, run, tmp_path)

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["failed_count"] == 1
    test = body["tests"][0]
    assert test["status"] == "FAILED"
    assert test["failure"] is not None
    assert test["failure"]["source_line_number"] == 10
    assert len(test["steps"][0]["screenshots"]) == 1


def test_get_results_includes_test_level_screenshot_separately(
    client, db_session, test_project, auth_headers, tmp_path
):
    """A TEST_FAILURE screenshot with step_result_id=NULL (the failure-hook
    screenshot captured at the test level) must appear under
    tests[0].screenshots, NOT duplicated into any step's screenshots list,
    while the existing step-level screenshot keeps appearing under its step."""
from sqlalchemy import select  # noqa: E402


def _get_test_row(db_session, run):
    from app.models.test_result import TestResult
    return db_session.scalars(
        select(TestResult).where(TestResult.run_id == run.id)
    ).first()


def test_get_results_includes_test_level_screenshot_separately(
    client, db_session, test_project, auth_headers, tmp_path
):
    """A TEST_FAILURE screenshot with step_result_id=NULL (the failure-hook
    screenshot captured at the test level) must appear under
    tests[0].screenshots, NOT duplicated into any step's screenshots list,
    while the existing step-level screenshot keeps appearing under its step."""
    from app.models.screenshot import Screenshot

    run = make_run(db_session, test_project, status="RUNNING")
    _persist_failed_with_screenshot(db_session, test_project, run, tmp_path)

    test_row = _get_test_row(db_session, run)

    test_level_shot = Screenshot(
        test_result_id=test_row.id,
        step_result_id=None,
        file_name="test_failure_hook.png",
        mime_type="image/png",
        capture_reason="TEST_FAILURE",
        content=b"\x89PNG\r\n\x1a\n" + b"\x01" * 16,
        size_bytes=24,
    )
    db_session.add(test_level_shot)
    db_session.commit()
    db_session.refresh(test_level_shot)

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    test = body["tests"][0]

    # Step-level screenshot still appears under its step.
    assert len(test["steps"][0]["screenshots"]) == 1
    step_shot_id = test["steps"][0]["screenshots"][0]["id"]
    assert step_shot_id != test_level_shot.id

    # Test-level screenshot appears under the new tests[].screenshots field.
    assert len(test["screenshots"]) == 1
    assert test["screenshots"][0]["id"] == test_level_shot.id
    assert test["screenshots"][0]["capture_reason"] == "TEST_FAILURE"

    # Not duplicated into any step's screenshots.
    for step in test["steps"]:
        ids_in_step = [s["id"] for s in step["screenshots"]]
        assert test_level_shot.id not in ids_in_step

    # The screenshot-binary endpoint still works for this test-level shot.
    shot_resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}/screenshots/{test_level_shot.id}",
        headers=auth_headers,
    )
    assert shot_resp.status_code == 200
    assert shot_resp.headers["content-type"] == "image/png"
    assert shot_resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Auth/ownership rules remain intact for the whole results call.
    other_user = User(
        full_name="Other3", email="other3.phase5@example.com",
        mobile_number="9000000003", password_hash=hash_password("x"), is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_headers = {"Authorization": f"Bearer {create_access_token(other_user.id)}"}
    resp2 = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=other_headers)
    assert resp2.status_code == 404


def test_get_results_missing_auth_header(client, test_project):
    run_id = 1
    resp = client.get(f"/api/projects/{test_project.id}/runs/{run_id}/results")
    assert resp.status_code in (401, 403)


def test_get_results_nonexistent_project(client, auth_headers):
    resp = client.get("/api/projects/999999999/runs/1/results", headers=auth_headers)
    assert resp.status_code == 404


def test_get_results_project_owned_by_different_user(client, db_session, test_project, tmp_path):
    other_user = User(
        full_name="Other User", email="other.phase5@example.com",
        mobile_number="9000000000", password_hash=hash_password("x"), is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_headers = {"Authorization": f"Bearer {create_access_token(other_user.id)}"}

    run = make_run(db_session, test_project, status="QUEUED")

    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=other_headers)
    assert resp.status_code == 404


def test_get_results_nonexistent_run(client, test_project, auth_headers):
    resp = client.get(f"/api/projects/{test_project.id}/runs/999999999/results", headers=auth_headers)
    assert resp.status_code == 404


def test_get_results_run_belonging_to_different_project(client, db_session, test_user, auth_headers, tmp_path):
    from app.models.project import Project

    project_a = Project(name="A", frequency="manual", browser="chrome", owner_id=test_user.id, is_active=True)
    project_b = Project(name="B", frequency="manual", browser="chrome", owner_id=test_user.id, is_active=True)
    db_session.add_all([project_a, project_b])
    db_session.commit()
    db_session.refresh(project_a)
    db_session.refresh(project_b)

    run = make_run(db_session, project_a, status="QUEUED")

    resp = client.get(f"/api/projects/{project_b.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 404


def test_get_results_queued_run_has_null_summary(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")
    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] is None
    assert body["tests"] is None


def test_get_results_running_run_has_null_summary(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="RUNNING")
    resp = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["summary"] is None


# ---------------------------------------------------------------------------
# Screenshot endpoint
# ---------------------------------------------------------------------------

def test_get_screenshot_valid(client, db_session, test_project, auth_headers, tmp_path):
    run = make_run(db_session, test_project, status="RUNNING")
    _persist_failed_with_screenshot(db_session, test_project, run, tmp_path)

    results = client.get(f"/api/projects/{test_project.id}/runs/{run.id}/results", headers=auth_headers).json()
    screenshot_id = results["tests"][0]["steps"][0]["screenshots"][0]["id"]

    resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}/screenshots/{screenshot_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_screenshot_missing_auth(client, test_project):
    resp = client.get(f"/api/projects/{test_project.id}/runs/1/screenshots/1")
    assert resp.status_code in (401, 403)


def test_get_screenshot_nonexistent_project(client, auth_headers):
    resp = client.get("/api/projects/999999999/runs/1/screenshots/1", headers=auth_headers)
    assert resp.status_code == 404


def test_get_screenshot_wrong_ownership(client, db_session, test_project, tmp_path):
    other_user = User(
        full_name="Other2", email="other2.phase5@example.com",
        mobile_number="9000000002", password_hash=hash_password("x"), is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)
    other_headers = {"Authorization": f"Bearer {create_access_token(other_user.id)}"}

    run = make_run(db_session, test_project, status="QUEUED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}/screenshots/1",
        headers=other_headers,
    )
    assert resp.status_code == 404


def test_get_screenshot_nonexistent_run(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs/999999999/screenshots/1",
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_get_screenshot_nonexistent_screenshot_id(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}/screenshots/999999999",
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_get_screenshot_belonging_to_different_run_is_404(client, db_session, test_project, auth_headers, tmp_path):
    run_a = make_run(db_session, test_project, status="RUNNING")
    _persist_failed_with_screenshot(db_session, test_project, run_a, tmp_path / "a")

    run_b = make_run(db_session, test_project, status="QUEUED")

    results = client.get(f"/api/projects/{test_project.id}/runs/{run_a.id}/results", headers=auth_headers).json()
    screenshot_id = results["tests"][0]["steps"][0]["screenshots"][0]["id"]

    # Same screenshot id, but requested under run_b's URL -> must 404.
    resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run_b.id}/screenshots/{screenshot_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 404
