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
