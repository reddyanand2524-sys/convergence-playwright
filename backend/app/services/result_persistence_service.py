"""

Reads everything produced by a Run's pytest subprocess (JUnit XML, the
Selenium compatibility layer's steps.json, any screenshots/ directory,
captured stdout/stderr, and the process exit code) and persists it into the
5 Phase 5 tables (RunResult, TestResult, StepResult, Screenshot,
FailureDiagnostic) in a single transaction, BEFORE the caller's
TemporaryDirectory block exits and deletes all of that source data.

Must be called with the run's temp directory still alive. Raises on any
genuine DB/persistence failure (after rolling back) so the caller can force
the Run to FAILED rather than silently losing results. Missing/unreadable
JUnit XML or steps.json is NOT an error — it is treated as "zero rows" and
logged at INFO/WARNING level.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.services.failure_sanitizer import (
    clean_exception_message,
    clean_exception_type,
    collect_known_secrets,
    collect_typed_values,
    has_sensitive_context,
    mask_known_secrets,
    redact_secrets,
    redact_source_line,
    redact_url,
    sanitize_identifier,
    sanitize_process_output,
    sanitize_step,
    sanitize_step_target,
    sanitize_step_value,
    sanitize_traceback,
)
from app.services.execution_report import build_execution_report, report_to_json
from app.services.pytest_diagnostics import parse_junit_report
from app.services.run_issue_service import make_run_note

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 200_000
_STATUS_MAP_DEFAULT = "PASSED"


_REPORT_COLUMN = {"available": None, "checked_at": 0.0, "alter_attempted": False}
_RECHECK_SECONDS = 300  # re-inspect (never re-ALTER) this often while the column is missing

_REPORT_COLUMN_DDL = "ALTER TABLE dbo.run_results ADD report_json NVARCHAR(MAX) NULL"  # SQL Server
_REPORT_COLUMN_DDL_SQLITE = "ALTER TABLE run_results ADD report_json TEXT NULL"  # tests only


def report_column_available() -> bool:
    """True only if run_results.report_json exists.

    * The column is looked up in the schema (never inferred from SQL errors).
    * If it is missing, ONE attempt to add it is made per process, and only when the login
      has ALTER permission on the table. Without that permission nothing is attempted and a
      warning with the DDL for a DBA is logged once; every other feature keeps working
      (reports are simply not stored, and report/json answers 404).
    * While the column is missing the schema is re-inspected every few minutes so that a
      DBA-added column is picked up without a restart.
    * A failure to inspect the schema is logged and reported as "unavailable" but is not
      cached and never triggers DDL."""

    import time

    from sqlalchemy import inspect, text

    from app.database import engine

    state = _REPORT_COLUMN
    if state["available"] is True:
        return True
    if state["available"] is False and time.monotonic() - state["checked_at"] < _RECHECK_SECONDS:
        return False

    def has_column() -> bool:
        return "report_json" in {c["name"] for c in inspect(engine).get_columns("run_results")}

    try:
        available = has_column()
    except Exception:
        logger.exception("Could not inspect run_results; JSON execution reports are unavailable for now")
        return False

    if not available and not state["alter_attempted"]:
        state["alter_attempted"] = True
        try:
            with engine.begin() as connection:
                if engine.dialect.name == "sqlite":
                    allowed, ddl = True, _REPORT_COLUMN_DDL_SQLITE
                else:
                    allowed = connection.execute(
                        text("SELECT HAS_PERMS_BY_NAME('dbo.run_results', 'OBJECT', 'ALTER')")
                    ).scalar() == 1
                    ddl = _REPORT_COLUMN_DDL
                if allowed:
                    connection.execute(text(ddl))
            available = has_column()
        except Exception:
            logger.exception("Adding run_results.report_json failed")
        if not available:
            logger.warning(
                "run_results.report_json is missing and this database login cannot add it; JSON execution "
                "reports are disabled until a database administrator runs: %s", _REPORT_COLUMN_DDL,
            )

    state["available"] = available
    state["checked_at"] = time.monotonic()
    return available


def persist_run_results(
    db: Session,
    run: Run,
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    junit_report_path: Path,
    steps_json_path: Path,
    screenshots_dir: Path,
    steps_meta_path: Path | None = None,
    events_jsonl_path: Path | None = None,
    interrupted: str | None = None,
) -> None:
    """`interrupted` (e.g. "run timed out after 120s") means pytest was stopped before it could
    write its JUnit report: the tests are then rebuilt from the files the runner writes as it
    goes (steps_meta.json, events.jsonl, steps.json) and the stored run says its results are partial."""
    started = time.monotonic()
    try:
        project_files = db.scalars(
            select(ProjectFile).where(ProjectFile.project_id == run.project_id)
        ).all()
        files_by_name = {pf.file_name: pf for pf in project_files}
        # Literals the scripts pass to secret-named fields: masked before storing.
        known_secrets = collect_known_secrets(pf.file_content for pf in project_files)

        project_row = db.get(Project, run.project_id)
        project_name = project_row.name if project_row is not None else None

        # ------------------------------------------------------------
        # 1. JUnit XML -> TestResult rows
        # ------------------------------------------------------------
        test_results_by_nodeid: dict[str, TestResult] = {}
        test_results_by_fcm: dict[tuple[str, str | None, str], TestResult] = {}

        if interrupted and not junit_report_path.exists():
            testcases = _testcases_from_run_files(steps_meta_path, steps_json_path, events_jsonl_path)
            logger.info(
                "Run %s: stage=persist no JUnit report (%s); rebuilt %d test(s) from the runner's files",
                run.id, interrupted, len(testcases),
            )
        else:
            testcases = _parse_junit_testcases(junit_report_path)

        if not testcases:
            logger.info(
                "Run %s: no JUnit testcases found (missing/empty report) — "
                "persisting zero test_results", run.id,
            )

        for tc in testcases:
            project_file_id = None
            matched = files_by_name.get(tc["file_name"])
            if matched is not None:
                project_file_id = matched.id

            test_result = TestResult(
                run_id=run.id,
                project_file_id=project_file_id,
                nodeid=tc["nodeid"],
                file_name=tc["file_name"],
                class_name=tc["class_name"],
                method_name=tc["method_name"],
                status=tc["status"],
                duration_seconds=tc["duration"],
                started_at=None,
                completed_at=None,
            )
            db.add(test_result)
            db.flush()  # need test_result.id for children below

            test_results_by_nodeid[tc["nodeid"]] = test_result
            test_results_by_fcm[(tc["file_name"], tc["class_name"], tc["method_name"])] = test_result

        # ------------------------------------------------------------
        # 2. steps.json -> StepResult rows, grouped by nodeid
        # ------------------------------------------------------------
        step_results_by_screenshot_name: dict[str, StepResult] = {}
        test_failure_screenshot_names: dict[str, int] = {}  # filename -> test_result_id

        events_by_nodeid = _read_steps_json(steps_json_path, run)

        # Everything this run typed (including values that came from fixtures or
        # data modules) is treated as secret: masked in step values, failure text,
        # tracebacks and process output before anything is stored.
        typed_values = collect_typed_values(
            (event.get("action"), recorded)
            for events in events_by_nodeid.values()
            for event in events
            for recorded in (event.get("value"), event.get("runtime_value"))
        )
        private_values = collect_typed_values(
            ("type", value) for value in read_private_typed_values(steps_json_path.parent)
        )
        known_secrets = tuple(dict.fromkeys((*known_secrets, *typed_values, *private_values)))

        for nodeid, events in events_by_nodeid.items():
            test_result = test_results_by_nodeid.get(nodeid)

            if test_result is None:
                logger.warning(
                    "Run %s: steps.json has %d event(s) for nodeid %r with no "
                    "matching JUnit TestResult — skipping (never fabricating a "
                    "TestResult)", run.id, len(events), nodeid,
                )
                continue

            for sequence_index, event in enumerate(events):
                if event.get("action") in ("TEST_FAILURE", "TEST_SETUP_FAILURE"):
                    # This is the test_failure-hook marker event, not a real
                    # Selenium IDE step — it has no step number/target and
                    # exists only to carry a test-level failure screenshot.
                    screenshot_name = event.get("screenshot") or ""
                    if screenshot_name:
                        test_failure_screenshot_names[screenshot_name] = test_result.id
                    continue

                occurred_at = _epoch_to_datetime(event.get("timestamp"))

                # Typed values are not stored (marker only) unless explicitly enabled;
                # secret-named fields and known secrets are always masked.
                step_target, step_value, step_error = sanitize_step(
                    event.get("target"), event.get("value"), event.get("error"),
                    known_secrets, action=event.get("action"),
                )

                step_result = StepResult(
                    test_result_id=test_result.id,
                    step_number=event.get("step"),
                    action=str(event.get("action") or "")[:100],
                    target=_truncate(step_target, 1000),
                    value=_truncate(step_value, 1000),
                    status=str(event.get("status") or "PASS")[:10],
                    duration_seconds=event.get("duration"),
                    error=step_error or None,
                    sequence_index=sequence_index,
                    occurred_at=occurred_at,
                )
                db.add(step_result)
                db.flush()

                screenshot_name = event.get("screenshot") or ""
                if screenshot_name:
                    step_results_by_screenshot_name[screenshot_name] = step_result

        # ------------------------------------------------------------
        # 3. screenshots/ directory -> Screenshot rows
        # ------------------------------------------------------------
        screenshot_count = 0
        if screenshots_dir.exists():
            for png_path in sorted(screenshots_dir.glob("*.png")):
                try:
                    content = png_path.read_bytes()
                except OSError as exc:
                    logger.warning(
                        "Run %s: could not read screenshot file %s (%s) — "
                        "skipping this file only", run.id, png_path.name, exc,
                    )
                    continue

                file_name = png_path.name
                step_result = step_results_by_screenshot_name.get(file_name)

                if step_result is not None:
                    screenshot = Screenshot(
                        test_result_id=step_result.test_result_id,
                        step_result_id=step_result.id,
                        file_name=file_name,
                        mime_type="image/png",
                        capture_reason="STEP_FAILURE" if step_result.status == "FAIL" else "ALL_MODE",
                        content=content,
                        size_bytes=len(content),
                    )
                    db.add(screenshot)
                    screenshot_count += 1
                    continue

                test_result_id = test_failure_screenshot_names.get(file_name)
                if test_result_id is not None:
                    screenshot = Screenshot(
                        test_result_id=test_result_id,
                        step_result_id=None,
                        file_name=file_name,
                        mime_type="image/png",
                        capture_reason="TEST_FAILURE",
                        content=content,
                        size_bytes=len(content),
                    )
                    db.add(screenshot)
                    screenshot_count += 1
                    continue

                # Unmatched screenshot (e.g. SCREENSHOT_MODE=all with no step
                # correlation available). Attach it to a TestResult only if
                # there is exactly one test in this run — otherwise there is
                # no safe way to know which test it belongs to, so skip it
                # rather than guess.
                if len(test_results_by_nodeid) == 1:
                    only_test = next(iter(test_results_by_nodeid.values()))
                    screenshot = Screenshot(
                        test_result_id=only_test.id,
                        step_result_id=None,
                        file_name=file_name,
                        mime_type="image/png",
                        capture_reason="ALL_MODE",
                        content=content,
                        size_bytes=len(content),
                    )
                    db.add(screenshot)
                    screenshot_count += 1
                else:
                    logger.warning(
                        "Run %s: screenshot %s could not be matched to any "
                        "step or test — skipping", run.id, file_name,
                    )

        # ------------------------------------------------------------
        # 4. parse_junit_report() -> FailureDiagnostic rows
        # ------------------------------------------------------------
        diagnostics = parse_junit_report(junit_report_path)

        for diag in diagnostics:
            key = (diag.test_file, diag.test_class, diag.test_method)
            test_result = test_results_by_fcm.get(key)

            if test_result is None:
                logger.warning(
                    "Run %s: failure diagnostic for %s::%s::%s has no "
                    "matching TestResult — skipping",
                    run.id, diag.test_file, diag.test_class, diag.test_method,
                )
                continue

            # New rows are stored already sanitized (secrets masked, paths reduced
            # to basenames, driver stack removed), so typed values do not sit in the
            # database or in the SQL log. Historical rows are sanitized on read.
            failure_context = has_sensitive_context(diag.source_line, diag.traceback)
            failure = FailureDiagnostic(
                test_result_id=test_result.id,
                exception_type=_truncate(clean_exception_type(diag.exception_type, known_secrets), 255),
                exception_message=clean_exception_message(
                    diag.exception_message, limit=None,
                    sensitive_context=failure_context, secrets=known_secrets,
                ),
                source_file=_truncate(diag.test_file, 255),
                source_line_number=diag.source_line_number,
                source_line=_truncate(
                    mask_known_secrets(redact_source_line(diag.source_line), known_secrets), 1000
                ),
                raw_traceback=sanitize_traceback(
                    diag.traceback, sensitive_context=failure_context, secrets=known_secrets,
                ),
            )
            db.add(failure)

        # A test that was running when the run was stopped has no exception to report; say what
        # is known instead of leaving it unexplained.
        for tc in testcases:
            if tc.get("interrupted") and tc["nodeid"] in test_results_by_nodeid:
                db.add(FailureDiagnostic(
                    test_result_id=test_results_by_nodeid[tc["nodeid"]].id,
                    exception_type="RunInterrupted",
                    exception_message=f"The test did not finish: the {interrupted}.",
                    source_file=None, source_line_number=None, source_line=None, raw_traceback=None,
                ))

        # ------------------------------------------------------------
        # 5. RunResult summary row
        # ------------------------------------------------------------
        all_test_results = list(test_results_by_nodeid.values())
        passed_count = sum(1 for t in all_test_results if t.status == "PASSED")
        failed_count = sum(1 for t in all_test_results if t.status in ("FAILED", "ERROR"))
        skipped_count = sum(1 for t in all_test_results if t.status == "SKIPPED")

        run_result = RunResult(
            run_id=run.id,
            exit_code=exit_code,
            total_tests=len(all_test_results),
            passed_count=passed_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            # Raw process output holds tracebacks and assertion text; store it
            # sanitized (it is not returned by any endpoint or report today).
            stdout=_truncate(sanitize_process_output(stdout, known_secrets), _MAX_TEXT_CHARS),
            # (the run note is ours and is added after sanitizing: see run_issue_service)
            stderr=(make_run_note("timeout", f"{interrupted}; results are partial") if interrupted else "")
            + (_truncate(sanitize_process_output(stderr, known_secrets), _MAX_TEXT_CHARS) or ""),
        )
        db.add(run_result)

        if report_column_available():
            db.flush()
            store_report_json(
                db, run.id,
                _build_report_json(
                    run, project_name, testcases, steps_json_path, steps_meta_path, screenshots_dir,
                    exit_code, stderr, known_secrets, interrupted,
                ),
            )

        # ------------------------------------------------------------
        # 6. Commit
        # ------------------------------------------------------------
        db.commit()

        logger.info(
            "Run %s: persisted %d test result(s), %d screenshot(s), %d "
            "failure diagnostic(s)",
            run.id, len(all_test_results), screenshot_count, len(diagnostics),
        )
        logger.info(
            "Run %s: stage=persist ok tests=%d steps=%d screenshots=%d interrupted=%s elapsed=%.2fs",
            run.id, len(all_test_results), sum(len(v) for v in events_by_nodeid.values()), screenshot_count,
            bool(interrupted), time.monotonic() - started,
        )

    except Exception as exc:
        logger.exception(
            "Run %s: result persistence failed — rolling back transaction",
            run.id,
        )
        logger.error(
            "Run %s: stage=persist failed error=%s elapsed=%.2fs",
            run.id, type(exc).__name__, time.monotonic() - started,
        )
        db.rollback()
        raise


def record_save_failure(db: Session, run: Run, *, exit_code: int | None, error_type: str) -> None:
    """After persist_run_results failed (and rolled back): store a results row that says so, so
    "the test ran but saving failed" is not mistaken for "no results" or "timed out".
    Never raises; only the exception's class name is stored."""
    try:
        if db.scalar(select(RunResult.id).where(RunResult.run_id == run.id)) is not None:
            return
        db.add(RunResult(
            run_id=run.id, exit_code=exit_code, total_tests=0, passed_count=0, failed_count=0, skipped_count=0,
            stdout="", stderr=make_run_note("save-failed", f"saving the results failed ({error_type})"),
        ))
        db.commit()
    except Exception:
        logger.exception("Run %s: could not record the save failure", run.id)
        try:
            db.rollback()
        except Exception:
            pass


