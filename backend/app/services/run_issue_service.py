"""
Explains a finished run that has no test results.

Previously the results page said only "No test results were captured for this run" for
every such run. The cause is different each time and the evidence is already stored:
pytest's real exit code and its captured output. This module turns that evidence into
a specific, honest explanation. It never invents a test result, a pass or a failure: it
only reports what the exit code and output show, and says so when it cannot tell.

pytest exit codes: 0 all passed, 1 tests failed, 2 interrupted (e.g. a collection
error), 3 internal error, 4 usage error, 5 no tests collected.
"""
from dataclasses import dataclass, field
from typing import Iterable

from app.services.failure_sanitizer import sanitize_process_output

_OUTPUT_LIMIT = 2000
_MAX_FILES_LISTED = 10


@dataclass
class RunIssue:
    code: str
    title: str
    detail: str
    hints: list[str] = field(default_factory=list)
    exit_code: int | None = None
    output_tail: str | None = None


def _tail(stdout: str | None, stderr: str | None, secrets: Iterable[str]) -> str | None:
    parts = []
    for label, text in (("stdout", stdout), ("stderr", stderr)):
        cleaned = (sanitize_process_output(text, secrets) or "").strip()
        if cleaned:
            parts.append(f"[{label}]\n{cleaned[-_OUTPUT_LIMIT:]}")
    return "\n\n".join(parts) or None


def _file_list(file_names: list[str]) -> str:
    shown = ", ".join(file_names[:_MAX_FILES_LISTED])
    extra = len(file_names) - _MAX_FILES_LISTED
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def describe_run_issue(
    *,
    run_status: str,
    has_run_result: bool,
    exit_code: int | None,
    stdout: str | None,
    stderr: str | None,
    file_names: list[str],
    secrets: Iterable[str] = (),
) -> RunIssue | None:
    """The explanation for a COMPLETED/FAILED run that recorded no test cases, or None
    when the run is not in that situation."""
    secrets = tuple(secrets)
    tail = _tail(stdout, stderr, secrets)

    if not has_run_result:
        return RunIssue(
            code="RESULTS_NOT_RECORDED",
            title="The run ended before its results were saved",
            detail=(
                f"The run finished with status {run_status}, but no result record exists for it. "
                "The most likely causes are that it exceeded the execution time limit and was stopped, "
                "or that saving the results failed."
            ),
            hints=["Check how long the run took, and run it again.",
                   "The backend log for this run holds the details."],
        )

    base = {"exit_code": exit_code, "output_tail": tail}

    if exit_code == 5:
        listed = _file_list(file_names) if file_names else "none"
        return RunIssue(
            code="NO_TESTS_COLLECTED",
            title="No tests were found in the uploaded files",
            detail=(
                "pytest started but collected no tests (exit code 5), so none of your script's code ran. "
                f"Uploaded files: {listed}."
            ),
            hints=[
                "A test needs a function named test_*, or a class named Test* with test_* methods.",
                "Convergence runs files named test_*.py or *_test.py, and any other uploaded file that defines such tests.",
                "If the file has a syntax error pytest cannot read it; open the file and check it.",
            ],
            **base,
        )
    if exit_code == 2:
        return RunIssue(
            code="COLLECTION_ERROR",
            title="pytest could not load the tests",
            detail=(
                "pytest was interrupted while collecting the tests (exit code 2), typically because a "
                "script has a syntax error or imports a module that is not available. No test ran."
            ),
            hints=["The output below shows the first error pytest met."],
            **base,
        )
    if exit_code == 3:
        return RunIssue(
            code="PYTEST_INTERNAL_ERROR",
            title="pytest stopped because of an internal error",
            detail="pytest reported an internal error (exit code 3) before any test result was written.",
            hints=["The output below shows the error."],
            **base,
        )
    if exit_code == 4:
        return RunIssue(
            code="PYTEST_USAGE_ERROR",
            title="pytest could not be started as requested",
            detail="pytest rejected its command line or configuration (exit code 4). No test ran.",
            hints=["This points at the execution environment rather than the script."],
            **base,
        )
    if exit_code is None or exit_code < 0 or exit_code > 5:
        shown = "unknown" if exit_code is None else str(exit_code)
        return RunIssue(
            code="PROCESS_ENDED_ABNORMALLY",
            title="The test process ended abnormally",
            detail=f"The pytest process ended with exit code {shown} and wrote no test results.",
            hints=["It may have crashed or been killed (for example by running out of memory)."],
            **base,
        )
    if exit_code == 1:
        return RunIssue(
            code="RESULT_REPORT_UNREADABLE",
            title="pytest reported failures but no per-test results were saved",
            detail=(
                "pytest exited with code 1 (some test failed) but its result report was missing or "
                "unreadable, so individual tests cannot be listed."
            ),
            hints=["The output below is what pytest printed."],
            **base,
        )
    return RunIssue(
        code="NO_TEST_CASES_RECORDED",
        title="pytest finished but recorded no test cases",
        detail="pytest exited with code 0 but its result report contains no test cases.",
        hints=["Check that the uploaded files define tests."],
        **base,
    )


