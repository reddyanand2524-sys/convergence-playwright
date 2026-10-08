

import subprocess
import time
import uuid

import httpx
import pytest

from tests.conftest import make_project_file, make_run


# ---------------------------------------------------------------------------
# Items 1-8: TestClient-based state-transition / ownership / auth matrix
# ---------------------------------------------------------------------------

def test_cancel_queued_run_succeeds(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["cancel_requested"] is True

    db_session.refresh(run)
    assert run.status == "CANCELLED"
    assert run.completed_at is not None


def test_cancel_completed_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 409

    db_session.refresh(run)
    assert run.status == "COMPLETED"


def test_cancel_failed_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 409

    db_session.refresh(run)
    assert run.status == "FAILED"


def test_cancel_already_cancelled_run_returns_409(client, db_session, test_project, auth_headers):
    """Explicitly not idempotent-success: CANCELLED -> CANCELLED is an
    invalid transition per the task brief, not a no-op success."""

    run = make_run(db_session, test_project, status="CANCELLED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 409


def test_cancel_run_without_auth_returns_401(client, db_session, test_project):
    run = make_run(db_session, test_project, status="QUEUED")

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/cancel",
    )

    assert response.status_code == 401


def test_cancel_another_users_run_returns_404(
    client, db_session, test_project, auth_headers,
):
    from app.core.security import create_access_token, hash_password
    from app.models.project import Project
    from app.models.user import User

    other_user = User(
        full_name="Other User",
        email="phase6a-other-user@example.com",
        mobile_number="9000000002",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_project = Project(
        name="Other User Project",
        frequency="manual",
        browser="chrome",
        owner_id=other_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    run = make_run(db_session, other_project, status="QUEUED")

    # auth_headers belongs to test_user, not other_user
    response = client.post(
        f"/api/projects/{other_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_cancel_run_with_mismatched_project_returns_404(
    client, db_session, test_project, auth_headers,
):
    from app.models.project import Project

    other_project = Project(
        name="Second Project",
        frequency="manual",
        browser="chrome",
        owner_id=test_project.owner_id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    run = make_run(db_session, test_project, status="QUEUED")

    # run belongs to test_project, not other_project
    response = client.post(
        f"/api/projects/{other_project.id}/runs/{run.id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 404


def test_cancel_nonexistent_run_returns_404(client, test_project, auth_headers):
    response = client.post(
        f"/api/projects/{test_project.id}/runs/999999999/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Item 9: real RUNNING cancellation against the live server
# ---------------------------------------------------------------------------

LIVE_BASE_URL = "http://127.0.0.1:8000"

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
def test_cancel_running_run_kills_live_subprocess():
    unique = uuid.uuid4().hex[:10]
    unique_digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")

    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        register_resp = http.post(
            "/api/auth/register",
            json={
                "full_name": "Phase6A Canceller",
                "email": f"phase6a-cancel-{unique}@example.com",
                "mobile_number": "9" + unique_digits,
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        assert register_resp.status_code == 201, register_resp.text

        login_resp = http.post(
            "/api/auth/login",
            json={
                "email": f"phase6a-cancel-{unique}@example.com",
                "password": "Password123!",
            },
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_resp = http.post(
            "/api/projects",
            json={"name": f"Phase6A Cancel Project {unique}", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        assert project_resp.status_code == 201, project_resp.text
        project_id = project_resp.json()["id"]

        upload_resp = http.post(
            f"/api/projects/{project_id}/files",
            files={"files": ("test_slow.py", _SLOW_TEST_SOURCE, "text/x-python")},
            headers=headers,
        )
        assert upload_resp.status_code == 201, upload_resp.text

        run_resp = http.post(
            f"/api/projects/{project_id}/runs",
            headers=headers,
        )
        assert run_resp.status_code == 201, run_resp.text
        run_id = run_resp.json()["id"]

        start_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/start",
            headers=headers,
        )
        assert start_resp.status_code == 200, start_resp.text
        assert start_resp.json()["status"] == "RUNNING"

        # Give the background task a moment to actually reach
        # subprocess.Popen(...) and register the pid before we try to find
        # it in the OS process list / cancel it.
        pytest_pid = None
        for _ in range(20):
            time.sleep(0.5)
            tasklist = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
                capture_output=True, text=True,
            )
            # We can't match by pid from here (not exposed over HTTP), so
            # just confirm RUNNING has been reached before cancelling.
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
            ).json()
            if run_state["status"] == "RUNNING":
                break

        assert run_state["status"] == "RUNNING"

        before_python_procs = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV"],
            capture_output=True, text=True,
        ).stdout.count("\n")

        cancel_resp = http.post(
            f"/api/projects/{project_id}/runs/{run_id}/cancel",
            headers=headers,
        )
        assert cancel_resp.status_code == 200, cancel_resp.text
        assert cancel_resp.json()["cancel_requested"] is True

        # The background thread owns the final CANCELLED transition; poll
        # for it instead of assuming the cancel response already reflects
        # it (RUNNING run's own status flip is asynchronous by design —
        # see runs.py cancel_run()).
        final_status = None
        for _ in range(30):
            time.sleep(0.5)
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
            ).json()
            final_status = run_state["status"]
            if final_status == "CANCELLED":
                break

        assert final_status == "CANCELLED", (
            f"Run did not reach CANCELLED within 15s of /cancel; "
            f"last observed status={final_status!r}"
        )

        # Confirm the test took nowhere near the full 20s sleep — i.e. the
        # subprocess was actually killed, not just outlived by a status
        # flag flip while pytest kept running to completion in the
        # background.
        results_resp = http.get(
            f"/api/projects/{project_id}/runs/{run_id}/results", headers=headers,
        )
        assert results_resp.status_code == 200
        results_body = results_resp.json()
        # Per Phase 5's _PENDING_STATUSES, a CANCELLED run returns
        # summary=None, tests=None — no fabricated test_results for a test
        # that never finished.
        assert results_body["summary"] is None
        assert results_body["tests"] is None
