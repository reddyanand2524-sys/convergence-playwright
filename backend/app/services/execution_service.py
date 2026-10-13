"""

Runs a Run's project files through pytest in a throwaway temporary
directory, using a real subprocess (never exec/eval, never dynamic
in-process execution of untrusted code).

SECURITY NOTE: uploaded Python/Selenium files are untrusted code and this
engine executes them with the backend's own Python interpreter and OS
permissions. This is acceptable for a controlled local development
environment ONLY. It is NOT production-grade sandboxing (no containers,
no restricted user, no network/filesystem isolation beyond the temporary
directory). Do not expose this as-is to untrusted multi-tenant users in
production without adding real sandboxing.

This module intentionally owns its own SQLAlchemy session, because it is
invoked from a FastAPI BackgroundTask: by the time it runs, the request
that created it has already finished and that request's `db` session
dependency has been closed. Reusing it would be unsafe.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.core.security import settings
from app.database import SessionLocal
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.services.pytest_diagnostics import parse_junit_report
from app.services.failure_sanitizer import (
    collect_known_secrets,
    collect_typed_values,
    sanitize_process_output,
    sanitize_step,
)
from app.services import run_event_service
from app.services.test_file_selection import select_test_files
from app.services.result_persistence_service import (
    persist_run_results,
    read_private_typed_values,
    record_save_failure,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
_PROCESS_REGISTRY: dict[int, subprocess.Popen] = {}
_REGISTRY_LOCK = threading.Lock()


def request_cancel(run_id: int) -> bool:
    """Called from a request-handler thread (the cancel endpoint) to kill a
    live pytest subprocess for `run_id`, if one is currently registered.
    Returns True if a live process was found and a termination attempt was
    made, False if no process is currently registered for this run_id
    (e.g. it already finished, or never started)."""

    with _REGISTRY_LOCK:
        proc = _PROCESS_REGISTRY.get(run_id)

    if proc is None or proc.poll() is not None:
        return False

    logger.info("Run %s: cancellation requested — terminating live subprocess tree (pid=%s)", run_id, proc.pid)
    _terminate_process_tree(proc)
    return True


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """Kills `proc` and its full descendant tree (pytest -> chromedriver ->
    chrome, for Selenium tests). psutil is not a dependency of this project
    and is not added for this — on this Windows-only deployment,
    `taskkill /T /F /PID <pid>` kills the whole process tree without a new
    dependency. proc.terminate()/kill() is used as a fallback on non-Windows
    (not an expected deployment target here, but kept so the function
    doesn't hard-fail if ever run elsewhere, e.g. under CI on Linux)."""

    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                text=True,
            )
        else:
            proc.terminate()
    except Exception:
        logger.exception("Failed to terminate process tree for pid=%s", proc.pid)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            logger.exception("Failed to force-kill pid=%s after terminate timeout", proc.pid)
    except Exception:
        pass


# Selenium execution compatibility layer (browser constructor patching,
# implicit wait, resilient find_element/click, ActionChains compatibility,
# screenshot handling, Selenium IDE step correlation) ported from the
# platform's proven reference implementation. See
# app/services/selenium_compat_runner.py for the full behavior and the
# rationale for embedding it as a generated conftest.py rather than an
# installed pytest plugin. This source text is never applied to, or
# derived from, any uploaded ProjectFile — it is the execution engine's
# own generated file, written fresh into each Run's throwaway temp
# directory and deleted with it.
#
# Deliberately read by file PATH rather than `import`ed: that module
# monkeypatches selenium.webdriver/WebElement/ActionChains at module import
# time — exactly what we want inside the pytest subprocess it's generated
# into, but importing it here would apply those same patches to this
# backend server process itself.
_SELENIUM_COMPAT_RUNNER_PATH = Path(__file__).parent / "selenium_compat_runner.py"
_CONFTEST_CONTENT = _SELENIUM_COMPAT_RUNNER_PATH.read_text(encoding="utf-8")