def _testcases_from_run_files(steps_meta_path, steps_json_path, events_jsonl_path) -> list[dict]:
    """Test cases for a run that was stopped before pytest wrote its JUnit report.

    Built only from what the runner recorded as it went, never guessed:
      * steps_meta.json lists every collected test (written at collection time);
      * events.jsonl says which tests started and which finished, with status and duration;
      * steps.json names the tests that recorded steps.
    A finished test keeps its real status; a test that started but did not finish is ERROR (its
    outcome is unknown); a collected test that never started is SKIPPED (not run)."""
    import json

    meta = _read_json(steps_meta_path, {})
    order: list[str] = [k for k in meta if k != "__run__"] if isinstance(meta, dict) else []
    started: set[str] = set()
    finished: dict[str, dict] = {}
    if events_jsonl_path is not None and events_jsonl_path.exists():
        try:
            lines = events_jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            node = event.get("nodeid") if isinstance(event, dict) else None
            if not isinstance(node, str):
                continue
            if node not in order:
                order.append(node)
            if event.get("type") == "test_started":
                started.add(node)
            elif event.get("type") == "test_finished":
                finished[node] = event
    events = _read_json(steps_json_path, [])
    for event in events if isinstance(events, list) else []:
        node = event.get("nodeid") if isinstance(event, dict) else None
        if isinstance(node, str) and node and node not in order:
            order.append(node)
            started.add(node)

    results = []
    for node in order:
        parts = node.split("::")
        done = finished.get(node)
        if done is not None:
            status = {"PASSED": "PASSED", "FAILED": "FAILED", "SKIPPED": "SKIPPED"}.get(done.get("status"), "FAILED")
            duration = done.get("duration") if isinstance(done.get("duration"), (int, float)) else None
        elif node in started:
            status, duration = "ERROR", None
        else:
            status, duration = "SKIPPED", None
        results.append({
            "nodeid": node,
            "file_name": Path(parts[0]).name,
            "class_name": parts[1] if len(parts) >= 3 else None,
            "method_name": parts[-1],
            "status": status,
            "duration": duration,
            "interrupted": done is None and node in started,
        })
    return results


