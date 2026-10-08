

import subprocess
import threading
import time
import uuid

import httpx
import pytest
import pyodbc

from tests.conftest import make_run


LIVE_BASE_URL = "http://127.0.0.1:8000"

CONN_STR = (
    "mssql+pyodbc://@DESKTOP-2I11G5V/Convergence?driver=ODBC+Driver+18+for+SQL+Server"
    "&trusted_connection=yes&TrustServerCertificate=yes"
)
RAW_CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=DESKTOP-2I11G5V;DATABASE=Convergence;"
    "Trusted_Connection=yes;TrustServerCertificate=yes"
)


def _live_server_reachable() -> bool:
    try:
        resp = httpx.get(f"{LIVE_BASE_URL}/api/health", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


LIVE = pytest.mark.skipif(
    not _live_server_reachable(),
    reason="live server not reachable at 127.0.0.1:8000",
)


def _db_query(sql, params=()):
    conn = pyodbc.connect(RAW_CONN_STR, timeout=5)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        cols = [c[0] for c in cur.description] if cur.description else []
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        return rows
    finally:
        conn.close()


_PASS_SOURCE = '''
def test_always_passes():
    assert True
'''

_FAIL_SOURCE = '''
def test_always_fails():
    assert False, "deliberate failure for isolation testing"
'''

_SLEEP_SOURCE = '''
import time


def test_sleeps_long_enough_to_be_cancelled():
    time.sleep(20)
    assert True
'''


def _register_login_project(http: httpx.Client, label: str, source: str, filename: str = "test_case.py"):
    unique = uuid.uuid4().hex[:10]
    unique_digits = str(int(uuid.uuid4().int % 10_000_000_000)).rjust(10, "1")

    register_resp = http.post(
        "/api/auth/register",
        json={
            "full_name": f"Phase6E {label}",
            "email": f"phase6e-{label}-{unique}@example.com",
            "mobile_number": "9" + unique_digits,
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
    )
    assert register_resp.status_code == 201, register_resp.text

    login_resp = http.post(
        "/api/auth/login",
        json={
            "email": f"phase6e-{label}-{unique}@example.com",
            "password": "Password123!",
        },
    )
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = http.post(
        "/api/projects",
        json={"name": f"Phase6E {label} Project {unique}", "frequency": "manual", "browser": "chrome"},
        headers=headers,
    )
    assert project_resp.status_code == 201, project_resp.text
    project_id = project_resp.json()["id"]

    upload_resp = http.post(
        f"/api/projects/{project_id}/files",
        files={"files": (filename, source, "text/x-python")},
        headers=headers,
    )
    assert upload_resp.status_code == 201, upload_resp.text

    return headers, project_id


def _create_and_start(http, headers, project_id):
    run_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
    assert run_resp.status_code == 201, run_resp.text
    run_id = run_resp.json()["id"]

    start_resp = http.post(
        f"/api/projects/{project_id}/runs/{run_id}/start", headers=headers,
    )
    assert start_resp.status_code == 200, start_resp.text
    return run_id


def _poll_until(http, headers, project_id, run_id, predicate, timeout_s=40, interval_s=0.5):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = http.get(
            f"/api/projects/{project_id}/runs/{run_id}", headers=headers,
        ).json()
        if predicate(last):
            return last
        time.sleep(interval_s)
    return last


# ---------------------------------------------------------------------------
# TestClient-based basic checks (no live server needed) — checklist
# completeness for invalid transitions / retry.
# ---------------------------------------------------------------------------

def test_retry_completed_run_succeeds_and_source_unchanged(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="COMPLETED")
    before_status = run.status
    before_completed_at = run.completed_at

    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )
    assert response.status_code == 201
    new_run = response.json()
    assert new_run["id"] != run.id
    assert new_run["status"] == "QUEUED"

    db_session.refresh(run)
    assert run.status == before_status
    assert run.completed_at == before_completed_at


def test_retry_queued_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="QUEUED")
    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/retry",
        headers=auth_headers,
    )
    assert response.status_code == 409


def test_start_already_running_run_returns_409(client, db_session, test_project, auth_headers):
    run = make_run(db_session, test_project, status="RUNNING")
    response = client.post(
        f"/api/projects/{test_project.id}/runs/{run.id}/start",
        headers=auth_headers,
    )
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Live-server concurrency tests
# ---------------------------------------------------------------------------