def execute_run(run_id: int) -> None:
    """Entry point scheduled as a FastAPI BackgroundTask. Must never raise —
    any failure must end with the Run marked FAILED, not an unhandled
    exception, so it can't take the server down or leave the Run stuck."""

    db = SessionLocal()
    run_event_service.open_run(run_id)

    try:
        run = db.scalar(select(Run).where(Run.id == run_id))

        if not run:
            logger.error("execute_run: run_id=%s no longer exists", run_id)
            return

        logger.info(
            "Run %s: execution starting (project_id=%s, browser=%s)",
            run.id, run.project_id, run.browser,
        )

        project_files = db.scalars(
            select(ProjectFile).where(ProjectFile.project_id == run.project_id)
        ).all()

        logger.info(
            "Run %s: %d project file(s) found in database",
            run.id, len(project_files),
        )

        _run_with_temp_directory(db, run, project_files)

    except Exception:
        logger.exception("Run %s: execution service crashed unexpectedly", run_id)

        try:
            run = db.scalar(select(Run).where(Run.id == run_id))

            if run and run.status == "RUNNING":
                run.status = "FAILED"
                run.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            logger.exception(
                "Run %s: failed to mark FAILED after crash", run_id
            )

    finally:
        _publish_terminal_event(db, run_id)
        db.close()


def _publish_terminal_event(db, run_id: int) -> None:
    """Publishes the run's single terminal event from its FINAL database status, so
    it reflects the result logic (COMPLETED / FAILED / CANCELLED) and not merely
    that the subprocess exited. Never raises."""
    try:
        status = db.scalar(select(Run.status).where(Run.id == run_id))
    except Exception:
        logger.exception("Run %s: could not read final status for the event stream", run_id)
        status = None
    try:
        run_event_service.finish_run(run_id, status)
    except Exception:
        logger.exception("Run %s: could not publish terminal event", run_id)


def _poll_events(tailer, final: bool = False) -> None:
    if tailer is None:
        return
    try:
        tailer.drain() if final else tailer.poll()
    except Exception:
        logger.exception("Run %s: event polling failed", tailer.run_id)


def _run_with_temp_directory(db, run: Run, project_files: list[ProjectFile]) -> None:
    with tempfile.TemporaryDirectory(prefix=f"run_{run.id}_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        logger.info("Run %s: temporary execution directory created at %s", run.id, tmp_path)

        written_count = _write_python_files(tmp_path, project_files, run.id)

        if written_count == 0:
            logger.warning(
                "Run %s: project has no executable Python files — marking FAILED",
                run.id,
            )
            run.status = "FAILED"
            run.completed_at = datetime.utcnow()
            db.commit()
            return

        _write_conftest(tmp_path, run.id)
        _execute_pytest(db, run, tmp_path)
        # TemporaryDirectory context manager deletes tmp_path on exit,
        # even if _execute_pytest raised — cleanup always happens.


def _write_python_files(
    tmp_path: Path,
    project_files: list[ProjectFile],
    run_id: int,
) -> int:
    """Writes each project file's DB content into the temp directory.

    SECURITY: `file_name` is only ever a logical label (see Phase 1/3
    architecture notes) and must never be trusted as a safe relative path.
    `Path(name).name` strips any directory components / ".." segments,
    so a stored name like "../../evil.py" can only ever be written as
    "evil.py" directly inside tmp_path — it can never escape it.
    """

    written = 0

    for project_file in project_files:
        safe_name = Path(project_file.file_name).name

        if not safe_name or not safe_name.lower().endswith(".py"):
            continue

        target = tmp_path / safe_name

        # Defense in depth: confirm the resolved path is still inside
        # tmp_path before writing, in case of any edge case above.
        if target.resolve().parent != tmp_path.resolve():
            logger.warning(
                "Run %s: refusing to write suspicious file name %r",
                run_id, project_file.file_name,
            )
            continue

        target.write_text(project_file.file_content, encoding="utf-8")
        written += 1

    logger.info("Run %s: wrote %d Python file(s) to temp directory", run_id, written)

    return written


def _write_conftest(tmp_path: Path, run_id: int) -> None:
    """Writes the engine-generated conftest.py (Selenium compatibility
    layer: browser patching, implicit wait, resilient find/click,
    ActionChains compatibility, screenshots, step correlation) into the
    run's temp directory. This file is never derived from or written over
    any ProjectFile — uploaded test files are untouched."""

    (tmp_path / "conftest.py").write_text(_CONFTEST_CONTENT, encoding="utf-8")
    logger.info("Run %s: wrote execution-time conftest.py (Selenium compatibility layer)", run_id)


def _collect_run_secrets(tmp_path: Path) -> tuple[str, ...]:
    """Secrets for sanitizing this run's output: literals the uploaded scripts pass
    to secret-named fields, plus every value the run typed (from steps.json, which
    includes values that came from fixtures or data modules)."""
    secrets = list(
        collect_known_secrets(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in tmp_path.glob("*.py")
            if path.name != "conftest.py"
        )
    )
    try:
        events = json.loads((tmp_path / "steps.json").read_text(encoding="utf-8"))
        secrets.extend(
            collect_typed_values(
                (e.get("action"), recorded)
                for e in events
                for recorded in (e.get("value"), e.get("runtime_value"))
            )
        )
    except (OSError, ValueError, AttributeError):
        pass
    # typed values of sensitive fields, which the runner keeps out of steps.json
    secrets.extend(collect_typed_values(("type", value) for value in read_private_typed_values(tmp_path)))
    return tuple(dict.fromkeys(secrets))


# Names (case-insensitive, substring) of environment variables that must never be
# handed to uploaded test code: the backend's own credentials and secrets.
_ENV_DENYLIST = ("DATABASE_URL", "JWT_", "SMTP_", "SECRET", "PASSWORD", "PASSWD", "TOKEN", "API_KEY", "ACCESS_KEY")


def _subprocess_environment() -> dict[str, str]:
    """The pytest subprocess environment: the backend's environment minus its own
    credentials. Uploaded test code runs in this subprocess, so anything left in
    here can be read (and printed) by a test."""
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _ENV_DENYLIST)
    }


