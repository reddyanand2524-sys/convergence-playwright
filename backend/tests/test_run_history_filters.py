

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.database import engine
from tests.conftest import make_run


# ---------------------------------------------------------------------------
# A. No-filter baseline
# ---------------------------------------------------------------------------

def test_no_filter_baseline_returns_all(client, db_session, test_project, auth_headers):
    for _ in range(3):
        make_run(db_session, test_project, status="QUEUED")

    resp = client.get(f"/api/projects/{test_project.id}/runs", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# B. status filter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value", ["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
)
def test_status_filter_each_value(client, db_session, test_project, auth_headers, value):
    match = make_run(db_session, test_project, status=value)
    # a run of a different status that must not show up
    other_status = "QUEUED" if value != "QUEUED" else "FAILED"
    make_run(db_session, test_project, status=other_status)

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status={value}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == match.id
    assert body["items"][0]["status"] == value


def test_status_filter_invalid_value_422(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status=NOT_A_STATUS", headers=auth_headers
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# C. browser filter
# ---------------------------------------------------------------------------

def test_browser_filter_exact_match(client, db_session, test_project, auth_headers):
    chrome_run = make_run(db_session, test_project, status="QUEUED", browser="chrome")
    make_run(db_session, test_project, status="QUEUED", browser="firefox")

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?browser=chrome", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["id"] == chrome_run.id


def test_browser_filter_no_match_returns_empty(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED", browser="chrome")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?browser=safari", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# D. run_id filter
# ---------------------------------------------------------------------------

def test_run_id_filter_existing(client, db_session, test_project, auth_headers):
    run_a = make_run(db_session, test_project, status="QUEUED")
    make_run(db_session, test_project, status="QUEUED")

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?run_id={run_a.id}", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["id"] == run_a.id


def test_run_id_filter_nonexistent_returns_empty(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?run_id=999999999", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.parametrize("bad_run_id", [0, -1])
def test_run_id_filter_invalid_422(client, test_project, auth_headers, bad_run_id):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?run_id={bad_run_id}", headers=auth_headers
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# E/F/G. search
# ---------------------------------------------------------------------------

def test_search_matches_browser_case_insensitive(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED", browser="chrome")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?search=CHR", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["id"] == run.id


def test_search_matches_status_case_insensitive(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="FAILED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?search=fail", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["id"] == run.id


def test_search_matches_run_id_when_numeric(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED", browser="opera")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?search={run.id}", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert any(item["id"] == run.id for item in body["items"])


def test_search_non_numeric_does_not_crash(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED", browser="chrome")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?search=not-a-number-at-all",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_search_no_match_returns_empty(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED", browser="chrome")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?search=zzzznomatch", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# H/I/J. created_from / created_to boundaries
# ---------------------------------------------------------------------------

def test_created_to_is_inclusive_of_whole_day(client, db_session, test_project, auth_headers):
    # Late in the day on the created_to date — must still be included,
    # since a naive "<= created_to" (implicitly midnight) would exclude it.
    late_in_day = datetime(2026, 10, 5, 23, 45, 0)
    run = make_run(db_session, test_project, status="QUEUED")
    run.created_at = late_in_day
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?created_from=2026-10-05&created_to=2026-10-05",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert any(item["id"] == run.id for item in body["items"])


def test_created_from_excludes_earlier_runs(client, db_session, test_project, auth_headers):
    early_run = make_run(db_session, test_project, status="QUEUED")
    early_run.created_at = datetime(2026, 9, 1, 10, 0, 0)
    in_range_run = make_run(db_session, test_project, status="QUEUED")
    in_range_run.created_at = datetime(2026, 10, 5, 10, 0, 0)
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?created_from=2026-10-01", headers=auth_headers
    )
    body = resp.json()
    ids = [i["id"] for i in body["items"]]
    assert in_range_run.id in ids
    assert early_run.id not in ids


def test_created_to_excludes_later_runs(client, db_session, test_project, auth_headers):
    in_range_run = make_run(db_session, test_project, status="QUEUED")
    in_range_run.created_at = datetime(2026, 10, 1, 10, 0, 0)
    later_run = make_run(db_session, test_project, status="QUEUED")
    later_run.created_at = datetime(2026, 10, 10, 10, 0, 0)
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?created_to=2026-10-05", headers=auth_headers
    )
    body = resp.json()
    ids = [i["id"] for i in body["items"]]
    assert in_range_run.id in ids
    assert later_run.id not in ids


def test_invalid_date_format_422(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?created_from=not-a-date", headers=auth_headers
    )
    assert resp.status_code == 422


def test_created_from_after_created_to_422(client, test_project, auth_headers):
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?created_from=2026-10-10&created_to=2026-10-01",
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# K. combined filters
# ---------------------------------------------------------------------------

def test_combined_filters(client, db_session, test_project, auth_headers):
    target = make_run(db_session, test_project, status="FAILED", browser="chrome")
    target.created_at = datetime(2026, 10, 5, 12, 0, 0)
    make_run(db_session, test_project, status="FAILED", browser="firefox")
    make_run(db_session, test_project, status="COMPLETED", browser="chrome")
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs"
        "?status=FAILED&browser=chrome&created_from=2026-10-01&created_to=2026-10-10",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 1
    assert body["items"][0]["id"] == target.id


# ---------------------------------------------------------------------------
# L/M/N. filtered pagination
# ---------------------------------------------------------------------------

def test_filtered_pagination(client, db_session, test_project, auth_headers):
    for _ in range(5):
        make_run(db_session, test_project, status="FAILED")
    make_run(db_session, test_project, status="COMPLETED")

    resp1 = client.get(
        f"/api/projects/{test_project.id}/runs?status=FAILED&page=1&page_size=2",
        headers=auth_headers,
    )
    body1 = resp1.json()
    assert body1["total"] == 5
    assert body1["total_pages"] == 3
    assert len(body1["items"]) == 2

    resp2 = client.get(
        f"/api/projects/{test_project.id}/runs?status=FAILED&page=2&page_size=2",
        headers=auth_headers,
    )
    body2 = resp2.json()
    assert len(body2["items"]) == 2
    ids1 = {i["id"] for i in body1["items"]}
    ids2 = {i["id"] for i in body2["items"]}
    assert ids1.isdisjoint(ids2)


def test_filtered_page_beyond_range_returns_empty(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="FAILED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status=FAILED&page=50&page_size=20",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["items"] == []
    assert body["total"] == 1


def test_no_matching_filters_returns_empty_envelope(client, db_session, test_project, auth_headers):
    make_run(db_session, test_project, status="QUEUED")
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status=CANCELLED", headers=auth_headers
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body == {"items": [], "page": 1, "page_size": 20, "total": 0, "total_pages": 0}


# ---------------------------------------------------------------------------
# O. authorization under filters
# ---------------------------------------------------------------------------

def test_unauthenticated_returns_401_with_filters(client, test_project):
    resp = client.get(f"/api/projects/{test_project.id}/runs?status=FAILED")
    assert resp.status_code == 401


def test_wrong_owner_project_404_with_filters(client, db_session, auth_headers):
    from app.core.security import hash_password
    from app.models.project import Project
    from app.models.user import User

    other_user = User(
        full_name="Other User 6D",
        email="other-run-history-filters-user@example.com",
        mobile_number="9000000003",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_project = Project(
        name="Other User's Project 6D",
        frequency="manual",
        browser="chrome",
        owner_id=other_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    other_run = make_run(db_session, other_project, status="FAILED")

    resp = client.get(
        f"/api/projects/{other_project.id}/runs?run_id={other_run.id}", headers=auth_headers
    )
    assert resp.status_code == 404


def test_run_id_cannot_discover_another_users_run(client, db_session, test_project, auth_headers):
    from app.core.security import hash_password
    from app.models.project import Project
    from app.models.user import User

    other_user = User(
        full_name="Other User 6D-2",
        email="other-run-history-filters-user-2@example.com",
        mobile_number="9000000004",
        password_hash=hash_password("Password123!"),
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    other_project = Project(
        name="Other User's Project 6D-2",
        frequency="manual",
        browser="chrome",
        owner_id=other_user.id,
        is_active=True,
    )
    db_session.add(other_project)
    db_session.commit()
    db_session.refresh(other_project)

    other_run = make_run(db_session, other_project, status="FAILED")

    # Querying MY OWN (owned) project but filtering by the OTHER user's
    # real run_id must return empty items, not that run, and not a 404.
    resp = client.get(
        f"/api/projects/{test_project.id}/runs?run_id={other_run.id}", headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# P. result metadata correctness under filtering
# ---------------------------------------------------------------------------

def test_metadata_correct_under_filtering(client, db_session, test_project, auth_headers):
    from app.models.run_result import RunResult
    from app.models.screenshot import Screenshot
    from app.models.test_result import TestResult

    started = datetime(2026, 1, 1, 10, 0, 0)
    completed = started + timedelta(seconds=15)
    run = make_run(
        db_session, test_project, status="COMPLETED",
        started_at=started, completed_at=completed,
    )
    rr = RunResult(
        run_id=run.id, exit_code=0,
        total_tests=4, passed_count=3, failed_count=1, skipped_count=0,
    )
    db_session.add(rr)
    tr = TestResult(
        run_id=run.id, project_file_id=None, nodeid="t.py::test_a",
        file_name="t.py", method_name="test_a", status="FAILED",
    )
    db_session.add(tr)
    db_session.commit()
    db_session.refresh(tr)
    shot = Screenshot(
        test_result_id=tr.id, step_result_id=None, file_name="s.png",
        mime_type="image/png", capture_reason="ALL_MODE",
        content=b"\x89PNG\r\n", size_bytes=6,
    )
    db_session.add(shot)
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status=COMPLETED", headers=auth_headers
    )
    item = next(i for i in resp.json()["items"] if i["id"] == run.id)
    assert item["duration_seconds"] == pytest.approx(15.0)
    assert item["test_count"] == 4
    assert item["passed_count"] == 3
    assert item["failed_count"] == 1
    assert item["skipped_count"] == 0
    assert item["screenshot_count"] == 1


# ---------------------------------------------------------------------------
# Q. ordering under filtered results
# ---------------------------------------------------------------------------

def test_ordering_preserved_under_filters(client, db_session, test_project, auth_headers):
    same_ts = datetime(2026, 2, 1, 9, 0, 0)
    run_a = make_run(db_session, test_project, status="FAILED")
    run_a.created_at = same_ts
    run_b = make_run(db_session, test_project, status="FAILED")
    run_b.created_at = same_ts
    db_session.commit()

    resp = client.get(
        f"/api/projects/{test_project.id}/runs?status=FAILED", headers=auth_headers
    )
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids.index(run_b.id) < ids.index(run_a.id)


# ---------------------------------------------------------------------------
# R. query-count / N+1 check for a filtered page
# ---------------------------------------------------------------------------

def test_filtered_query_count_stays_small(client, db_session, test_project, auth_headers):
    for _ in range(15):
        make_run(db_session, test_project, status="FAILED")

    query_count = 0

    def _count(*args, **kwargs):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", _count)
    try:
        resp = client.get(
            f"/api/projects/{test_project.id}/runs?status=FAILED&page=1&page_size=20",
            headers=auth_headers,
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert resp.status_code == 200
    assert resp.json()["total"] == 15
    # Ownership check + COUNT + SELECT runs + batch RunResult + batch
    # screenshot aggregation + whatever auth/user lookups occur — a small
    # constant number, not one query per matching run.
    assert query_count < 10, f"expected a small constant query count, got {query_count}"


# ---------------------------------------------------------------------------
# Regression — Phase 6C's own test file still passes unmodified (sanity:
# exercised directly by the full suite run, this just documents the intent).
# ---------------------------------------------------------------------------

def test_phase_6c_module_still_importable():
    import tests.test_run_history  # noqa: F401
