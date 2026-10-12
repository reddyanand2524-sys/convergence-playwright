from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.security import settings
from app.database import SessionLocal, get_db
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.screenshot import Screenshot
from app.models.test_result import TestResult
from app.models.user import User
from app.schemas.run import RunHistoryItem, RunHistoryResponse, RunResponse
from app.services import execution_service, run_event_service


MAX_PAGE_SIZE = 100


router = APIRouter(
    prefix="/api/projects",
    tags=["Runs"],
)


def _get_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
) -> Project:
    """Same ownership convention as projects.py / project_files.py:
    a project that doesn't belong to the current user (or doesn't
    exist, or is inactive) is treated as not found."""

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


@router.post(
    "/{project_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_run(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)

    run = Run(
        project_id=project.id,
        status="QUEUED",
        browser=project.browser,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
    )

    db.add(run)
    db.commit()
    db.refresh(run)

    return run


@router.get(
    "/{project_id}/runs",
    response_model=RunHistoryResponse,
)
def get_project_runs(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, gt=0, le=MAX_PAGE_SIZE),
    status_filter: Optional[
        Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    ] = Query(default=None, alias="status"),
    browser: Optional[str] = Query(default=None),
    run_id: Optional[int] = Query(default=None, gt=0),
    created_from: Optional[date] = Query(default=None),
    created_to: Optional[date] = Query(default=None),
    search: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):

    project = _get_owned_project(project_id, db, current_user)

    if created_from is not None and created_to is not None and created_from > created_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="created_from must not be after created_to",
        )

    filters = [Run.project_id == project.id]

    if status_filter is not None:
        filters.append(Run.status == status_filter)

    if browser is not None:
        filters.append(Run.browser == browser)

    if run_id is not None:
        filters.append(Run.id == run_id)

    if created_from is not None:
        filters.append(Run.created_at >= datetime.combine(created_from, datetime.min.time()))

    if created_to is not None:
        filters.append(
            Run.created_at < datetime.combine(created_to, datetime.min.time()) + timedelta(days=1)
        )

    if search is not None and search != "":
        search_conditions = [
            Run.browser.ilike(f"%{search}%"),
            Run.status.ilike(f"%{search}%"),
        ]
        if search.isdigit():
            search_conditions.append(Run.id == int(search))
        filters.append(or_(*search_conditions))

    base_query = select(Run).where(*filters)

    total = db.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0

    total_pages = (total + page_size - 1) // page_size if total else 0

    runs = db.scalars(
        base_query
        .order_by(Run.created_at.desc(), Run.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    run_ids = [r.id for r in runs]

    # Batch-fetch RunResult rows for this page's runs in one query (no
    # RunResult row at all for QUEUED/RUNNING/CANCELLED runs, per
    # result_persistence_service's design — those simply fall back to the
    # zero-count defaults below).
    results_by_run_id: dict[int, RunResult] = {}
    if run_ids:
        for rr in db.scalars(
            select(RunResult).where(RunResult.run_id.in_(run_ids))
        ).all():
            results_by_run_id[rr.run_id] = rr

    # Single aggregated screenshot-count query (COUNT(*) joining
    # screenshots -> test_results, grouped by run_id) rather than one query
    # per run.
    screenshot_counts_by_run_id: dict[int, int] = {}
    if run_ids:
        rows = db.execute(
            select(TestResult.run_id, func.count(Screenshot.id))
            .join(Screenshot, Screenshot.test_result_id == TestResult.id)
            .where(TestResult.run_id.in_(run_ids))
            .group_by(TestResult.run_id)
        ).all()
        screenshot_counts_by_run_id = {run_id: count for run_id, count in rows}

    items = []
    for run in runs:
        rr = results_by_run_id.get(run.id)

        duration_seconds = None
        if run.started_at is not None and run.completed_at is not None:
            duration_seconds = (run.completed_at - run.started_at).total_seconds()

        items.append(
            RunHistoryItem(
                id=run.id,
                project_id=run.project_id,
                status=run.status,
                browser=run.browser,
                created_at=run.created_at,
                started_at=run.started_at,
                completed_at=run.completed_at,
                duration_seconds=duration_seconds,
                test_count=rr.total_tests if rr else 0,
                passed_count=rr.passed_count if rr else 0,
                failed_count=rr.failed_count if rr else 0,
                skipped_count=rr.skipped_count if rr else 0,
                screenshot_count=screenshot_counts_by_run_id.get(run.id, 0),
            )
        )

    return RunHistoryResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get(
    "/{project_id}/runs/{run_id}",
    response_model=RunResponse,
)
def get_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    return _get_owned_run(project, run_id, db)


@router.post(
    "/{project_id}/runs/{run_id}/start",
    response_model=RunResponse,
)
def start_run(
    project_id: int,
    run_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)

    # Conditional UPDATE (not read-then-write) to avoid a TOCTOU race when
    # two concurrent /start requests for the same run_id both pass the
    # plain status check before either commits: only one UPDATE can match
    # a row still in QUEUED (SQL Server row-level locking on the UPDATE
    # serializes the two statements), so only one request ever flips the
    # row to RUNNING and schedules execute_run; the other sees rowcount==0
    # and gets 409, same pattern already used by cancel_run/retry_run's
    # analogous race concerns.
    result = db.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == "QUEUED")
        .values(status="RUNNING", started_at=datetime.utcnow())
    )

    if result.rowcount == 0:
        db.rollback()
        db.refresh(run)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run cannot be started from status '{run.status}'. "
                f"Only a QUEUED Run can be started."
            ),
        )

    db.commit()
    db.refresh(run)

    background_tasks.add_task(execution_service.execute_run, run.id)

    return run


