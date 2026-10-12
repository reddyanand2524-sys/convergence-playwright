# JSON execution report (schema 1.0)

Every Run produces one validated JSON report, stored in `run_results.report_json`.
Source of truth: `backend/app/services/execution_report.py` (Pydantic models, builder, validation).

Schema 1.0 was extended **additively**: the original fields and their meanings are unchanged, and every added
field is optional. Reports stored before the additions (*legacy 1.0 reports*) still validate and render.

## Retrieving and downloading a report

* API: `GET /api/projects/{project_id}/runs/{run_id}/report/json` (bearer token; the project must belong to the
  caller). Add `?download=true` for a `Content-Disposition: attachment` response
  (`Convergence_Run_<id>.json`).
  * `409` – run is not `COMPLETED`/`FAILED`; `404` – unknown project/run, or no stored report (e.g. a run that
    predates reporting); `500` – the stored report fails validation (never returned as if valid).
  * The report is re-validated on every read.
* Screenshots: `GET /api/projects/{project_id}/runs/{run_id}/artifacts/screenshots/{file_name}`.
* UI: on a run's **Results** page, the **Execution report** panel (expand it; "View JSON" shows the raw JSON) and
  **Reports → JSON Report** (downloads the file).

## Pipeline

1. `selenium_compat_runner.py` (the generated `conftest.py`) records every Selenium action to `steps.json`
   (status `PASS|FAIL|SKIP`, timestamps, URL, title, locator, error type, screenshot) and, to `steps_meta.json`, the
   steps each test *defines* (Selenium IDE `# N | action | target | value` comments) plus whether
   continue-after-failure was enabled.
2. `result_persistence_service.persist_run_results` builds the report from those files, the JUnit result and the
   project name, validates it, and stores it with the other results. A failure to build it is logged and the report is
   then simply absent (404); other results are still saved.
3. The API serves it; the frontend (`ExecutionReportPanel`) renders the response.

## Continue after errors

Before a test body runs, its top-level statements are wrapped in an in-memory copy of the function (the uploaded
file is never modified). A failed action **or assertion** is recorded as `FAILED` (with a screenshot when possible)
and execution continues. The first swallowed exception is re-raised at the end, so pytest/JUnit still report the
test as failed. Disable with `SELENIUM_COMPAT_CONTINUE_ON_ERROR=0`.

A statement that uses a variable assigned by a failed statement (`el = find(...)` failed, then `el.click()`) is
`SKIPPED` with a reason; if the browser becomes unusable, later driver statements are `SKIPPED`; if setup fails
(browser did not start) every defined step is never attempted. Independent steps always run.

## Statuses

`PASSED | FAILED | SKIPPED` only (there is no `NOT_RUN` status).

* Step: `FAILED` if any of its events failed; `PASSED` if it ran; otherwise `SKIPPED`.
  * **Never attempted** (test stopped, setup failed, step not reached): `SKIPPED`, `attempted=false`, `skipReason`
    beginning `Not executed…`.
  * **Dependency / browser-unusable skip:** `SKIPPED`, `attempted=false`, a different `skipReason`
    (e.g. `Depends on 'label', which is unavailable because step 7 failed`).
* Test: `FAILED` if pytest says failed/error or any step failed; `SKIPPED` if pytest skipped; else `PASSED`.
* Run: `FAILED` if any test failed, no tests were reported, or a run-level error exists; `SKIPPED` if every test
  was skipped; else `PASSED`.

## Fields

Original 1.0 fields (unchanged):

