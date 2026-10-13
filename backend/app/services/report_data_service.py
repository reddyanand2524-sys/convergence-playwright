"""


Builds a single normalized, read-only ReportData structure for a Run, used
by all three renderers (HTML/Excel/PDF) so that every format is guaranteed
to present identical numbers — they all come from the exact same object.

Batching strategy: one query for TestResult rows, then one IN(...) query
each for StepResult / Screenshot / FailureDiagnostic rows across all
test_result_ids (never per-row, never per-test N+1).

Never fabricates TestResult.started_at/completed_at or
FailureDiagnostic.raw_traceback — passes through actual DB state only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.services.run_issue_service import describe_run_issue, parse_run_note
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.services import failure_analysis_service
from app.services.failure_analysis_service import FailureAnalysis
from app.services.failure_sanitizer import (
    collect_known_secrets,
    collect_typed_values,
    sanitize_failure_fields,
    sanitize_identifier,
    sanitize_step,
)


@dataclass
class ScreenshotData:
    id: int
    file_name: str
    mime_type: str
    capture_reason: str
    content: bytes
    size_bytes: int


@dataclass
class FailureData:
    exception_type: str | None
    exception_message: str | None
    source_file: str | None
    source_line_number: int | None
    source_line: str | None
    raw_traceback: str | None


@dataclass
class StepData:
    id: int
    sequence_index: int
    step_number: int | None
    display_name: str
    action: str
    target: str | None
    value: str | None
    status: str
    duration_seconds: float | None
    error: str | None
    occurred_at: datetime | None = None
    screenshots: list[ScreenshotData] = field(default_factory=list)


@dataclass
class TestData:
    id: int
    nodeid: str
    project_file_id: int | None
    file_name: str
    class_name: str | None
    method_name: str
    status: str
    duration_seconds: float | None
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[StepData] = field(default_factory=list)
    screenshots: list[ScreenshotData] = field(default_factory=list)
    failure: FailureData | None = None
    # Same rule-based analysis the results API returns (None for passing tests).
    analysis: FailureAnalysis | None = None


@dataclass
class SummaryData:
    total_tests: int
    passed_count: int
    failed_count: int
    skipped_count: int
    total_steps: int
    passed_steps: int
    failed_steps: int
    screenshot_count: int
    failure_count: int
    duration_seconds: float
    exit_code: int | None


@dataclass
class RunData:
    id: int
    project_id: int
    browser: str
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    cancel_requested: bool


@dataclass
class ProjectData:
    id: int
    name: str


@dataclass
class ReportData:
    project: ProjectData
    run: RunData
    summary: SummaryData
    tests: list[TestData] = field(default_factory=list)
    # Set when the run did not end normally (stopped at the time limit: partial results; saving
    # failed) or recorded no tests: a report must not read like a clean, complete run.
    notice: str | None = None


def _display_name(action: str, target: str | None, value: str | None) -> str:
    parts = [action]
    if target:
        parts.append(target)
    if value:
        parts.append(value)
    return " | ".join(parts)


def build_report_data(db: Session, run: Run, project: Project) -> ReportData:
    run_result = db.scalar(select(RunResult).where(RunResult.run_id == run.id))

    test_rows: list[TestResult] = list(
        db.scalars(
            select(TestResult).where(TestResult.run_id == run.id).order_by(TestResult.id)
        ).all()
    )
    test_result_ids = [t.id for t in test_rows]

    steps_by_test: dict[int, list[StepResult]] = {}
    screenshots_by_test: dict[int, list[Screenshot]] = {}
    screenshots_by_step: dict[int, list[Screenshot]] = {}
    failure_by_test: dict[int, FailureDiagnostic] = {}

    if test_result_ids:
        step_rows = db.scalars(
            select(StepResult)
            .where(StepResult.test_result_id.in_(test_result_ids))
            .order_by(StepResult.test_result_id, StepResult.sequence_index)
        ).all()
        for step_row in step_rows:
            steps_by_test.setdefault(step_row.test_result_id, []).append(step_row)

        screenshot_rows = db.scalars(
            select(Screenshot).where(Screenshot.test_result_id.in_(test_result_ids))
        ).all()
        for shot in screenshot_rows:
            if shot.step_result_id is not None:
                screenshots_by_step.setdefault(shot.step_result_id, []).append(shot)
            else:
                screenshots_by_test.setdefault(shot.test_result_id, []).append(shot)

        failure_rows = db.scalars(
            select(FailureDiagnostic).where(
                FailureDiagnostic.test_result_id.in_(test_result_ids)
            )
        ).all()
        for f in failure_rows:
            failure_by_test[f.test_result_id] = f

    def _to_screenshot_data(shot: Screenshot) -> ScreenshotData:
        return ScreenshotData(
            id=shot.id,
            file_name=shot.file_name,
            mime_type=shot.mime_type,
            capture_reason=shot.capture_reason,
            content=shot.content,
            size_bytes=shot.size_bytes,
        )

    # Literals the uploaded scripts pass to secret-named fields (masked in all output).
    script_secrets = collect_known_secrets(
        db.scalars(select(ProjectFile.file_content).where(ProjectFile.project_id == project.id))
    )

    total_duration = 0.0
    total_steps = 0
    passed_steps = 0
    failed_steps = 0
    screenshot_count = 0
    failure_count = 0

    tests: list[TestData] = []
    for test_row in test_rows:
        if test_row.duration_seconds:
            total_duration += test_row.duration_seconds

        # Script literals plus every value this test typed (older rows still hold
        # the typed values, which are then masked everywhere they surface).
        known_secrets = tuple(dict.fromkeys((
            *script_secrets,
            *collect_typed_values((s.action, s.value) for s in steps_by_test.get(test_row.id, [])),
        )))

        steps: list[StepData] = []
        for step_row in steps_by_test.get(test_row.id, []):
            total_steps += 1
            if step_row.status == "PASS":
                passed_steps += 1
            elif step_row.status == "FAIL":
                failed_steps += 1

            step_shots = [
                _to_screenshot_data(s) for s in screenshots_by_step.get(step_row.id, [])
            ]
            screenshot_count += len(step_shots)

            # Sanitized once here, so the HTML, Excel and PDF reports (which all
            # read StepData) show the same masked values as the API.
            step_target, step_value, step_error = sanitize_step(
                step_row.target, step_row.value, step_row.error, known_secrets,
                action=step_row.action,
            )
            steps.append(
                StepData(
                    id=step_row.id,
                    sequence_index=step_row.sequence_index,
                    step_number=step_row.step_number,
                    display_name=_display_name(
                        step_row.action, step_target, step_value
                    ),
                    action=step_row.action,
                    target=step_target,
                    value=step_value,
                    status=step_row.status,
                    duration_seconds=step_row.duration_seconds,
                    error=step_error,
                    occurred_at=step_row.occurred_at,
                    screenshots=step_shots,
                )
            )

        test_level_shots = [
            _to_screenshot_data(s) for s in screenshots_by_test.get(test_row.id, [])
        ]
        screenshot_count += len(test_level_shots)

        failure_row = failure_by_test.get(test_row.id)
        failure_data = None
        if failure_row:
            failure_count += 1
            # Sanitized (stored rows untouched) so every report format shows the
            # same redacted text as the API.
            exc_type, exc_message, src_line, traceback_text = sanitize_failure_fields(
                failure_row.exception_type,
                failure_row.exception_message,
                failure_row.source_line,
                failure_row.raw_traceback,
                known_secrets,
            )
            failure_data = FailureData(
                exception_type=exc_type,
                exception_message=exc_message,
                source_file=failure_row.source_file,
                source_line_number=failure_row.source_line_number,
                source_line=src_line,
                raw_traceback=traceback_text,
            )

        tests.append(
            TestData(
                id=test_row.id,
                nodeid=sanitize_identifier(test_row.nodeid, known_secrets),
                project_file_id=test_row.project_file_id,
                file_name=test_row.file_name,
                class_name=test_row.class_name,
                method_name=sanitize_identifier(test_row.method_name, known_secrets),
                status=test_row.status,
                duration_seconds=test_row.duration_seconds,
                started_at=test_row.started_at,
                completed_at=test_row.completed_at,
                steps=steps,
                screenshots=test_level_shots,
                failure=failure_data,
                analysis=failure_analysis_service.safe_analyze(
                    failure_analysis_service.input_from_records(
                        test_row,
                        steps_by_test.get(test_row.id, []),
                        screenshots_by_step,
                        screenshots_by_test.get(test_row.id, []),
                        failure_row,
                        run.browser,
                        run.completed_at,
                        known_secrets,
                    )
                ),
            )
        )

    if run_result is not None:
        total_tests = run_result.total_tests
        passed_count = run_result.passed_count
        failed_count = run_result.failed_count
        skipped_count = run_result.skipped_count
        exit_code = run_result.exit_code
    else:
        total_tests = len(test_rows)
        passed_count = sum(1 for t in test_rows if t.status == "PASSED")
        failed_count = sum(1 for t in test_rows if t.status in ("FAILED", "ERROR"))
        skipped_count = sum(1 for t in test_rows if t.status == "SKIPPED")
        exit_code = None

    summary = SummaryData(
        total_tests=total_tests,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        total_steps=total_steps,
        passed_steps=passed_steps,
        failed_steps=failed_steps,
        screenshot_count=screenshot_count,
        failure_count=failure_count,
        duration_seconds=total_duration,
        exit_code=exit_code,
    )

    run_duration_seconds = None
    if run.started_at and run.completed_at:
        run_duration_seconds = (run.completed_at - run.started_at).total_seconds()

    run_data = RunData(
        id=run.id,
        project_id=run.project_id,
        browser=run.browser,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_seconds=run_duration_seconds,
        cancel_requested=run.cancel_requested,
    )

    project_data = ProjectData(id=project.id, name=project.name)

    notice = None
    if run_result is not None:
        note = parse_run_note(run_result.stderr)
        if note is not None:
            kind, detail = note
            notice = f"Partial results: {detail}." if kind == "timeout" else f"Results incomplete: {detail}."
        elif total_tests == 0 and run.status in ("COMPLETED", "FAILED"):
            issue = describe_run_issue(
                run_status=run.status, has_run_result=True, exit_code=run_result.exit_code,
                stdout=None, stderr=None, file_names=[],
            )
            notice = f"{issue.title}." if issue else None

    return ReportData(project=project_data, run=run_data, summary=summary, tests=tests, notice=notice)