def read_private_typed_values(directory: Path) -> list[str]:
    """Typed values of sensitive fields, which the runner keeps out of steps.json (see
    selenium_compat_runner._keep_private). Used only to mask them; never stored or shown."""
    path = directory / "typed_values.json"
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []  # no sensitive value was typed in this run
    except (OSError, ValueError):
        # Present but unreadable: nothing is exposed (the file is never copied anywhere), but values
        # in it cannot be used for masking either. Say so, without any content.
        logger.warning("typed_values.json is unreadable; sensitive typed values cannot be masked by value")
        return []
    return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []


def store_report_json(db: Session, run_id: int, report_json: str | None) -> None:
    """Writes the report with plain SQL (the column is deliberately not part
    of the ORM model, so databases without it keep working unchanged)."""
    from sqlalchemy import text

    if report_json is None:
        return
    db.execute(
        text("UPDATE run_results SET report_json = :report WHERE run_id = :run_id"),
        {"report": report_json, "run_id": run_id},
    )


def load_report_json(db: Session, run_id: int) -> str | None:
    from sqlalchemy import text

    if not report_column_available():
        return None
    return db.execute(
        text("SELECT report_json FROM run_results WHERE run_id = :run_id"), {"run_id": run_id}
    ).scalar()


def _read_json(path: Path | None, default):
    if path is None or not path.exists():
        return default
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s (%s)", path.name, exc)
        return default