# ---------------------------------------------------------------------------
# Run notes: a run that did not end normally says so, in its stored data
# ---------------------------------------------------------------------------
# When a run is stopped at the execution time limit, or its results could not be saved, the
# backend stores a one-line note at the top of that run's stderr ("[convergence] timeout: ...").
# It is how the results API, the reports and the UI tell "partial results" and "saving failed"
# apart from an ordinary run, without a schema change. It is written by the backend after the
# process output was sanitized, so nothing the test printed can end up in it.
RUN_NOTE_PREFIX = "[convergence] "
_NOTE_KINDS = ("timeout", "save-failed")


def make_run_note(kind: str, detail: str) -> str:
    return f"{RUN_NOTE_PREFIX}{kind}: {detail}\n"


def parse_run_note(stderr: str | None) -> tuple[str, str] | None:
    """(kind, detail) of the run note at the top of a stored stderr, or None."""
    if not stderr or not stderr.startswith(RUN_NOTE_PREFIX):
        return None
    kind, _, detail = stderr.split("\n", 1)[0][len(RUN_NOTE_PREFIX):].partition(": ")
    return (kind, detail) if kind in _NOTE_KINDS else None


def strip_run_note(stderr: str | None) -> str | None:
    return stderr.split("\n", 1)[1] if parse_run_note(stderr) and "\n" in (stderr or "") else (
        None if parse_run_note(stderr) else stderr
    )


def describe_run_note(
    note: tuple[str, str],
    *,
    total_tests: int,
    unfinished_tests: int,
    exit_code: int | None,
    stdout: str | None,
    stderr: str | None,
    secrets: Iterable[str] = (),
) -> RunIssue:
    """The explanation for a run whose stored note says it was stopped or could not be saved."""
    kind, detail = note
    tail = _tail(stdout, strip_run_note(stderr), tuple(secrets))
    if kind == "timeout":
        import re

        match = re.search(r"after (\d+)s", detail)
        limit = f"{match.group(1)} s" if match else "the execution time limit"
        recorded = (
            f"{total_tests} test(s) were recorded" + (f", {unfinished_tests} of them did not finish" if unfinished_tests else "")
            if total_tests else "no test had been recorded yet"
        )
        return RunIssue(
            code="RUN_TIMED_OUT",
            title="The run timed out; these results are partial",
            detail=(
                f"The run was stopped after {limit} (the execution time limit) before pytest finished, "
                f"so {recorded}. Everything recorded up to that moment is shown."
            ),
            hints=[
                "A test that was still running is marked ERROR: it did not finish, so its outcome is unknown.",
                "Each element that is not found can cost about 30 s with the default waits, so several failed "
                "steps can use up the whole limit. Fixing the failing locators is the first thing to try.",
                "The limit is TEST_EXECUTION_TIMEOUT_SECONDS on the backend (default 120).",
            ],
            exit_code=exit_code,
            output_tail=tail,
        )
    return RunIssue(
        code="RESULTS_SAVE_FAILED",
        title="The test ran, but saving its results failed",
        detail=(
            f"pytest finished, but storing the results was not possible ({detail}). "
            "No test results or reports exist for this run, although the test itself did execute."
        ),
        hints=["The backend log for this run (look for 'stage=persist') holds the technical details.",
               "Run it again; if it keeps happening the database needs attention."],
        exit_code=exit_code,
        output_tail=tail,
    )