@LIVE
def test_duplicate_concurrent_start_only_one_wins():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "dupstart", _PASS_SOURCE)

        run_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
        assert run_resp.status_code == 201
        run_id = run_resp.json()["id"]

        results = []

        def fire_start():
            with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as h2:
                r = h2.post(
                    f"/api/projects/{project_id}/runs/{run_id}/start", headers=headers,
                )
                results.append(r.status_code)

        threads = [threading.Thread(target=fire_start) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert sorted(results) == [200, 409], f"expected exactly one 200 and one 409, got {results}"

        final = _poll_until(
            http, headers, project_id, run_id,
            lambda r: r["status"] in ("COMPLETED", "FAILED"),
        )
        assert final["status"] == "COMPLETED", final

        rows = _db_query(
            "SELECT COUNT(*) AS c FROM run_results WHERE run_id = ?", (run_id,),
        )
        assert rows[0]["c"] == 1, "exactly one run_results row expected, no duplicate execution"


@LIVE
def test_two_independent_concurrent_runs_pass_and_fail_isolated():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers_a, project_a = _register_login_project(http, "concA", _PASS_SOURCE)
        headers_b, project_b = _register_login_project(http, "concB", _FAIL_SOURCE)

        run_a = _create_and_start(http, headers_a, project_a)
        run_b = _create_and_start(http, headers_b, project_b)

        final_a = _poll_until(http, headers_a, project_a, run_a, lambda r: r["status"] in ("COMPLETED", "FAILED"))
        final_b = _poll_until(http, headers_b, project_b, run_b, lambda r: r["status"] in ("COMPLETED", "FAILED"))

        assert final_a["status"] == "COMPLETED", final_a
        assert final_b["status"] == "FAILED", final_b

        test_results_a = _db_query("SELECT run_id FROM test_results WHERE run_id = ?", (run_a,))
        test_results_b = _db_query("SELECT run_id FROM test_results WHERE run_id = ?", (run_b,))
        assert all(r["run_id"] == run_a for r in test_results_a)
        assert all(r["run_id"] == run_b for r in test_results_b)

        cross_a = _db_query("SELECT run_id FROM test_results WHERE run_id = ?", (run_b,))
        assert all(r["run_id"] != run_a for r in cross_a)


@LIVE
def test_concurrent_cancellation_isolation():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers_a, project_a = _register_login_project(http, "cancA", _SLEEP_SOURCE, "test_slow_a.py")
        headers_b, project_b = _register_login_project(http, "cancB", _SLEEP_SOURCE, "test_slow_b.py")

        run_a = _create_and_start(http, headers_a, project_a)
        run_b = _create_and_start(http, headers_b, project_b)

        state_a = _poll_until(http, headers_a, project_a, run_a, lambda r: r["status"] == "RUNNING", timeout_s=15)
        state_b = _poll_until(http, headers_b, project_b, run_b, lambda r: r["status"] == "RUNNING", timeout_s=15)
        assert state_a["status"] == "RUNNING"
        assert state_b["status"] == "RUNNING"

        cancel_resp = http.post(f"/api/projects/{project_a}/runs/{run_a}/cancel", headers=headers_a)
        assert cancel_resp.status_code == 200, cancel_resp.text

        final_a = _poll_until(http, headers_a, project_a, run_a, lambda r: r["status"] == "CANCELLED", timeout_s=20)
        assert final_a["status"] == "CANCELLED", final_a

        # B must still be running shortly after A's cancellation landed.
        b_check = http.get(f"/api/projects/{project_b}/runs/{run_b}", headers=headers_b).json()
        assert b_check["status"] == "RUNNING", b_check

        # Duplicate cancel on A: first already consumed, this one is 409.
        dup_cancel = http.post(f"/api/projects/{project_a}/runs/{run_a}/cancel", headers=headers_a)
        assert dup_cancel.status_code == 409

        # Let B finish naturally.
        final_b = _poll_until(http, headers_b, project_b, run_b, lambda r: r["status"] in ("COMPLETED", "FAILED"), timeout_s=30)
        assert final_b["status"] == "COMPLETED", final_b


@LIVE
def test_duplicate_cancel_quick_succession():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "dupcancel", _SLEEP_SOURCE)
        run_id = _create_and_start(http, headers, project_id)

        _poll_until(http, headers, project_id, run_id, lambda r: r["status"] == "RUNNING", timeout_s=15)

        r1 = http.post(f"/api/projects/{project_id}/runs/{run_id}/cancel", headers=headers)
        r2 = http.post(f"/api/projects/{project_id}/runs/{run_id}/cancel", headers=headers)

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 409, r2.text