def _log_step_summary(run: Run, tmp_path: Path) -> None:
    """Logs the Selenium IDE step correlation captured by the compatibility
    layer (step number/action/target/value/status/error) for this run.
    steps.json lives only in the throwaway temp directory — there is no
    Run schema field to persist it into, so this is a log-only summary."""

    steps_path = tmp_path / "steps.json"

    if not steps_path.exists():
        return

    try:
        steps = json.loads(steps_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Run %s: steps.json present but unreadable", run.id)
        return

    failed_steps = [s for s in steps if s.get("status") == "FAIL"]

    logger.info(
        "Run %s: %d Selenium IDE step(s) recorded, %d failed",
        run.id, len(steps), len(failed_steps),
    )

    # The log must not carry typed secrets: sanitize exactly like the API/reports.
    secrets = _collect_run_secrets(tmp_path)

    for step in failed_steps:
        target, value, error = sanitize_step(
            step.get("target"), step.get("value"), step.get("error"), secrets,
            action=step.get("action"),
        )
        logger.error(
            "Run %s: step %s (%s %s%s) FAILED: %s",
            run.id, step.get("step"), step.get("action"), target,
            f" = {value}" if value else "",
            error,
        )


def _log_failure_diagnostics(run: Run, junit_report_path: Path) -> None:
    """Logs, per failing test, the exact uploaded-test source line and
    exception that caused the failure — not just the tail of the raw
    pytest/Selenium traceback, which can bury the user's own line under
    library frames."""

    diagnostics = parse_junit_report(junit_report_path)

    if not diagnostics:
        logger.warning(
            "Run %s: test(s) failed but no structured diagnostics could be "
            "extracted from the JUnit report", run.id,
        )
        return

    for diagnostic in diagnostics:
        logger.error("Run %s: %s", run.id, diagnostic.format())


def _pytest_targets(run_id: int, tmp_path: Path) -> list[str]:
    """What pytest is asked to collect. Normally the run directory (pytest finds
    test_*.py / *_test.py itself). When an uploaded file defines tests but is named
    otherwise ("tes(1).py", "login.py"), pytest's own discovery would skip it and exit
    with code 5 without importing anything, so the test-defining files are passed
    explicitly instead. Files are only read (parsed), never changed."""
    try:
        selected, by_content = select_test_files(tmp_path)
    except Exception:
        logger.exception("Run %s: could not inspect the uploaded files; using plain discovery", run_id)
        return [str(tmp_path)]

    if not by_content:
        return [str(tmp_path)]

    logger.info(
        "Run %s: %d file(s) define tests but are not named test_*.py or *_test.py; collecting them "
        "explicitly: %s",
        run_id, len(by_content), ", ".join(p.name for p in by_content),
    )
    return [str(path) for path in selected]


def _persist_results(db, run: Run, tmp_path: Path, junit_report_path: Path, *, returncode, stdout, stderr,
                     interrupted: str | None = None) -> bool:
    """Saves the run's results while the temporary directory still exists. True when saved.

    A failure is never silent: it is logged with its stage and exception class (no values), and
    a results row noting "saving failed" is stored, so the API and reports can tell this apart
    from a run that simply recorded nothing."""
    started = time.monotonic()
    logger.info("Run %s: stage=persist start exit_code=%s interrupted=%s", run.id, returncode, bool(interrupted))
    try:
        persist_run_results(
            db,
            run,
            exit_code=returncode,
            stdout=stdout,
            stderr=stderr,
            junit_report_path=junit_report_path,
            steps_json_path=tmp_path / "steps.json",
            steps_meta_path=tmp_path / "steps_meta.json",
            screenshots_dir=tmp_path / "screenshots",
            events_jsonl_path=tmp_path / "events.jsonl",
            interrupted=interrupted,
        )
        return True
    except Exception as exc:
        logger.exception("Run %s: result persistence failed (%s) after %.2fs", run.id, type(exc).__name__,
                         time.monotonic() - started)
        record_save_failure(db, run, exit_code=returncode, error_type=type(exc).__name__)
        return False


def _execute_pytest(db, run: Run, tmp_path: Path) -> None:
    # Uploaded test files hardcode their own webdriver.Chrome()/.Firefox()/
    # .Edge() calls regardless of the Run's selected browser. The generated
    # conftest.py (selenium_compat_runner.py) reads SELENIUM_BROWSER and
    # patches every Selenium browser constructor to redirect to it, so an
    # uploaded webdriver.Chrome() call actually opens whatever browser this
    # Run was created with.
    env = _subprocess_environment()
    env["SELENIUM_BROWSER"] = run.browser.lower()
    env["SELENIUM_COMPAT_RUN_DIR"] = str(tmp_path)
    env["SELENIUM_COMPAT_SCREENSHOT_DIR"] = str(tmp_path / "screenshots")
    # "all" mode captures a screenshot on every step (passing or failing),
    # not just on failure — selenium_compat_runner._record() already
    # implements this (capture_reason="ALL_MODE" for passing steps,
    # "STEP_FAILURE"/"TEST_FAILURE" for failures), it just needed this env
    # var set to actually turn on for real Runs (it previously defaulted to
    # "failed", so passing tests never got screenshots).
    env.setdefault("SELENIUM_COMPAT_SCREENSHOT_MODE", "all")

    # --tb=long and the JUnit report are both diagnostics-only: they change
    # how much pytest tells us about a failure, not whether a test passes,
    # and the XML report is pytest's own generated output written into the
    # throwaway run directory — never derived from or written over the
    # uploaded test file.
    junit_report_path = tmp_path / "_diagnostics_report.xml"

    command = [
        sys.executable, "-m", "pytest", *_pytest_targets(run.id, tmp_path),
        "-q", "--tb=long", f"--junitxml={junit_report_path}",
    ]

    # Phase 6A: check for a cancellation that arrived in the narrow window
    # between /start's synchronous RUNNING flip and this call actually
    # launching the subprocess. Without this check, a cancel request that
    # lands before the subprocess exists would have nothing to terminate.
    try:
        db.refresh(run)
    except Exception:
        logger.exception("Run %s: failed to refresh run before launch", run.id)

    if run.cancel_requested:
        logger.info(
            "Run %s: cancellation requested before pytest launch — skipping execution",
            run.id,
        )
        run.status = "CANCELLED"
        run.completed_at = datetime.utcnow()
        db.commit()
        return

    logger.info("Run %s: launching pytest subprocess: %s", run.id, " ".join(command))

    proc: subprocess.Popen | None = None
    tailer = None
    cancelled = False
    timed_out = False

    # Subprocess stdout/stderr are redirected to disk files rather than
    # subprocess.PIPE. The manual poll loop below only calls proc.poll()
    # on each tick and only reads output via communicate() AFTER the loop
    # breaks — it never drains the pipes while the process is still
    # running. An OS pipe has a small, fixed-size kernel buffer (observed
    # to deadlock well under the handful of KB a failing Selenium test's
    # --tb=long traceback + Selenium's own verbose exception text easily
    # produces); once that buffer fills, the child blocks inside its own
    # write() call to stdout/stderr and therefore never actually exits —
    # proc.poll() keeps returning None, not because the test is slow, but
    # because the child is stuck waiting for a reader that doesn't exist
    # yet. The loop then runs all the way to test_execution_timeout_seconds
    # (120s) and _terminate_process_tree() kills it, which is exactly why
    # this looked like a 120s hang ending in FAILED with zero persisted
    # results (the timed_out branch intentionally skips persistence).
    # Passing tests print only a few short "-q" dot/summary lines and never
    # fill the buffer, which is why they were never affected.
    #
    # Writing to real files instead of pipes sidesteps this entirely — a
    # file write is never blocked waiting on a concurrent reader — and is
    # consistent with the engine already reading pytest's JUnit XML report
    # from disk rather than over a pipe.
    stdout_path = tmp_path / "_pytest_stdout.log"
    stderr_path = tmp_path / "_pytest_stderr.log"

    try:
        with open(stdout_path, "w", encoding="utf-8", errors="replace") as stdout_f, \
                open(stderr_path, "w", encoding="utf-8", errors="replace") as stderr_f:
            proc = subprocess.Popen(
                command,
                cwd=str(tmp_path),
                env=env,
                stdout=stdout_f,
                stderr=stderr_f,
                text=True,
            )

            with _REGISTRY_LOCK:
                _PROCESS_REGISTRY[run.id] = proc

            # Live events: tail this run's own events.jsonl from the same loop that
            # already watches the subprocess. A failure here never affects the run.
            try:
                tailer = run_event_service.RunEventTailer(
                    run.id, tmp_path / "events.jsonl", lambda: _collect_run_secrets(tmp_path)
                )
                run_event_service.publish_run_started(run.id)
            except Exception:
                logger.exception("Run %s: live events unavailable", run.id)
                tailer = None

            # Manual poll loop (instead of a single blocking proc.wait(timeout=...))
            # so this thread can notice a cancellation signal (either the DB
            # cancel_requested flag, checked every poll interval, or a direct
            # kill performed by the cancel endpoint's own thread via
            # request_cancel()) and a timeout, without ever blocking so long
            # that a cancel request would sit unnoticed. stdout/stderr are
            # files here (see above), not pipes, so this loop never risks
            # the pipe-buffer deadlock that previously made failing (large
            # --tb=long output) runs hang until test_execution_timeout_seconds.
            poll_interval_seconds = 0.5
            deadline = time.monotonic() + settings.test_execution_timeout_seconds

            while True:
                returncode = proc.poll()
                _poll_events(tailer)
                if returncode is not None:
                    break

                try:
                    db.refresh(run)
                except Exception:
                    logger.exception("Run %s: failed to refresh run during poll loop", run.id)

                if run.cancel_requested:
                    cancelled = True
                    _terminate_process_tree(proc)
                    break

                if time.monotonic() >= deadline:
                    timed_out = True
                    _terminate_process_tree(proc)
                    break

                time.sleep(poll_interval_seconds)

            # proc has exited (or been killed) at this point — wait() just
            # reaps it (no output to drain over a pipe anymore, it's on
            # disk). The `with` block then closes/flushes both files before
            # we read them back below.
            proc.wait()
            returncode = proc.returncode
            _poll_events(tailer, final=True)  # everything the process wrote before it ended

        try:
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stdout = ""

        try:
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            stderr = ""

        logger.info("Run %s: pytest exited with return code %s", run.id, returncode)
        # Process output holds tracebacks and assertion text: sanitize before logging.
        log_secrets = _collect_run_secrets(tmp_path)
        logger.info(
            "Run %s: pytest stdout (tail):\n%s",
            run.id, (sanitize_process_output(stdout, log_secrets) or "")[-4000:],
        )

        if stderr:
            logger.info(
                "Run %s: pytest stderr (tail):\n%s",
                run.id, (sanitize_process_output(stderr, log_secrets) or "")[-4000:],
            )

        # Phase 6A fix — final authoritative cancellation check.
        #
        # Root cause of the FAILED-after-cancel race: the poll loop above
        # checks `proc.poll()` FIRST on every iteration and breaks
        # immediately ("if returncode is not None: break") the instant it
        # sees the process has exited, without checking `cancel_requested`
        # in that same iteration. The cancel endpoint (app/api/runs.py
        # cancel_run()) commits cancel_requested=True to the DB and THEN
        # calls request_cancel()->_terminate_process_tree() from its own
        # thread, independently of this loop's ~0.5s tick. If that kill
        # lands in the gap between two of this loop's iterations, the next
        # iteration's `proc.poll()` observes the process already dead (from
        # the OTHER thread's taskkill) and breaks via the top-of-loop path —
        # never reaching the `if run.cancel_requested` check below it — so
        # `cancelled` stays False even though cancel_requested was already
        # True in the DB. The subsequent status decision then only looked at
        # the (nonzero, killed-process) returncode and classified it as an
        # ordinary pytest failure.
        #
        # Fix: regardless of which branch broke the loop (natural exit,
        # this-thread's own cancel detection, or timeout), do one more
        # fresh DB read for cancel_requested here, after the process has
        # fully exited and communicate() has drained its pipes. Cancellation
        # always wins: if cancel_requested is true at this point, the run is
        # CANCELLED, never FAILED/COMPLETED, no matter what the return code
        # or any exception says.
        #
        # Chosen rule for "cancel races with natural completion" (the
        # process exits on its own right as /cancel is requested): since
        # there is no way to recover true cross-thread/cross-process event
        # ordering here, this code treats "cancel_requested is true at the
        # moment THIS loop observes/finishes handling the process's exit"
        # as "cancellation was requested and acted upon before the natural
        # exit was observed" by definition — i.e. cancellation wins whenever
        # the flag is visible by the time we reach this check. This is a
        # narrow, already-rare window (the cancel endpoint only reaches this
        # UPDATE for a still-RUNNING row) and erring toward CANCELLED in it
        # is the safer choice: it can never fabricate a false PASS/FAIL
        # result for a run the caller explicitly tried to stop.
        try:
            db.refresh(run)
        except Exception:
            logger.exception(
                "Run %s: failed to refresh run for final cancellation check", run.id,
            )

        if run.cancel_requested:
            cancelled = True

        if cancelled:
            logger.info("Run %s: cancelled by request — subprocess terminated", run.id)
            run.status = "CANCELLED"
            # Do NOT persist test_results/screenshots for a cancelled run —
            # pytest never finished, so there is no meaningful result tree
            # to persist (and persist_run_results assumes a complete JUnit
            # report, which a killed run does not reliably produce).

        elif timed_out:
            logger.error(
                "Run %s: execution timed out after %ss — marking FAILED",
                run.id, settings.test_execution_timeout_seconds,
            )
            run.status = "FAILED"
            # pytest was killed, so it never wrote its JUnit report - but the runner records
            # tests, steps and screenshots as it goes. Keep everything that exists and say the
            # results are partial; do not discard a run's evidence because it ran out of time.
            _persist_results(
                db, run, tmp_path, junit_report_path, returncode=returncode, stdout=stdout, stderr=stderr,
                interrupted=f"run timed out after {settings.test_execution_timeout_seconds}s",
            )

        else:
            run.status = "COMPLETED" if returncode == 0 else "FAILED"

            _log_step_summary(run, tmp_path)

            if returncode == 5:
                logger.warning(
                    "Run %s: pytest collected no tests (exit code 5): no uploaded file defines a "
                    "test it can find. No test results will be recorded.", run.id,
                )
            elif run.status == "FAILED":
                _log_failure_diagnostics(run, junit_report_path)

            # Phase 5: persist the full result tree (test results, step
            # results, screenshots, failure diagnostics) while everything is
            # still on disk, before the TemporaryDirectory is deleted. A
            # persistence failure must never leave the Run looking COMPLETED
            # with nothing persisted — force FAILED in that case even if
            # pytest's own exit code was 0.
            if not _persist_results(
                db, run, tmp_path, junit_report_path, returncode=returncode, stdout=stdout, stderr=stderr,
            ):
                run.status = "FAILED"

    except Exception:
        logger.exception("Run %s: unexpected error launching/running pytest", run.id)

        # A termination-induced exception (e.g. proc.communicate() raising
        # after taskkill tore down the pipes, or any other error arising
        # from the kill itself) must never be classified as a generic
        # "unexpected error -> FAILED" when a cancellation was actually in
        # progress. Re-check cancel_requested (fresh DB read) and the
        # in-loop `cancelled` flag before deciding: cancellation still wins.
        try:
            db.refresh(run)
        except Exception:
            logger.exception(
                "Run %s: failed to refresh run while handling exception", run.id,
            )

        if cancelled or run.cancel_requested:
            run.status = "CANCELLED"
        else:
            run.status = "FAILED"

    finally:
        if proc is not None:
            with _REGISTRY_LOCK:
                _PROCESS_REGISTRY.pop(run.id, None)

        run.completed_at = datetime.utcnow()
        db.commit()
        logger.info("Run %s: execution finished with status %s", run.id, run.status)
