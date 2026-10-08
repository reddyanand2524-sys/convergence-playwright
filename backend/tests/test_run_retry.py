

import time
import uuid

import httpx
import pytest

from tests.conftest import make_run


def test_retry_failed_run_creates_new_queued_run(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != run.id
    assert body["status"] == "QUEUED"
    assert body["project_id"] == test_project.id
    assert body["cancel_requested"] is False
    assert body["started_at"] is None
    assert body["completed_at"] is None


def test_retry_does_not_mutate_source_run(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    before = {
        "status": run.status,
        "browser": run.browser,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "cancel_requested": run.cancel_requested,
    }

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )
    assert response.status_code == 201

    db_session.refresh(run)
    assert run.status == before["status"]
    assert run.browser == before["browser"]
    assert run.created_at == before["created_at"]
    assert run.started_at == before["started_at"]
    assert run.completed_at == before["completed_at"]
    assert run.cancel_requested == before["cancel_requested"]


def test_retry_completed_run_succeeds(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["status"] == "QUEUED"


def test_retry_queued_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_retry_running_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="RUNNING")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_retry_cancelled_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="CANCELLED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_retry_copies_browser_from_source_run_not_live_project(
    client, db_session, test_project, auth_headers,
):
    run = make_run(db_session, test_project, status="FAILED", browser="firefox")
    assert test_project.browser == "chrome"

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["browser"] == "firefox"


def test_retry_new_run_has_zero_result_rows(client, db_session, test_project, auth_headers):
    from sqlalchemy import select
    from app.models.run_result import RunResult
    from app.models.test_result import TestResult

    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )
    new_run_id = response.json()["id"]

    run_results = db_session.scalars(
        select(RunResult).where(RunResult.run_id == new_run_id)
    ).all()
    test_results = db_session.scalars(
        select(TestResult).where(TestResult.run_id == new_run_id)
    ).all()
    assert run_results == []
    assert test_results == []


def test_retry_run_without_auth_returns_401(client, db_session, test_project):
    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
    )

    assert response.status_code == 401