@LIVE
def test_retry_isolation_source_unchanged_and_new_runs_independent():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers_a, project_a = _register_login_project(http, "retryA", _PASS_SOURCE)
        headers_b, project_b = _register_login_project(http, "retryB", _FAIL_SOURCE)

        run_a = _create_and_start(http, headers_a, project_a)
        run_b = _create_and_start(http, headers_b, project_b)

        # NOTE: result_persistence_service.persist_run_results() does an
        # intermediate db.commit() (persisting status + result rows)
        # BEFORE execute_run's outer `finally` sets completed_at and
        # commits again — a narrow, pre-existing window where another
        # session can observe the terminal status with completed_at still
        # NULL. Poll on completed_at too (not just status) to land past
        # that window, since this test asserts completed_at equality.
        final_a = _poll_until(
            http, headers_a, project_a, run_a,
            lambda r: r["status"] == "COMPLETED" and r["completed_at"] is not None,
        )
        final_b = _poll_until(
            http, headers_b, project_b, run_b,
            lambda r: r["status"] == "FAILED" and r["completed_at"] is not None,
        )
        assert final_a["status"] == "COMPLETED"
        assert final_b["status"] == "FAILED"

        retry_a_resp = http.post(f"/api/projects/{project_a}/runs/{run_a}/retry", headers=headers_a)
        retry_b_resp = http.post(f"/api/projects/{project_b}/runs/{run_b}/retry", headers=headers_b)
        assert retry_a_resp.status_code == 201
        assert retry_b_resp.status_code == 201

        new_run_a = retry_a_resp.json()["id"]
        new_run_b = retry_b_resp.json()["id"]
        assert new_run_a != run_a
        assert new_run_b != run_b
        assert new_run_a != new_run_b

        # Source runs unchanged.
        reread_a = http.get(f"/api/projects/{project_a}/runs/{run_a}", headers=headers_a).json()
        reread_b = http.get(f"/api/projects/{project_b}/runs/{run_b}", headers=headers_b).json()
        assert reread_a["status"] == "COMPLETED"
        assert reread_a["completed_at"] == final_a["completed_at"]
        assert reread_b["status"] == "FAILED"
        assert reread_b["completed_at"] == final_b["completed_at"]


@LIVE
def test_retry_then_start_then_cancel_source_unchanged_no_copied_results():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "retrycancel", _FAIL_SOURCE)
        source_run = _create_and_start(http, headers, project_id)
        source_final = _poll_until(
            http, headers, project_id, source_run,
            lambda r: r["status"] == "FAILED" and r["completed_at"] is not None,
        )
        assert source_final["status"] == "FAILED"

        retry_resp = http.post(f"/api/projects/{project_id}/runs/{source_run}/retry", headers=headers)
        assert retry_resp.status_code == 201
        new_run = retry_resp.json()["id"]

        start_resp = http.post(f"/api/projects/{project_id}/runs/{new_run}/start", headers=headers)
        assert start_resp.status_code == 200

        cancel_resp = http.post(f"/api/projects/{project_id}/runs/{new_run}/cancel", headers=headers)
        assert cancel_resp.status_code in (200, 409)  # may already be terminal if pytest was very fast

        final_new = _poll_until(
            http, headers, project_id, new_run,
            lambda r: r["status"] in ("CANCELLED", "COMPLETED", "FAILED"), timeout_s=20,
        )
        assert final_new["status"] in ("CANCELLED", "COMPLETED", "FAILED")

        # Source run re-queried: completely unchanged.
        reread_source = http.get(f"/api/projects/{project_id}/runs/{source_run}", headers=headers).json()
        assert reread_source["status"] == "FAILED"
        assert reread_source["completed_at"] == source_final["completed_at"]

        # Any results that exist for the new run must be keyed to new_run,
        # never referencing source_run.
        new_run_results = _db_query("SELECT run_id FROM test_results WHERE run_id = ?", (new_run,))
        assert all(r["run_id"] == new_run for r in new_run_results)
        cross_check = _db_query("SELECT run_id FROM test_results WHERE run_id = ? AND run_id = ?", (new_run, source_run))
        assert cross_check == []


@LIVE
def test_terminal_status_never_flips_after_stabilizing():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "stableterm", _PASS_SOURCE)
        run_id = _create_and_start(http, headers, project_id)

        final = _poll_until(
            http, headers, project_id, run_id,
            lambda r: r["status"] in ("COMPLETED", "FAILED") and r["completed_at"] is not None,
        )
        assert final["status"] == "COMPLETED"

        time.sleep(2)
        recheck = http.get(f"/api/projects/{project_id}/runs/{run_id}", headers=headers).json()
        assert recheck["status"] == "COMPLETED"
        assert recheck["completed_at"] == final["completed_at"]