def _current_run_status(run_id: int) -> str | None:
    """Fresh, short-lived read of the run's status (the request's own session is
    closed once the response starts streaming)."""
    with SessionLocal() as session:
        return session.scalar(select(Run.status).where(Run.id == run_id))


@router.get("/{project_id}/runs/{run_id}/events")
def stream_run_events(
    project_id: int,
    run_id: int,
    request: Request,
    after: Optional[int] = Query(default=None, ge=0),
    last_event_id: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Server-Sent Events for one run: live step events, replayable from `after`
    (or the Last-Event-ID header; the bearer token is never put in a URL)."""
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)

    cursor = after
    if cursor is None:
        cursor = int(last_event_id) if last_event_id and last_event_id.strip().isdigit() else 0

    buffer = run_event_service.get_buffer(run.id)
    if buffer is None:
        if run.status in ("QUEUED", "RUNNING"):
            buffer = run_event_service.open_run(run.id)  # execution attaches to the same buffer
        else:
            buffer = run_event_service.replay_from_results(
                db,
                run,
                db.scalars(select(ProjectFile.file_content).where(ProjectFile.project_id == project.id)),
            )

    async def probe() -> str | None:
        return await run_in_threadpool(_current_run_status, run.id)

    return StreamingResponse(
        run_event_service.sse_stream(
            buffer,
            cursor,
            request.is_disconnected,
            status_probe=probe,
            max_seconds=settings.test_execution_timeout_seconds + 120,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


_CANCELLABLE_STATUSES = ("QUEUED", "RUNNING")


@router.post(
    "/{project_id}/runs/{run_id}/cancel",
    response_model=RunResponse,
)
def cancel_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    run = _get_owned_run(project, run_id, db)

    if run.status not in _CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run cannot be cancelled from status '{run.status}'. "
                f"Only a QUEUED or RUNNING Run can be cancelled."
            ),
        )

    was_running = run.status == "RUNNING"

    # Conditional UPDATE (not read-then-write) to avoid a lost-update race
    # against /start flipping QUEUED->RUNNING, or against execute_run's own
    # background thread finishing (COMPLETED/FAILED) concurrently. Only a
    # row still in QUEUED/RUNNING at the moment of the UPDATE is touched.
    #
    # A QUEUED run has no execute_run background task to ever act on
    # cancel_requested (it was never started), so this endpoint flips its
    # status straight to CANCELLED itself. A RUNNING run's status is left
    # alone here — cancel_requested is set, and the live execute_run
    # background thread (which owns that Run row's final status
    # transition) is the one that flips it to CANCELLED once it notices
    # the flag / its subprocess is killed below, same as the FAILED/
    # COMPLETED paths it already owns exclusively.
    if run.status == "QUEUED":
        result = db.execute(
            update(Run)
            .where(Run.id == run.id, Run.status == "QUEUED")
            .values(status="CANCELLED", cancel_requested=True, completed_at=datetime.utcnow())
        )
    else:
        # cancel_requested == False is also required here (not just
        # status == "RUNNING"): the RUNNING -> CANCELLED status flip is
        # owned exclusively by execute_run's background thread, so a
        # RUNNING run can sit with cancel_requested already True for a
        # while before that flip lands. Without this extra condition, a
        # second /cancel call in that window would match the same WHERE
        # clause as the first and also return 200, instead of the intended
        # "first succeeds, second is a conflict" duplicate-cancel contract.
        result = db.execute(
            update(Run)
            .where(Run.id == run.id, Run.status == "RUNNING", Run.cancel_requested == False)  # noqa: E712
            .values(cancel_requested=True)
        )

    if result.rowcount == 0:
        db.rollback()
        db.refresh(run)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run cannot be cancelled from status '{run.status}'. "
                f"Only a QUEUED or RUNNING Run can be cancelled."
            ),
        )

    db.commit()
    db.refresh(run)

    if was_running:
        # Reach into the (possibly) still-running background task's live
        # subprocess and terminate it directly — the DB flag alone does
        # nothing to a subprocess.Popen a thread is blocked waiting on
        # unless that thread is polling it, which execute_run does, but
        # this makes termination immediate rather than waiting for the
        # next ~0.5s poll tick.
        execution_service.request_cancel(run.id)

    return run



_RETRYABLE_STATUSES = ("COMPLETED", "FAILED")


@router.post(
    "/{project_id}/runs/{run_id}/retry",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_run(
    project_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    source_run = _get_owned_run(project, run_id, db)

    if source_run.status not in _RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Run cannot be retried from status '{source_run.status}'. "
                f"Only a COMPLETED or FAILED Run can be retried."
            ),
        )

    new_run = Run(
        project_id=source_run.project_id,
        status="QUEUED",
        browser=source_run.browser,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
    )

    db.add(new_run)
    db.commit()
    db.refresh(new_run)

    return new_run
