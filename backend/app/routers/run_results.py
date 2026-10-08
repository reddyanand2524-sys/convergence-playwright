from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult
from app.models.user import User
from app.schemas.run_result import (
    FailureDiagnosticSchema,
    RunResultsResponse,
    RunResultsSummarySchema,
    ScreenshotRefSchema,
    StepResultSchema,
    TestResultSchema,
)
from app.services.report_data_service import build_report_data
from app.services.report_excel_service import render_excel_report
from app.services.report_html_service import render_html_report
from app.services.report_pdf_service import render_pdf_report


router = APIRouter(
    prefix="/api/projects",
    tags=["Run Results"],
)

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

    for test_row in test_rows:
        if test_row.duration_seconds:
            total_duration += test_row.duration_seconds

        step_rows = db.scalars(
            select(StepResult)
            .where(StepResult.test_result_id == test_row.id)
            .order_by(StepResult.sequence_index)
        ).all()

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
            steps.append(
                StepResultSchema(
                    id=step_row.id,
                    sequence_index=step_row.sequence_index,
                    step_number=step_row.step_number,
                    action=step_row.action,
                    target=step_row.target,
                    value=step_row.value,
                    status=step_row.status,
                    duration_seconds=step_row.duration_seconds,
                    error=step_row.error,
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
            failure_schema = FailureDiagnosticSchema(
                exception_type=failure_row.exception_type,
                exception_message=failure_row.exception_message,
                source_file=failure_row.source_file,
                source_line_number=failure_row.source_line_number,
                source_line=failure_row.source_line,
                raw_traceback=failure_row.raw_traceback,
            )

        tests.append(
            TestResultSchema(
                id=test_row.id,
                nodeid=test_row.nodeid,
                file_name=test_row.file_name,
                class_name=test_row.class_name,
                method_name=test_row.method_name,
                status=test_row.status,
                duration_seconds=test_row.duration_seconds,
                steps=steps,
                failure=failure_schema,
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

    return RunResultsResponse(run=run, summary=summary, tests=tests)


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