def _safe_report_inputs(testcases, events, meta, known_secrets):
    """The JSON execution report is stored and served like the rest of the results, so it is
    built from the same sanitized facts: typed values masked, errors / URLs / locators /
    identifiers cleaned of secrets, and internal fields (the real typed value) dropped. The
    report builder's own redaction (password=..., Bearer ...) is only a second line of defence.
    Identifiers are sanitized the same way on every side, so tests and steps still correlate."""

    def ident(value):
        return sanitize_identifier(value, known_secrets) if isinstance(value, str) else value

    def text(value):
        return mask_known_secrets(redact_secrets(value), known_secrets) if isinstance(value, str) else value

    safe_tests = [
        {**tc, "nodeid": ident(tc.get("nodeid")), "method_name": ident(tc.get("method_name"))} for tc in testcases
    ]

    failed_assertions: set = set()  # (nodeid, step number) of assertion steps that failed
    safe_events = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        item = {k: v for k, v in event.items() if k != "runtime_value"}
        item["nodeid"] = ident(item.get("nodeid"))
        target, value, error = sanitize_step(
            item.get("target"), item.get("value"), item.get("error"), known_secrets, action=item.get("action"),
        )
        item["target"], item["value"], item["error"] = target, value, error or ""
        if item.get("locator_value") is not None:
            item["locator_value"] = sanitize_step_target(item.get("locator_value"), known_secrets)
        if item.get("url"):
            item["url"] = mask_known_secrets(redact_url(item.get("url")), known_secrets)
        item["title"] = text(item.get("title"))
        item["skip_reason"] = text(item.get("skip_reason"))
        if item.get("status") == "FAIL" and item.get("step") is not None and (
            re.match(r"(?i)^(assert|verify)", str(item.get("action") or ""))
            or str(item.get("error") or "").startswith("AssertionError")
        ):
            failed_assertions.add((item["nodeid"], item["step"]))
        safe_events.append(item)

    safe_meta = {}
    for key, entry in (meta if isinstance(meta, dict) else {}).items():
        if key == "__run__" or not isinstance(entry, dict):
            safe_meta[key] = entry  # run-level settings, not a test's defined steps
            continue
        entry = dict(entry)
        node = ident(key)
        entry["defined_steps"] = [
            {
                **step,
                "target": sanitize_step_target(step.get("target"), known_secrets),
                # A passing assertion keeps its expected text; a FAILED one does not repeat it in the
                # report (the failure is reported by action, status and sanitized diagnostics).
                "value": "" if (node, step.get("number")) in failed_assertions
                and re.match(r"(?i)^(assert|verify)", str(step.get("action") or ""))
                else sanitize_step_value(step.get("target"), step.get("value"), known_secrets, step.get("action")),
            }
            for step in entry.get("defined_steps") or []
            if isinstance(step, dict)
        ]
        safe_meta[node] = entry

    return safe_tests, safe_events, safe_meta