@LIVE
def test_history_filters_reflect_active_running_and_queued_runs():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "histfilter", _SLEEP_SOURCE)

        queued_resp = http.post(f"/api/projects/{project_id}/runs", headers=headers)
        assert queued_resp.status_code == 201
        queued_run_id = queued_resp.json()["id"]

        running_run_id = _create_and_start(http, headers, project_id)
        _poll_until(http, headers, project_id, running_run_id, lambda r: r["status"] == "RUNNING", timeout_s=15)

        running_list = http.get(
            f"/api/projects/{project_id}/runs", params={"status": "RUNNING"}, headers=headers,
        ).json()
        assert [item["id"] for item in running_list["items"]] == [running_run_id]

        queued_list = http.get(
            f"/api/projects/{project_id}/runs", params={"status": "QUEUED"}, headers=headers,
        ).json()
        assert [item["id"] for item in queued_list["items"]] == [queued_run_id]

        # Clean up: cancel both so no leaked subprocess survives this test.
        http.post(f"/api/projects/{project_id}/runs/{running_run_id}/cancel", headers=headers)
        http.post(f"/api/projects/{project_id}/runs/{queued_run_id}/cancel", headers=headers)

        final_running = _poll_until(
            http, headers, project_id, running_run_id, lambda r: r["status"] == "CANCELLED", timeout_s=20,
        )
        assert final_running["status"] == "CANCELLED"

        cancelled_list = http.get(
            f"/api/projects/{project_id}/runs", params={"status": "CANCELLED"}, headers=headers,
        ).json()
        ids = {item["id"] for item in cancelled_list["items"]}
        assert {running_run_id, queued_run_id}.issubset(ids)


@LIVE
def test_authorization_isolation_while_other_user_run_is_active():
    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers_owner, project_owner = _register_login_project(http, "authowner", _SLEEP_SOURCE)
        headers_other, project_other = _register_login_project(http, "authother", _PASS_SOURCE)

        run_id = _create_and_start(http, headers_owner, project_owner)
        _poll_until(http, headers_owner, project_owner, run_id, lambda r: r["status"] == "RUNNING", timeout_s=15)

        # Other user cannot view, start, cancel, or retry owner's active run.
        get_resp = http.get(f"/api/projects/{project_owner}/runs/{run_id}", headers=headers_other)
        assert get_resp.status_code == 404

        cancel_resp = http.post(f"/api/projects/{project_owner}/runs/{run_id}/cancel", headers=headers_other)
        assert cancel_resp.status_code == 404

        retry_resp = http.post(f"/api/projects/{project_owner}/runs/{run_id}/retry", headers=headers_other)
        assert retry_resp.status_code == 404

        # Other user cannot discover owner's run via run_id/search filters
        # on their OWN project (should be empty results, not an error).
        search_resp = http.get(
            f"/api/projects/{project_other}/runs", params={"run_id": run_id}, headers=headers_other,
        )
        assert search_resp.status_code == 200
        assert search_resp.json()["items"] == []

        # Clean up: cancel owner's run so no subprocess is left running.
        http.post(f"/api/projects/{project_owner}/runs/{run_id}/cancel", headers=headers_owner)
        _poll_until(http, headers_owner, project_owner, run_id, lambda r: r["status"] == "CANCELLED", timeout_s=20)


@LIVE
def test_process_and_tempdir_cleanup_after_terminal_states():
    import glob
    import tempfile

    with httpx.Client(base_url=LIVE_BASE_URL, timeout=30) as http:
        headers, project_id = _register_login_project(http, "cleanup", _PASS_SOURCE)
        run_id = _create_and_start(http, headers, project_id)
        final = _poll_until(http, headers, project_id, run_id, lambda r: r["status"] in ("COMPLETED", "FAILED"))
        assert final["status"] == "COMPLETED"

        # TemporaryDirectory should have been cleaned up — no leftover dir
        # with this run's id in its name under the OS temp path. There is a
        # small, genuine timing window: the Run row's status commit happens
        # just BEFORE the `with tempfile.TemporaryDirectory()` block in
        # _run_with_temp_directory() actually exits and deletes the
        # directory, so polling can observe COMPLETED a few milliseconds
        # before cleanup finishes. Bounded retry rather than an immediate
        # hard assert to account for that real (and harmless) ordering.
        leftover = glob.glob(f"{tempfile.gettempdir()}/run_{run_id}_*")
        for _ in range(20):
            if not leftover:
                break
            time.sleep(0.25)
            leftover = glob.glob(f"{tempfile.gettempdir()}/run_{run_id}_*")
        assert leftover == [], f"leftover temp dirs found: {leftover}"
