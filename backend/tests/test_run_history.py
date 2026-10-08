

import time
import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.test_result import TestResult
from tests.conftest import make_project_file, make_run

LIVE_BASE_URL = "http://127.0.0.1:8000"

_SLOW_TEST_SOURCE = '''
import time


def test_sleeps_long_enough_to_be_cancelled():
    time.sleep(20)
    assert True
'''

_QUICK_FAIL_TEST_SOURCE = '''
def test_quick_failure():
    assert False, "deliberate quick failure for retry regression test"
'''


def _live_server_reachable() -> bool:
    try:
        resp = httpx.get(f"{LIVE_BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _make_run_result(db_session, run, **overrides):
    rr = RunResult(
        run_id=run.id,
        exit_code=overrides.get("exit_code", 0),
        total_tests=overrides.get("total_tests", 0),
        passed_count=overrides.get("passed_count", 0),
        failed_count=overrides.get("failed_count", 0),
        skipped_count=overrides.get("skipped_count", 0),
        stdout=overrides.get("stdout"),
        stderr=overrides.get("stderr"),
    )
    db_session.add(rr)
    db_session.commit()
    db_session.refresh(rr)
    return rr


def _make_test_result(db_session, run, **overrides):
    tr = TestResult(
        run_id=run.id,
        project_file_id=overrides.get("project_file_id"),
        nodeid=overrides.get("nodeid", "test_x.py::test_a"),
        file_name=overrides.get("file_name", "test_x.py"),
        class_name=overrides.get("class_name"),
        method_name=overrides.get("method_name", "test_a"),
        status=overrides.get("status", "PASSED"),
        duration_seconds=overrides.get("duration_seconds"),
    )
    db_session.add(tr)
    db_session.commit()
    db_session.refresh(tr)
    return tr


def _make_screenshot(db_session, test_result, file_name="shot.png"):
    s = Screenshot(
        test_result_id=test_result.id,
        step_result_id=None,
        file_name=file_name,
        mime_type="image/png",
        capture_reason="ALL_MODE",
        content=b"\x89PNG\r\n",
        size_bytes=6,
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


# ---------------------------------------------------------------------------
# Empty history
# ---------------------------------------------------------------------------

def test_empty_history_returns_empty_items(client, test_project, auth_headers):
    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["total_pages"] == 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_pagination_page_size_and_total(client, db_session, test_project, auth_headers):
    for _ in range(5):
        make_run(db_session, test_project, status="QUEUED")

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?page=1&page_size=2",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["total_pages"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 2

    resp2 = client.get(
        f"/api/projects/{test_project.id}/runs?page=3&page_size=2",
        headers=auth_headers,
    )
    body2 = resp2.json()
    assert len(body2["items"]) == 1  # last page, 5 % 2 = 1 remaining


def test_page_beyond_total_pages_returns_empty_items(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED")

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?page=999&page_size=20",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 1


@pytest.mark.parametrize("page", [0, -1])
def test_invalid_page_is_rejected(client, test_project, auth_headers, page):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?page={page}",
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("page_size", [0, -1])
def test_invalid_page_size_is_rejected(client, test_project, auth_headers, page_size):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?page_size={page_size}",
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_excessively_large_page_size_is_rejected(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?page_size=10000",
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_default_pagination_values(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED")
    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 20


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_ordering_created_at_desc_id_desc_stable(client, db_session, test_project, auth_headers):
    # Force identical created_at timestamps to verify the id DESC tiebreaker.
    same_ts = datetime(2026, 1, 1, 12, 0, 0)
    run_a = make_run(db_session, test_project, status="QUEUED")
    run_a.created_at = same_ts
    run_b = make_run(db_session, test_project, status="QUEUED")
    run_b.created_at = same_ts
    db_session.commit()

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    # run_b has the higher id and was created after run_a, so with equal
    # created_at it must sort first under id DESC.
    assert ids.index(run_b.id) < ids.index(run_a.id)


# ---------------------------------------------------------------------------
# Duration calculation
# ---------------------------------------------------------------------------

def test_duration_for_completed_run(client, db_session, test_project, auth_headers):
    started = datetime(2026, 1, 1, 10, 0, 0)
    completed = started + timedelta(seconds=42)
    run = make_run(
        db_session, test_project, status="COMPLETED",
        started_at=started, completed_at=completed,
    )

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["duration_seconds"] == pytest.approx(42.0)


def test_duration_null_for_queued_and_running(client, db_session, test_project, auth_headers):
    queued = make_run(db_session, test_project, status="QUEUED")
    running = make_run(
        db_session, test_project, status="RUNNING",
        started_at=datetime(2026, 1, 1, 10, 0, 0),
    )

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    items_by_id = {i["id"]: i for i in resp.json()["items"]}
    assert items_by_id[queued.id]["duration_seconds"] is None
    assert items_by_id[running.id]["duration_seconds"] is None


def test_duration_for_cancelled_run_with_both_timestamps(client, db_session, test_project, auth_headers):
    started = datetime(2026, 1, 1, 10, 0, 0)
    completed = started + timedelta(seconds=7)
    run = make_run(
        db_session, test_project, status="CANCELLED",
        started_at=started, completed_at=completed,
    )

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["duration_seconds"] == pytest.approx(7.0)


def test_duration_null_for_cancelled_queued_run_never_started(client, db_session, test_project, auth_headers):
    # A QUEUED run cancelled directly gets completed_at but no started_at.
    run = make_run(
        db_session, test_project, status="CANCELLED",
        started_at=None, completed_at=datetime(2026, 1, 1, 10, 0, 0),
    )

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["duration_seconds"] is None


# ---------------------------------------------------------------------------
# Result counts sourced from RunResult
# ---------------------------------------------------------------------------

def test_counts_sourced_from_run_result(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")
    _make_run_result(
        db_session, run,
        total_tests=10, passed_count=7, failed_count=2, skipped_count=1,
    )

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["test_count"] == 10
    assert item["passed_count"] == 7
    assert item["failed_count"] == 2
    assert item["skipped_count"] == 1


def test_counts_zero_when_no_run_result_row(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="CANCELLED")

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["test_count"] == 0
    assert item["passed_count"] == 0
    assert item["failed_count"] == 0
    assert item["skipped_count"] == 0


# ---------------------------------------------------------------------------
# Screenshot count aggregation
# ---------------------------------------------------------------------------

def test_screenshot_count_matches_real_screenshots(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    tr = _make_test_result(db_session, run, status="FAILED")
    _make_screenshot(db_session, tr, "shot1.png")
    _make_screenshot(db_session, tr, "shot2.png")
    _make_screenshot(db_session, tr, "shot3.png")

    other_run = make_run(db_session, test_project, status="COMPLETED")
    other_tr = _make_test_result(db_session, other_run, status="PASSED")
    _make_screenshot(db_session, other_tr, "unrelated.png")

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    items_by_id = {i["id"]: i for i in resp.json()["items"]}
    assert items_by_id[run.id]["screenshot_count"] == 3
    assert items_by_id[other_run.id]["screenshot_count"] == 1


def test_screenshot_count_zero_when_none(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")
    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["screenshot_count"] == 0


# ---------------------------------------------------------------------------
# Ownership / auth
# ---------------------------------------------------------------------------

def test_unauthenticated_returns_401(client, test_project):
    resp = client.get(f"/api/projects/{test_project.id}/runs")
    assert resp.status_code == 401


def test_nonexistent_project_returns_404(client, auth_headers):
    resp = client.get("/api/projects/999999999/runs", headers=auth_headers)
    assert resp.status_code == 404


def test_wrong_owner_project_returns_404(client, db_session, auth_headers):
    from app.core.security import hash_password
    from app.models.project import Project
    from app.models.user import User

    other_user = User(
        full_name="Other User",
        email="other-run-history-user@example.com",
        mobile_number="9000000002",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_project = Project(
        name="Other User's Project",
        frequency="manual",
        browser="chrome",
        owner_id=other_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    resp = client.get(f"/api/projects/{other_project.id}/runs", headers=auth_headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Regression — single-run GET endpoint and the create/start/cancel/retry
# lifecycle are unaffected by this change.
# ---------------------------------------------------------------------------

def test_get_single_run_unaffected(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs/{run.id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == run.id
    assert body["status"] == "QUEUED"
    # single-run response is still the plain RunResponse shape, not an envelope
    assert "items" not in body


@pytest.mark.skipif(
    not _live_server_reachable(),
    reason="live server not reachable at 127.0.0.1:8000",
)
def test_create_start_cancel_retry_still_work():
    # /start triggers a real BackgroundTask (execute_run), which opens its
    # own SessionLocal() against the real engine, independent of any
    # TestClient-held SAVEPOINT transaction. Exercising it through the
    # TestClient `client` fixture would deadlock: the fixture's own
    # uncommitted transaction (held open until test teardown) blocks
    # execute_run's separate connection from reading the very Run row the
    # fixture just modified. Same real-live-server pattern as
    # tests/test_run_cancellation.py's item 9 and tests/test_run_retry.py.
    unique = uuid.uuid4().hex[:10]
    unique_digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")

    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        register_resp = http.post(
            "/api/auth/register",
            json={
                "full_name": "Phase6C History Regression",
                "email": f"phase6c-history-{unique}@example.com",
                "mobile_number": "9" + unique_digits,
                "password": "Password123!",
                "confirm_password": "Password123!",
            },
        )
        assert register_resp.status_code == 201, register_resp.text

        login_resp = http.post(
            "/api/auth/login",
            json={
                "email": f"phase6c-history-{unique}@example.com",
                "password": "Password123!",
            },
        )
        assert login_resp.status_code == 200, login_resp.text
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        project_resp = http.post(
            "/api/projects",
            json={"name": "Phase6C History Regression", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        assert project_resp.status_code == 201, project_resp.text
        project_id = project_resp.json()["id"]

        # A run with zero uploaded files resolves to FAILED almost
        # instantly (execution_service's "no executable files" short
        # circuit) — too fast to reliably still be RUNNING by the time
        # /cancel is called under full-suite timing. Upload a real
        # slow-enough file first, same pattern as
        # test_run_cancellation.py's live RUNNING-cancellation test.
        upload_resp = http.post(
            f"/api/projects/{project_id}/files",
            files={"files": ("test_sleeps.py", _SLOW_TEST_SOURCE, "text/x-python")},
            headers=headers,
        )
        assert upload_resp.status_code == 201, upload_resp.text

        create_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
        assert create_resp.status_code == 201
        run_id = create_resp.json()["id"]

        start_resp = http.post(f"/api/projects/{project_id}/runs/{run_id}/start", headers=headers)
        assert start_resp.status_code == 200
        assert start_resp.json()["status"] == "RUNNING"

        cancel_resp = http.post(f"/api/projects/{project_id}/runs/{run_id}/cancel", headers=headers)
        assert cancel_resp.status_code == 200

        # Wait for the background execute_run thread to actually resolve
        # the Run to a terminal state before retrying a separate, already-
        # terminal run below (unrelated to run_id, but keeps the live
        # server's background thread pool from overlapping test runs).
        for _ in range(15):
            poll_resp = http.get(f"/api/projects/{project_id}/runs/{run_id}", headers=headers)
            if poll_resp.json()["status"] in ("CANCELLED", "COMPLETED", "FAILED"):
                break
            time.sleep(1)

        # Separate project with a quick-failing (not slow) test, so the
        # retry portion doesn't need to wait out the 20s sleep test above.
        project_resp2 = http.post(
            "/api/projects",
            json={"name": "Phase6C History Regression Retry", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        assert project_resp2.status_code == 201, project_resp2.text
        project_id2 = project_resp2.json()["id"]

        upload_resp2 = http.post(
            f"/api/projects/{project_id2}/files",
            files={"files": ("test_quick_fail.py", _QUICK_FAIL_TEST_SOURCE, "text/x-python")},
            headers=headers,
        )
        assert upload_resp2.status_code == 201, upload_resp2.text

        create_resp2 = http.post(f"/api/projects/{project_id2}/runs", headers=headers)
        run_id2 = create_resp2.json()["id"]
        start_resp2 = http.post(f"/api/projects/{project_id2}/runs/{run_id2}/start", headers=headers)
        assert start_resp2.status_code == 200
        for _ in range(15):
            poll_resp2 = http.get(f"/api/projects/{project_id2}/runs/{run_id2}", headers=headers)
            status2 = poll_resp2.json()["status"]
            if status2 in ("COMPLETED", "FAILED"):
                break
            time.sleep(1)
        assert status2 in ("COMPLETED", "FAILED")

        retry_resp = http.post(f"/api/projects/{project_id2}/runs/{run_id2}/retry", headers=headers)
        assert retry_resp.status_code == 201
        assert retry_resp.json()["status"] == "QUEUED"