def _build_report_json(
    run, project_name, testcases, steps_json_path, steps_meta_path, screenshots_dir, exit_code, stderr,
    known_secrets=(), interrupted=None,
) -> str | None:
    """Builds and validates the JSON execution report from the same facts
    being persisted. A failure here is logged loudly but never blocks
    persisting the rest of the results (the report is then simply absent)."""

    try:
        testcases, events, meta = _safe_report_inputs(
            testcases, _read_json(steps_json_path, []), _read_json(steps_meta_path, {}), known_secrets,
        )
        available = (
            {p.name for p in screenshots_dir.glob("*.png")} if screenshots_dir.exists() else set()
        )
        run_errors = []
        if interrupted:
            run_errors.append(f"Execution {interrupted}; the results in this report are partial.")
        if not testcases and exit_code not in (0, None):
            tail = (sanitize_process_output(stderr, known_secrets) or "").strip()[-500:]
            run_errors.append(
                f"pytest exited with code {exit_code} and reported no tests (collection or startup error)."
                + (f" Output: {tail}" if tail else "")
            )
        report = build_execution_report(
            run_id=run.id,
            test_results=testcases,
            events=events,
            meta=meta,
            run_started_at=run.started_at,
            browser=run.browser,
            project_id=run.project_id,
            project_name=project_name,
            exit_code=exit_code,
            available_screenshots=available,
            run_level_errors=run_errors,
        )
        return report_to_json(report)
    except Exception:
        logger.exception("Run %s: could not build the JSON execution report", run.id)
        return None


