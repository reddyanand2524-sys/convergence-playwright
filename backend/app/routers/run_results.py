from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.models.user import User
from app.schemas.run_result import (
    FailureAnalysisSchema,
    FailureDiagnosticSchema,
    RunIssueSchema,
    RunResultsResponse,
    RunResultsSummarySchema,
    ScreenshotRefSchema,
    StepResultSchema,
    TestResultSchema,
)
from app.services import failure_analysis_service
from app.services.run_issue_service import describe_run_issue
from app.services.failure_sanitizer import (
    collect_known_secrets,
    collect_typed_values,
    sanitize_failure_fields,
    sanitize_identifier,
    sanitize_step,
)
from app.services.report_data_service import build_report_data
from app.services.report_excel_service import render_excel_report
from app.services.report_html_service import render_html_report
from app.services.report_pdf_service import render_pdf_report


router = APIRouter(
    prefix="/api/projects",
    tags=["Run Results"],
)

def _analysis_schema(analysis, screenshot_rows, to_ref) -> FailureAnalysisSchema:
    schema = FailureAnalysisSchema.model_validate(analysis, from_attributes=True)
    shot = next((s for s in screenshot_rows if s.id == analysis.screenshot_id), None)
    schema.failure_screenshot = to_ref(shot) if shot is not None else None
    return schema


_PENDING_STATUSES = ("QUEUED", "RUNNING", "CANCELLED")
_REPORTABLE_STATUSES = ("COMPLETED", "FAILED")


def _get_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
) -> Project:
    """Same ownership convention as runs.py / projects.py / project_files.py
    — duplicated locally per-router by design, not imported."""

    project = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == current_user.id,
            Project.is_active == True,  # noqa: E712
        )
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project


def _get_owned_run(
    project: Project,
    run_id: int,
    db: Session,
) -> Run:
    run = db.scalar(
        select(Run).where(
            Run.id == run_id,
            Run.project_id == project.id,
        )
    )

    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run not found",
        )

    return run