def test_retry_nonexistent_project_returns_404(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/999999999/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_retry_nonexistent_run_returns_404(client, test_project, auth_headers):
    response = client.post(
        f"/api/projects/{test_project.id}/runs/999999999/retry",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_retry_run_with_mismatched_project_returns_404(
    client, db_session, test_project, auth_headers,
):
    from app.models.project import Project

    other_project = Project(
        name="Second Project Retry",
        frequency="manual",
        browser="chrome",
        owner_id=test_project.owner_id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/{other_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_retry_another_users_run_returns_404(
    client, db_session, test_project, auth_headers,
):
    from app.core.security import hash_password
    from app.models.project import Project
    from app.models.user import User

    other_user = User(
        full_name="Other User Retry",
        email="phase6b-other-user@example.com",
        mobile_number="9000000003",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_project = Project(
        name="Other User Project Retry",
        frequency="manual",
        browser="chrome",
        owner_id=other_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    run = make_run(db_session, other_project, status="FAILED")

    response = client.post(
        f"/api/projects/{other_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Items 12-13: real start + cancellation of a retried run, against the live
# server (same pattern as test_run_cancellation.py's item 9).
# ---------------------------------------------------------------------------

LIVE_BASE_URL = "http://127.0.0.1:8000"

_FAILING_TEST_SOURCE = '''
def test_always_fails():
    assert False
'''

_SLOW_TEST_SOURCE = '''
import time


def test_sleeps_long_enough_to_be_cancelled():
    time.sleep(20)
    assert True
'''


def _live_server_reachable() -> bool:
    try:
        resp = httpx.get(f"{LIVE_BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(
    not _live_server_reachable(),
    reason="live server not reachable at 127.0.0.1:8000",
)
def test_retried_run_can_be_started_and_executes():
    unique = uuid.uuid4().hex[:10]
    unique_digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")

    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        register_resp = http.post(
            "/api/auth/register",
            json={
                "full_name": "Phase6B Retrier",
                "email": f"phase6b-retry-{unique}@example.com",
                "mobile_number": "9" + unique_digits,
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        assert register_resp.status_code == 201, register_resp.text

        login_resp = http.post(
            "/api/auth/login",
            json={
                "email": f"phase6b-retry-{unique}@example.com",
                "password": "Password123!",
            },
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_resp = http.post(
            "/api/projects",
            json={"name": f"Phase6B Retry Project {unique}", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        assert project_resp.status_code == 201, project_resp.text
        project_id = project_resp.json()["id"]

        upload_resp = http.post(
            f"/api/projects/{project_id}/files",
            files={"files": ("test_fail.py", _FAILING_TEST_SOURCE, "text/x-python")},
            headers=headers,
        )
        assert upload_resp.status_code == 201, upload_resp.text

        run_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
        assert run_resp.status_code == 201, run_resp.text
        run_id = run_resp.json()["id"]

        start_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/start", headers=headers,
        )
        assert start_resp.status_code == 200, start_resp.text

        final_status = None
        for _ in range(40):
            time.sleep(0.5)
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
            ).json()
            final_status = run_state["status"]
            if final_status in ("FAILED", "COMPLETED"):
                break
        assert final_status == "FAILED", f"expected FAILED, got {final_status}"

        retry_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/retry", headers=headers,
        )
        assert retry_resp.status_code == 201, retry_resp.text
        new_run = retry_resp.json()
        assert new_run["id"] != run_id
        assert new_run["status"] == "QUEUED"

        new_start_resp = http.post(
            f"/api/projects/{project_id}/runs/{new_run['id']}/start", headers=headers,
        )
        assert new_start_resp.status_code == 200, new_start_resp.text

        final_new_status = None
        for _ in range(40):
            time.sleep(0.5)
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{new_run['id']}", headers=headers,
            ).json()
            final_new_status = run_state["status"]
            if final_new_status in ("FAILED", "COMPLETED"):
                break
        assert final_new_status == "FAILED"

        # Original run remains unchanged throughout.
        original_state = http.get(
            f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
        ).json()
        assert original_state["status"] == "FAILED"


@pytest.mark.skipif(
    not _live_server_reachable(),
    reason="live server not reachable at 127.0.0.1:8000",
)
def test_retried_run_supports_cancellation():
    unique = uuid.uuid4().hex[:10]
    unique_digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")

    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        register_resp = http.post(
            "/api/auth/register",
            json={
                "full_name": "Phase6B Retry Canceller",
                "email": f"phase6b-retrycancel-{unique}@example.com",
                "mobile_number": "9" + unique_digits,
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        assert register_resp.status_code == 201, register_resp.text

        login_resp = http.post(
            "/api/auth/login",
            json={
                "email": f"phase6b-retrycancel-{unique}@example.com",
                "password": "Password123!",
            },
        )
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_resp = http.post(
            "/api/projects",
            json={"name": f"Phase6B RetryCancel Project {unique}", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        project_id = project_resp.json()["id"]

        upload_resp = http.post(
            f"/api/projects/{project_id}/files",
            files={"files": ("test_fail2.py", _FAILING_TEST_SOURCE, "text/x-python")},
            headers=headers,
        )
        assert upload_resp.status_code == 201

        run_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
        run_id = run_resp.json()["id"]
        start_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/start", headers=headers,
        )
        assert start_resp.status_code == 200

        for _ in range(40):
            time.sleep(0.5)
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
            ).json()
            if run_state["status"] in ("FAILED", "COMPLETED"):
                break
        assert run_state["status"] == "FAILED"

        retry_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/retry", headers=headers,
        )
        new_run_id = retry_resp.json()["id"]

        # Cancel the retried run while still QUEUED.
        cancel_resp = http.post(
            f"/api/projects/{project_id}/runs/{new_run_id}/cancel", headers=headers,
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["status"] == "CANCELLED"

        # Original source run remains unchanged.
        original_state = http.get(
            f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
        ).json()
        assert original_state["status"] == "FAILED"