```
reportVersion, executionId ("RUN-<id>"), testcaseId/testcaseName (null when several tests), browser,
status, startTime, completionTime, executionTimeMs, expectedOutcome (always null: tests declare none), actualOutcome,
summary, tests[], steps[], pages[], errors[], attempts[], artifacts
summary { totalSteps, attemptedSteps, executedSteps(=attemptedSteps), passedSteps, failedSteps, skippedSteps,
          totalErrors, totalPages }
steps[] { testId, stepNumber, stepName, command, locatorType, locatorValue, value, status, attempted, skipReason,
          startTime, completionTime, executionTimeMs, error{type,message,category}, screenshot }
pages[] { pageNumber, pageId, url, pageTitle, screenshot, events[...], errors[] }
tests[] { testId, testName, status, startTime, completionTime, executionTimeMs, actualOutcome, summary }
attempts[] { attemptNumber, status, startTime, completionTime, executionTimeMs }   (one: Retry creates a new Run)
artifacts { jsonReport, screenshotsDirectory, screenshots[], executionLog:null, video:null, trace:null }
```

Added optional fields (absent from legacy reports; `null` when the information is not available):

| Location | Field | Meaning |
|---|---|---|
| top level | `reportType` | `"automation_execution"` |
| top level | `metadata` | `projectId`, `projectName`, `runId`, `testFile` (comma-separated test file names), `executionEngine` (`"selenium"`), `startedAt`, `finishedAt`, `durationMs`. Always `null`: `runName` (runs have no name), `environment`, `triggeredBy` (not recorded) |
| top level | `environment` | `browser` (the run's browser). Always `null`: `browserVersion`, `operatingSystem`, `baseUrl` (not recorded) |
| top level | `executionLog` | one entry per real recorded step event, in execution order: `timestamp`, `level` (`ERROR` failed, `WARNING` skipped, `INFO` passed), `message`, `testId`, `stepNumber`. It is derived from events, not stdout/stderr |
| `summary` (and each `tests[].summary`) | `failureCount` | number of `FAILED` steps (equals `failedSteps`; test-level/run-level errors are in `totalErrors`) |
| | `notRunSteps` | `SKIPPED` steps with `attempted=false` and a `Not executed…` reason. **Dependency skips are not counted**, so `notRunSteps <= skippedSteps` |
| | `continuationEnabled` | whether the runner continued after failures (`true`/`false`); `null` if the runner did not report it |
| | `warningCount` | always `null` – warnings are not tracked |
| `steps[]` | `stepId` | `"<testId>#step-<n>"` |
| | `name`, `action` | same values as `stepName`, `command` |
| | `locator` | `{strategy, value}`, same data as `locatorType`/`locatorValue`; `null` when there is no locator |
| | `expected` | the defined value of an `assert*`/`verify*` step; `null` for all other steps |
| | `attempt` | `1` when the step was attempted, else `null` |

`actual` is **not populated** (there is no `actual` field in the report): the actual value of an assertion cannot be
captured reliably, so it is not invented; the failure text is in `error.message`.

## Validation

Statuses are an enum, timestamps must parse as ISO-8601, durations are non-negative ints, unknown fields are
rejected, and `summary` must equal the counts recomputed from `steps`/`pages`/`errors`. `notRunSteps` and
`failureCount` are compared only when present (legacy reports lack them); `continuationEnabled` and `warningCount`
are not derivable from the records and are not compared.

Error categories: `LOCATOR_ERROR, INVALID_SELECTOR, TIMEOUT, CLICK_INTERCEPTED, ELEMENT_NOT_INTERACTABLE,
STALE_ELEMENT, FRAME_ERROR, BROWSER_CRASH, BROWSER_INIT_ERROR, ASSERTION_FAILURE, RUN_ERROR, UNEXPECTED_ERROR`.

## Artifacts and security

Screenshots stay in the existing `screenshots` table. Report paths are `screenshots/<file>.png`; the report only
references files that exist, with plain `[A-Za-z0-9._-]+.png` names, and only when capture succeeded. The artifact
endpoint looks the name up among **that run's own** screenshots (no filesystem access, no cross-run access).
Values typed into password-like fields are replaced by `***REDACTED***` in `steps.json` and the report; error
messages are scrubbed of `password=…`, `token: …`, `Authorization: Bearer …`. Project ownership is checked on every
endpoint.

## Database column (SQL Server) and the DBA step

The report is stored in the optional column `dbo.run_results.report_json NVARCHAR(MAX) NULL`. It is deliberately **not**
mapped on the ORM `RunResult` model; it is read/written with plain SQL in `result_persistence_service.py`, so a
database without the column keeps working exactly as before.

`report_column_available()` behaviour:

* The column is detected from the schema, never inferred from SQL errors. A failure to inspect the schema is logged,
  reported as "unavailable" and not cached.
* If the column is missing, **one** attempt per process is made to add it, and only if the login has `ALTER`
  permission on the table (`HAS_PERMS_BY_NAME`). Otherwise nothing is attempted and a warning containing the DDL is
  logged once.
* While the column is missing the schema is re-inspected every 5 minutes (never re-altered), so a DBA-added column is
  picked up without a restart.
* While unavailable: runs, results, HTML/Excel/PDF reports and screenshots work normally, reports are not stored, and
  `GET .../report/json` answers `404` with "No JSON execution report is available for this run (JSON report storage is
  not enabled on this database)", which the UI shows in the Execution report panel.