def _parse_junit_testcases(junit_report_path: Path) -> list[dict]:
    if not junit_report_path.exists():
        return []

    try:
        tree = ET.parse(junit_report_path)
    except ET.ParseError:
        logger.warning("JUnit report at %s is not valid XML", junit_report_path)
        return []

    results = []

    for testcase in tree.getroot().iter("testcase"):
        classname = testcase.get("classname") or ""
        method = testcase.get("name") or "<unknown test>"

        # pytest's junit classname for a module-level test function is just
        # the dotted module path (e.g. "test_login"); for a class-based test
        # it's "test_login.TestClass". Only treat the trailing component as
        # a real test class when it looks like one (matches
        # pytest_diagnostics.parse_junit_report's own heuristic so nodeids
        # line up with TestFailureDiagnostic matching).
        class_name = None
        module_parts = classname.split(".") if classname else []
        if len(module_parts) > 1 and module_parts[-1][:1].isupper():
            class_name = module_parts[-1]
            module_parts = module_parts[:-1]

        file_name = testcase.get("file")
        if not file_name:
            file_name = (".".join(module_parts) + ".py") if module_parts else (classname.replace(".", "/") + ".py" if classname else "unknown.py")
        file_name = Path(file_name).name

        if testcase.find("skipped") is not None:
            status = "SKIPPED"
        elif testcase.find("failure") is not None:
            status = "FAILED"
        elif testcase.find("error") is not None:
            status = "ERROR"
        else:
            status = _STATUS_MAP_DEFAULT

        nodeid = f"{file_name}::{class_name}::{method}" if class_name else f"{file_name}::{method}"

        duration_raw = testcase.get("time")
        try:
            duration = float(duration_raw) if duration_raw is not None else None
        except ValueError:
            duration = None

        results.append(
            {
                "nodeid": nodeid,
                "file_name": file_name,
                "class_name": class_name,
                "method_name": method,
                "status": status,
                "duration": duration,
            }
        )

    return results


def _read_steps_json(steps_json_path: Path, run: Run) -> dict[str, list[dict]]:
    if not steps_json_path.exists():
        logger.info("Run %s: no steps.json found — persisting zero step_results", run.id)
        return {}

    try:
        import json

        events = json.loads(steps_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Run %s: steps.json present but unreadable (%s)", run.id, exc)
        return {}

    grouped: dict[str, list[dict]] = {}
    for event in events:
        nodeid = event.get("nodeid") or ""
        grouped.setdefault(nodeid, []).append(event)

    return grouped


def _epoch_to_datetime(epoch_seconds):
    if epoch_seconds is None:
        return None
    try:
        from datetime import datetime

        return datetime.utcfromtimestamp(float(epoch_seconds))
    except (TypeError, ValueError, OSError):
        return None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    return value[-max_len:] if len(value) > max_len else value