@router.get(
    "/{project_id}/runs/{run_id}/results",
    response_model=RunResultsResponse,
)
def get_run_results(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)

    if run.status in _PENDING_STATUSES:
        return RunResultsResponse(run=run, summary=None, tests=None)

    run_result = db.scalar(select(RunResult).where(RunResult.run_id == run.id))

    test_rows = db.scalars(
        select(TestResult).where(TestResult.run_id == run.id).order_by(TestResult.id)
    ).all()

    tests: list[TestResultSchema] = []
    total_duration = 0.0
    # Literals the uploaded scripts pass to secret-named fields (script source is
    # stored in the database); masked wherever they surface in this response.
    script_secrets = collect_known_secrets(
        db.scalars(select(ProjectFile.file_content).where(ProjectFile.project_id == project.id))
    )

    for test_row in test_rows:
        if test_row.duration_seconds:
            total_duration += test_row.duration_seconds

        step_rows = db.scalars(
            select(StepResult)
            .where(StepResult.test_result_id == test_row.id)
            .order_by(StepResult.sequence_index)
        ).all()

        # Script literals plus every value this test typed (older rows still hold
        # the typed values, which are then masked everywhere they surface).
        known_secrets = tuple(dict.fromkeys((
            *script_secrets,
            *collect_typed_values((s.action, s.value) for s in step_rows),
        )))

        screenshot_rows = db.scalars(
            select(Screenshot).where(Screenshot.test_result_id == test_row.id)
        ).all()

        screenshots_by_step: dict[int, list[Screenshot]] = {}
        test_level_screenshots: list[Screenshot] = []
        for shot in screenshot_rows:
            if shot.step_result_id is not None:
                screenshots_by_step.setdefault(shot.step_result_id, []).append(shot)
            else:
                test_level_screenshots.append(shot)

        def _to_screenshot_ref(shot: Screenshot) -> ScreenshotRefSchema:
            return ScreenshotRefSchema(
                id=shot.id,
                file_name=shot.file_name,
                mime_type=shot.mime_type,
                capture_reason=shot.capture_reason,
                url=f"/api/projects/{project_id}/runs/{run_id}/screenshots/{shot.id}",
            )

        steps: list[StepResultSchema] = []
        for step_row in step_rows:
            # Stored rows (including historical ones) are sanitized on output.
            step_target, step_value, step_error = sanitize_step(
                step_row.target, step_row.value, step_row.error, known_secrets,
                action=step_row.action,
            )
            steps.append(
                StepResultSchema(
                    id=step_row.id,
                    sequence_index=step_row.sequence_index,
                    step_number=step_row.step_number,
                    action=step_row.action,
                    target=step_target,
                    value=step_value,
                    status=step_row.status,
                    duration_seconds=step_row.duration_seconds,
                    error=step_error,
                    screenshots=[
                        _to_screenshot_ref(s)
                        for s in screenshots_by_step.get(step_row.id, [])
                    ],
                )
            )

        failure_row = db.scalar(
            select(FailureDiagnostic).where(FailureDiagnostic.test_result_id == test_row.id)
        )

        failure_schema = None
        if failure_row:
            # Same sanitizing as the analysis (stored rows stay untouched): the
            # raw exception text / source line can contain typed secrets or
            # run-directory paths.
            exc_type, exc_message, src_line, traceback_text = sanitize_failure_fields(
                failure_row.exception_type,
                failure_row.exception_message,
                failure_row.source_line,
                failure_row.raw_traceback,
                known_secrets,
            )
            failure_schema = FailureDiagnosticSchema(
                exception_type=exc_type,
                exception_message=exc_message,
                source_file=failure_row.source_file,
                source_line_number=failure_row.source_line_number,
                source_line=src_line,
                raw_traceback=traceback_text,
            )

        # Computed on read from the records above (never stored); safe_analyze
        # logs and returns None on any unexpected failure so results still load.
        analysis_schema = None
        analysis = failure_analysis_service.safe_analyze(
            failure_analysis_service.input_from_records(
                test_row, step_rows, screenshots_by_step, test_level_screenshots,
                failure_row, run.browser, run.completed_at, known_secrets,
            )
        )
        if analysis is not None:
            analysis_schema = _analysis_schema(analysis, screenshot_rows, _to_screenshot_ref)

        tests.append(
            TestResultSchema(
                id=test_row.id,
                nodeid=sanitize_identifier(test_row.nodeid, known_secrets),
                file_name=test_row.file_name,
                class_name=test_row.class_name,
                method_name=sanitize_identifier(test_row.method_name, known_secrets),
                status=test_row.status,
                duration_seconds=test_row.duration_seconds,
                steps=steps,
                failure=failure_schema,
                analysis=analysis_schema,
                screenshots=[
                    _to_screenshot_ref(s) for s in test_level_screenshots
                ],
            )
        )

    summary = None
    if run_result is not None:
        summary = RunResultsSummarySchema(
            exit_code=run_result.exit_code,
            total_tests=run_result.total_tests,
            passed_count=run_result.passed_count,
            failed_count=run_result.failed_count,
            skipped_count=run_result.skipped_count,
            duration_seconds=total_duration,
        )

    issue = None
    if not tests and run.status in ("COMPLETED", "FAILED"):
        # A finished run with no test cases: say why, from the stored exit code and output.
        found = describe_run_issue(
            run_status=run.status,
            has_run_result=run_result is not None,
            exit_code=run_result.exit_code if run_result is not None else None,
            stdout=run_result.stdout if run_result is not None else None,
            stderr=run_result.stderr if run_result is not None else None,
            file_names=list(db.scalars(select(ProjectFile.file_name).where(ProjectFile.project_id == project.id))),
            secrets=script_secrets,
        )
        if found is not None:
            issue = RunIssueSchema(**found.__dict__)

    return RunResultsResponse(run=run, summary=summary, tests=tests, issue=issue)


@router.get(
    "/{project_id}/runs/{run_id}/screenshots/{screenshot_id}",
)
def get_screenshot(
    project_id: int,
    run_id: int,
    screenshot_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)

    row = db.execute(
        select(Screenshot)
        .join(TestResult, TestResult.id == Screenshot.test_result_id)
        .where(
            Screenshot.id == screenshot_id,
            TestResult.run_id == run.id,
        )
    ).first()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Screenshot not found",
        )

    screenshot = row[0]

    return Response(content=screenshot.content, media_type=screenshot.mime_type)


def _require_reportable_run(run: Run) -> None:
    if run.status not in _REPORTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is {run.status}; report is only available for COMPLETED or FAILED runs",
        )


@router.get("/{project_id}/runs/{run_id}/report/html")
def get_run_report_html(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)
    _require_reportable_run(run)

    report = build_report_data(db, run, project)
    html = render_html_report(report)

    return Response(
        content=html,
        media_type="text/html",
        headers={
            "Content-Disposition": f'attachment; filename="Convergence_Run_{run_id}.html"'
        },
    )


@router.get("/{project_id}/runs/{run_id}/report/excel")
def get_run_report_excel(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)
    _require_reportable_run(run)

    report = build_report_data(db, run, project)
    content = render_excel_report(report)

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="Convergence_Run_{run_id}.xlsx"'
        },
    )


@router.get("/{project_id}/runs/{run_id}/report/pdf")
def get_run_report_pdf(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)
    _require_reportable_run(run)

    report = build_report_data(db, run, project)
    content = render_pdf_report(report)

    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="Convergence_Run_{run_id}.pdf"'
        },
    )