The application login (`convergence_docker` in the verified environment) has only `db_datareader`/`db_datawriter`
(`HAS_PERMS_BY_NAME('dbo.run_results','OBJECT','ALTER')` = 0), so it cannot add the column. A DBA (an authorized
account) does this once in **SSMS**: connect to the correct instance, select the `Convergence` database, open a new
query window, confirm `dbo.run_results` exists, and run this idempotent statement (use the verified schema name if it
is not `dbo`; never create a new table):

```sql
USE Convergence;
GO

IF COL_LENGTH('dbo.run_results', 'report_json') IS NULL
BEGIN
    ALTER TABLE dbo.run_results
    ADD report_json NVARCHAR(MAX) NULL;
END;
GO
```

Verify (expected: exactly one row, `COLUMN_NAME=report_json`, `DATA_TYPE=nvarchar`, `IS_NULLABLE=YES`):

```sql
USE Convergence;
GO

SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'run_results' AND COLUMN_NAME = 'report_json';
```

No application restart is needed: while the column is missing the app re-inspects the schema every 5 minutes (it never
re-attempts DDL after a permission denial), so reports start being stored within about 5 minutes (a restart picks it up
immediately). Reports for runs executed before the column existed are never backfilled; they keep answering `404`.

Read-only diagnostics: `SELECT DB_NAME(), USER_NAME();`, `INFORMATION_SCHEMA.TABLES`/`COLUMNS` lookups for `run_results`
/ `report_json`, and `SELECT HAS_PERMS_BY_NAME('dbo.run_results','OBJECT','ALTER');` (1 = allowed, 0 = denied).

**Verification status (2026-10-12):** the column `dbo.run_results.report_json NVARCHAR(MAX) NULL` was added by a DBA and is
detected by the application without any DDL attempt. Report persistence and retrieval are **verified on the configured SQL
Server** (`Convergence`): a real run was executed through a running backend, its report was stored in
`dbo.run_results.report_json`, `GET .../report/json` returned exactly the stored JSON, the same report was returned after a
backend restart, screenshots resolved as PNGs, another user got `404`, no token `401`, an unknown run `404`, and no
SQL/credential text appeared in responses. The temporary test user/project/run created for that check were removed
afterwards by their exact IDs.

## Tests

Run from `backend/`: `python -m pytest tests -q -p no:cacheprovider -W ignore -rsf`

* `test_execution_report.py` – report model, validation, legacy compatibility, redaction (no database).
* `test_continue_after_failure.py` – real pytest subprocess with an in-memory WebDriver (no database/browser).
* `test_execution_report_api.py` – persistence + endpoints; needs the column, otherwise skipped.
* `test_run_lifecycle_concurrency.py` – the live-server tests are opt-in because they create users/projects/runs through a
  running backend: set `LIVE_BASE_URL=http://127.0.0.1:8000` and `DATABASE_URL` to the database *that server uses*
  (prefer a scratch database). Without `LIVE_BASE_URL` they are skipped.
