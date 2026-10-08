

import time
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models.run import Run
from app.models.schedule import Schedule
from app.services.scheduler_service import run_due_schedules_once
from tests.conftest import make_run


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# A. CRUD
# ---------------------------------------------------------------------------

def test_create_schedule_once(client, test_project, auth_headers):
    start = datetime.utcnow() + timedelta(hours=1)
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Nightly smoke",
            "browser": "chrome",
            "frequency": "ONCE",
            "start_at": _iso(start),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["project_id"] == test_project.id
    assert body["frequency"] == "ONCE"
    assert body["interval_value"] is None
    assert body["enabled"] is True
    assert body["next_run_at"] is not None
    assert body["last_run_at"] is None


def test_create_schedule_interval(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Every 5 minutes",
            "browser": "chrome",
            "frequency": "INTERVAL",
            "interval_value": 5,
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["interval_value"] == 5


def test_client_cannot_set_next_run_at_directly(client, test_project, auth_headers):
    start = datetime.utcnow() + timedelta(hours=2)
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Try to cheat",
            "browser": "chrome",
            "frequency": "ONCE",
            "start_at": _iso(start),
            "next_run_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    # next_run_at ignored from input, computed server-side == start_at
    body = resp.json()
    assert body["next_run_at"][:19] == _iso(start)[:19]


def test_list_schedules(client, test_project, auth_headers):
    for i in range(3):
        client.post(
            f"/api/projects/{test_project.id}/schedules",
            json={
                "name": f"S{i}",
                "browser": "chrome",
                "frequency": "HOURLY",
                "start_at": _iso(datetime.utcnow()),
            },
            headers=auth_headers,
        )
    resp = client.get(f"/api/projects/{test_project.id}/schedules", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 3


def test_get_schedule(client, test_project, auth_headers):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Daily",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()
    resp = client.get(
        f"/api/projects/{test_project.id}/schedules/{create['id']}", headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == create["id"]


def test_get_nonexistent_schedule_404(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/schedules/99999999", headers=auth_headers,
    )
    assert resp.status_code == 404


def test_patch_schedule_updates_fields(client, test_project, auth_headers):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Weekly",
            "browser": "chrome",
            "frequency": "WEEKLY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()

    resp = client.patch(
        f"/api/projects/{test_project.id}/schedules/{create['id']}",
        json={"name": "Weekly Renamed", "browser": "firefox"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Weekly Renamed"
    assert body["browser"] == "firefox"


def test_patch_schedule_recomputes_next_run_at_on_frequency_change(
    client, test_project, auth_headers,
):
    start = datetime.utcnow()
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Reschedule me",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(start),
        },
        headers=auth_headers,
    ).json()

    new_start = start + timedelta(days=10)
    resp = client.patch(
        f"/api/projects/{test_project.id}/schedules/{create['id']}",
        json={"start_at": _iso(new_start)},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["next_run_at"][:19] == _iso(new_start)[:19]


def test_patch_interval_without_value_rejected_when_switching_to_interval(
    client, test_project, auth_headers,
):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Switch to interval",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()

    resp = client.patch(
        f"/api/projects/{test_project.id}/schedules/{create['id']}",
        json={"frequency": "INTERVAL"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_delete_schedule(client, db_session, test_project, auth_headers):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "To delete",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()

    resp = client.delete(
        f"/api/projects/{test_project.id}/schedules/{create['id']}", headers=auth_headers,
    )
    assert resp.status_code == 204

    resp2 = client.get(
        f"/api/projects/{test_project.id}/schedules/{create['id']}", headers=auth_headers,
    )
    assert resp2.status_code == 404


def test_delete_schedule_does_not_cascade_to_run(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")

    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "With a run",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()

    schedule = db_session.get(Schedule, create["id"])
    schedule.last_run_id = run.id
    db_session.add(schedule)
    db_session.commit()

    resp = client.delete(
        f"/api/projects/{test_project.id}/schedules/{create['id']}", headers=auth_headers,
    )
    assert resp.status_code == 204

    # Run row untouched.
    still_there = db_session.get(Run, run.id)
    assert still_there is not None
    assert still_there.status == "COMPLETED"


# ---------------------------------------------------------------------------
# B. Validation
# ---------------------------------------------------------------------------

def test_invalid_frequency_rejected(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Bad",
            "browser": "chrome",
            "frequency": "YEARLY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_interval_requires_interval_value(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Bad interval",
            "browser": "chrome",
            "frequency": "INTERVAL",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_interval_value_must_be_positive(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Bad interval value",
            "browser": "chrome",
            "frequency": "INTERVAL",
            "interval_value": 0,
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_interval_value_rejected_for_non_interval_frequency(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Bad",
            "browser": "chrome",
            "frequency": "DAILY",
            "interval_value": 5,
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_missing_required_fields_rejected(client, test_project, auth_headers):
    resp = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={"browser": "chrome", "frequency": "DAILY"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_create_schedule_for_nonexistent_project_404(client, auth_headers):
    resp = client.post(
        "/api/projects/999999999/schedules",
        json={
            "name": "Ghost Project Schedule",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# C. Enable/disable
# ---------------------------------------------------------------------------

def test_disable_then_enable_schedule(client, test_project, auth_headers):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Toggle",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow()),
        },
        headers=auth_headers,
    ).json()

    disable_resp = client.post(
        f"/api/projects/{test_project.id}/schedules/{create['id']}/disable", headers=auth_headers,
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["enabled"] is False

    enable_resp = client.post(
        f"/api/projects/{test_project.id}/schedules/{create['id']}/enable", headers=auth_headers,
    )
    assert enable_resp.status_code == 200
    assert enable_resp.json()["enabled"] is True


def test_disabled_schedule_skipped_by_scheduler_even_if_due(
    client, db_session, test_project, auth_headers,
):
    create = client.post(
        f"/api/projects/{test_project.id}/schedules",
        json={
            "name": "Due but disabled",
            "browser": "chrome",
            "frequency": "DAILY",
            "start_at": _iso(datetime.utcnow() - timedelta(minutes=5)),
        },
        headers=auth_headers,
    ).json()

    client.post(
        f"/api/projects/{test_project.id}/schedules/{create['id']}/disable", headers=auth_headers,
    )

    created_run_ids = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert created_run_ids == []


# ---------------------------------------------------------------------------
# D. Run-now
# ---------------------------------------------------------------------------

# NOTE: run-now schedules execution_service.execute_run as a real
# BackgroundTask, same as /start — per the documented environment gotcha,
# letting TestClient run that BackgroundTask synchronously deadlocks
# against the SAVEPOINT-rollback db_session fixture's open transaction.
# So, same as test_run_retry.py's item 12-13, these run against the real
# live uvicorn server over real HTTP instead of the `client` TestClient
# fixture.

LIVE_BASE_URL = "http://127.0.0.1:8000"


def _live_server_reachable() -> bool:
    try:
        resp = httpx.get(f"{LIVE_BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _register_and_login(http: httpx.Client, unique: str) -> dict:
    digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")
    reg = http.post(
        "/api/auth/register",
        json={
            "full_name": "Phase8 Scheduler User",
            "email": f"phase8-sched-{unique}@example.com",
            "mobile_number": "9" + digits,
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
    )
    assert reg.status_code == 201, reg.text
    login = http.post(
        "/api/auth/login",
        json={"email": f"phase8-sched-{unique}@example.com", "password": "Password123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.skipif(not _live_server_reachable(), reason="live server not reachable at 127.0.0.1:8000")
def test_run_now_creates_run_without_touching_timing_live():
    unique = uuid.uuid4().hex[:10]
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers = _register_and_login(http, unique)

        project_resp = http.post(
            "/api/projects",
            json={"name": f"Phase8 Project {unique}", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        assert project_resp.status_code == 201, project_resp.text
        project_id = project_resp.json()["id"]

        start = datetime.utcnow() + timedelta(days=1)
        create_resp = http.post(
            f"/api/projects/{project_id}/schedules",
            json={
                "name": "Run me now",
                "browser": "chrome",
                "frequency": "DAILY",
                "start_at": _iso(start),
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, create_resp.text
        schedule = create_resp.json()
        before_next_run_at = schedule["next_run_at"]

        run_resp = http.post(
            f"/api/projects/{project_id}/schedules/{schedule['id']}/run-now", headers=headers,
        )
        assert run_resp.status_code == 201, run_resp.text
        run_body = run_resp.json()
        assert run_body["status"] == "RUNNING"
        assert run_body["browser"] == "chrome"

        after = http.get(
            f"/api/projects/{project_id}/schedules/{schedule['id']}", headers=headers,
        ).json()
        assert after["next_run_at"] == before_next_run_at

        # Wait for execution to actually finish so the second run-now check
        # (and server state) is deterministic.
        for _ in range(40):
            time.sleep(0.5)
            run_state = http.get(
                f"/api/projects/{project_id}/runs/{run_body['id']}", headers=headers,
            ).json()
            if run_state["status"] in ("FAILED", "COMPLETED"):
                break


@pytest.mark.skipif(not _live_server_reachable(), reason="live server not reachable at 127.0.0.1:8000")
def test_run_now_rejects_second_call_while_first_still_active_live():
    unique = uuid.uuid4().hex[:10]
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers = _register_and_login(http, unique)

        project_resp = http.post(
            "/api/projects",
            json={"name": f"Phase8 NoStack Project {unique}", "frequency": "manual", "browser": "chrome"},
            headers=headers,
        )
        project_id = project_resp.json()["id"]

        # Upload a slow test so the first run-now stays RUNNING long enough
        # for the second call to observe it as still active.
        slow_source = (
            "import time\n\n\n"
            "def test_slow():\n"
            "    time.sleep(10)\n"
            "    assert True\n"
        )
        upload_resp = http.post(
            f"/api/projects/{project_id}/files",
            files={"files": ("test_slow.py", slow_source, "text/x-python")},
            headers=headers,
        )
        assert upload_resp.status_code == 201, upload_resp.text

        create_resp = http.post(
            f"/api/projects/{project_id}/schedules",
            json={
                "name": "No stacking",
                "browser": "chrome",
                "frequency": "DAILY",
                "start_at": _iso(datetime.utcnow()),
            },
            headers=headers,
        )
        schedule_id = create_resp.json()["id"]

        first = http.post(
            f"/api/projects/{project_id}/schedules/{schedule_id}/run-now", headers=headers,
        )
        assert first.status_code == 201, first.text

        second = http.post(
            f"/api/projects/{project_id}/schedules/{schedule_id}/run-now", headers=headers,
        )
        assert second.status_code == 409


# ---------------------------------------------------------------------------
# E. Security / ownership
# ---------------------------------------------------------------------------

def _make_other_user_project(db_session, suffix: str):
    from app.core.security import hash_password
    from app.models.project import Project
    from app.models.user import User

    user = User(
        full_name="Other Schedule User",
        email=f"phase8-other-{suffix}@example.com",
        mobile_number=f"91000000{suffix[-2:]}",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name="Other User Project",
        frequency="manual",
        browser="chrome",
        owner_id=user.id,
        is_active=True,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


def test_cross_user_cannot_access_schedule(client, db_session, auth_headers):
    unique = uuid.uuid4().hex[:8]
    other_user, other_project = _make_other_user_project(db_session, unique)

    schedule = Schedule(
        project_id=other_project.id,
        name="Private",
        browser="chrome",
        frequency="DAILY",
        interval_value=None,
        start_at=datetime.utcnow(),
        next_run_at=datetime.utcnow(),
        enabled=True,
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    base = f"/api/projects/{other_project.id}/schedules/{schedule.id}"
    assert client.get(base, headers=auth_headers).status_code == 404
    assert client.patch(base, json={"name": "hacked"}, headers=auth_headers).status_code == 404
    assert client.delete(base, headers=auth_headers).status_code == 404
    assert client.post(f"{base}/enable", headers=auth_headers).status_code == 404
    assert client.post(f"{base}/disable", headers=auth_headers).status_code == 404
    assert client.post(f"{base}/run-now", headers=auth_headers).status_code == 404


def test_schedule_endpoints_require_auth(client, test_project):
    resp = client.get(f"/api/projects/{test_project.id}/schedules")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# F. Scheduler due-processing logic
# ---------------------------------------------------------------------------

def test_due_once_schedule_creates_run_and_disables(client, db_session, test_project, auth_headers):
    schedule = Schedule(
        project_id=test_project.id,
        name="Due once",
        browser="chrome",
        frequency="ONCE",
        interval_value=None,
        start_at=datetime.utcnow() - timedelta(minutes=1),
        next_run_at=datetime.utcnow() - timedelta(minutes=1),
        enabled=True,
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert len(created) == 1

    db_session.refresh(schedule)
    assert schedule.enabled is False
    assert schedule.last_run_id == created[0]
    assert schedule.last_run_at is not None

    run = db_session.get(Run, created[0])
    assert run.project_id == test_project.id
    assert run.browser == "chrome"
    assert run.status in ("RUNNING", "COMPLETED", "FAILED")


def test_due_interval_schedule_advances_next_run_at_and_stays_enabled(
    db_session, test_project,
):
    now = datetime.utcnow()
    schedule = Schedule(
        project_id=test_project.id,
        name="Due interval",
        browser="chrome",
        frequency="INTERVAL",
        interval_value=10,
        start_at=now - timedelta(minutes=1),
        next_run_at=now - timedelta(minutes=1),
        enabled=True,
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert len(created) == 1

    db_session.refresh(schedule)
    assert schedule.enabled is True
    assert schedule.next_run_at > now + timedelta(minutes=9)


def test_not_yet_due_schedule_is_skipped(db_session, test_project):
    schedule = Schedule(
        project_id=test_project.id,
        name="Future",
        browser="chrome",
        frequency="DAILY",
        interval_value=None,
        start_at=datetime.utcnow() + timedelta(hours=1),
        next_run_at=datetime.utcnow() + timedelta(hours=1),
        enabled=True,
    )
    db_session.add(schedule)
    db_session.commit()

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert created == []


def test_multiple_schedules_independent_no_cross_project_contamination(
    db_session, test_project, test_user,
):
    from app.models.project import Project

    other_project = Project(
        name="Second Project",
        frequency="manual",
        browser="firefox",
        owner_id=test_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    now = datetime.utcnow() - timedelta(minutes=1)

    s1 = Schedule(
        project_id=test_project.id, name="S1", browser="chrome",
        frequency="DAILY", interval_value=None, start_at=now, next_run_at=now, enabled=True,
    )
    s2 = Schedule(
        project_id=other_project.id, name="S2", browser="firefox",
        frequency="DAILY", interval_value=None, start_at=now, next_run_at=now, enabled=True,
    )
    db_session.add_all([s1, s2])
    db_session.commit()
    db_session.refresh(s1)
    db_session.refresh(s2)

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert len(created) == 2

    run1 = db_session.get(Run, s1.last_run_id)
    run2 = db_session.get(Run, s2.last_run_id)
    assert run1.project_id == test_project.id
    assert run1.browser == "chrome"
    assert run2.project_id == other_project.id
    assert run2.browser == "firefox"


# ---------------------------------------------------------------------------
# G. Duplicate-prevention / overlap concurrency
# ---------------------------------------------------------------------------

def test_overlap_skips_schedule_whose_previous_run_still_active(db_session, test_project):
    active_run = make_run(db_session, test_project, status="RUNNING")

    now = datetime.utcnow() - timedelta(minutes=1)
    schedule = Schedule(
        project_id=test_project.id, name="Overlap", browser="chrome",
        frequency="DAILY", interval_value=None, start_at=now, next_run_at=now,
        enabled=True, last_run_id=active_run.id,
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert created == []

    db_session.refresh(schedule)
    # Not advanced (within SQL Server DATETIME's ~3ms rounding) — stays due,
    # will be retried once the active run finishes.
    assert abs((schedule.next_run_at - now).total_seconds()) < 0.01


def test_overlap_allows_new_run_once_previous_run_terminal(db_session, test_project):
    finished_run = make_run(db_session, test_project, status="COMPLETED")

    now = datetime.utcnow() - timedelta(minutes=1)
    schedule = Schedule(
        project_id=test_project.id, name="No longer overlapping", browser="chrome",
        frequency="DAILY", interval_value=None, start_at=now, next_run_at=now,
        enabled=True, last_run_id=finished_run.id,
    )
    db_session.add(schedule)
    db_session.commit()

    created = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    assert len(created) == 1


def test_concurrent_due_processing_claims_schedule_exactly_once(db_session, test_project, engine_for_second_session=None):
    """Simulates two near-simultaneous scheduler ticks racing on the same
    due schedule using two independent raw connections (not the shared
    SAVEPOINT-rollback db_session, since that session alone can't model two
    concurrent DB transactions) against the same schedule row. Because the
    outer test transaction (db_session's connection) has not committed, a
    genuinely separate connection cannot see the row yet under default
    isolation — so this test instead verifies the conditional-UPDATE
    mechanism directly: calling run_due_schedules_once twice in a row against
    the SAME session must only ever create one Run for one due schedule,
    since the first call's UPDATE already advances next_run_at past "now".
    """
    now = datetime.utcnow() - timedelta(minutes=1)
    schedule = Schedule(
        project_id=test_project.id, name="Race", browser="chrome",
        frequency="DAILY", interval_value=None, start_at=now, next_run_at=now,
        enabled=True,
    )
    db_session.add(schedule)
    db_session.commit()

    created_first = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)
    created_second = run_due_schedules_once(db_session, execute_fn=lambda run_id: None)

    assert len(created_first) == 1
    assert created_second == []