* The test database is whatever `DATABASE_URL` points to (default: the SQL Server in `database.py`). SQLite is supported as a
  test backend (`DATABASE_URL=sqlite:///some.db`); `tests/conftest.py` enables foreign keys and real transactions for it.
  Never use SQLite results as evidence about SQL Server.

### Last recorded results (commands run from `backend/`)

| Backend | Command | Result |
|---|---|---|
| none (pure) | `pytest tests/test_execution_report.py tests/test_continue_after_failure.py tests/test_selenium_compat_runner.py tests/test_pytest_diagnostics.py --noconftest` | 76 passed |
| SQL Server | `pytest tests -q -p no:cacheprovider -W ignore -rsf` | 275 passed, 0 failed, 16 skipped (10 opt-in live concurrency, 6 older live-server tests that skip when nothing listens on :8000) |
| SQL Server | focused: `test_execution_report_api.py test_execution_report.py test_continue_after_failure.py test_report_generation.py test_result_persistence_service.py test_run_results_api.py` | 131 passed (all 6 report API tests executed) |
| SQLite (scratch file) | same full command | 275 passed, 0 failed, 16 skipped (same 16) |
| SQLite + scratch live server on :8000 | `pytest tests/test_run_cancellation.py tests/test_run_history.py tests/test_run_retry.py tests/test_schedules.py` | 77 passed |
| SQLite + scratch live server | `LIVE_BASE_URL=... pytest tests/test_run_lifecycle_concurrency.py` | 13 passed |

The live-server tests were **not** run against SQL Server (they create users/projects/runs in shared data). Do not run
overlapping pytest suites against the shared database: they hold row locks and block each other.

## Running in Docker

`docker compose build backend frontend && docker compose up -d` (from the repository root). Services: `backend`
(`http://localhost:8000`, docs at `/docs`, health at `/api/health`) and `frontend` (`http://localhost:5173`, Nginx proxying
`/api/` to `backend:8000`; the browser only talks to `localhost:5173/api`). The backend reads `backend/.env`
(`DATABASE_URL` must use an address of the SQL Server that is reachable **from inside the container**, e.g. its LAN IP or
DNS name, port 1433, with TCP/IP and the firewall open; `host.docker.internal` only works if SQL Server runs on the Docker
host itself). The backend image contains Chrome, Edge, Firefox and matching drivers; Chrome runs headless/Xvfb through
the wrapper script. The backend health check calls `/api/health` (runs `SELECT 1` on SQL Server; returns 503 when the
database is unreachable, without echoing driver details) and the frontend starts only once it is healthy.

Useful commands: `docker compose ps`, `docker compose logs --tail=100 backend`, `docker compose up -d --force-recreate
backend`, `docker compose exec backend python -c "from app.services.result_persistence_service import
report_column_available as r; print(r())"`. Do not use `down -v`/volume or system prunes.

**Warning when running the test suite:** if the Docker backend is listening on `localhost:8000`, the older live-server tests
(`test_run_cancellation.py`, `test_run_history.py`, `test_run_retry.py`, `test_schedules.py`) detect it and run against it,
creating real test users/projects/runs (`phase6*`/`phase8*` emails) in whatever database that backend uses. Stop the backend
(or run the suite while nothing listens on :8000) unless that is intended. `tests/` is excluded from the backend image
(`.dockerignore`), so the test suite runs on the host, not in the container.

Frontend caching: `frontend/nginx.conf` serves `index.html` with `Cache-Control: no-cache` and the fingerprinted
`/assets/*` files as immutable, so a rebuilt UI (e.g. the Reports → JSON Report entry) shows up on the next page load; before
this, a browser could keep an old cached `index.html` pointing at the previous JS bundle. After `docker compose build
frontend && docker compose up -d frontend`, hard-refresh once (Ctrl+F5) if the old UI was cached before this change.
