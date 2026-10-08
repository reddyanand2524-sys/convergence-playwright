from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.project import Project
from app.models.run import Run
from app.models.schedule import FREQUENCY_ONCE, Schedule
from app.models.user import User
from app.schemas.run import RunResponse
from app.schemas.schedule import (
    CreateScheduleRequest,
    ScheduleResponse,
    UpdateScheduleRequest,
)
from app.services import execution_service
from app.services.scheduler_service import compute_next_run_at


router = APIRouter(
    prefix="/api/projects",
    tags=["Schedules"],
)


def _get_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
) -> Project:
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


def _get_owned_schedule(project: Project, schedule_id: int, db: Session) -> Schedule:
    schedule = db.scalar(
        select(Schedule).where(
            Schedule.id == schedule_id,
            Schedule.project_id == project.id,
        )
    )
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found",
        )
    return schedule


@router.post(
    "/{project_id}/schedules",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    project_id: int,
    body: CreateScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)

    # Server computes next_run_at — the client never controls it directly.
    # First occurrence is start_at itself (if start_at is in the future,
    # the schedule simply isn't due yet; if start_at is in the past/now,
    # it is immediately due on the next scheduler tick).
    schedule = Schedule(
        project_id=project.id,
        name=body.name,
        browser=body.browser,
        frequency=body.frequency,
        interval_value=body.interval_value,
        start_at=body.start_at,
        next_run_at=body.start_at,
        last_run_at=None,
        enabled=body.enabled,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return schedule


@router.get(
    "/{project_id}/schedules",
    response_model=list[ScheduleResponse],
)
def list_schedules(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    schedules = db.scalars(
        select(Schedule)
        .where(Schedule.project_id == project.id)
        .order_by(Schedule.id.desc())
    ).all()
    return schedules


@router.get(
    "/{project_id}/schedules/{schedule_id}",
    response_model=ScheduleResponse,
)
def get_schedule(
    project_id: int,
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    return _get_owned_schedule(project, schedule_id, db)


@router.patch(
    "/{project_id}/schedules/{schedule_id}",
    response_model=ScheduleResponse,
)
def update_schedule(
    project_id: int,
    schedule_id: int,
    body: UpdateScheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    schedule = _get_owned_schedule(project, schedule_id, db)

    recompute = False

    if body.name is not None:
        schedule.name = body.name
    if body.browser is not None:
        schedule.browser = body.browser
    if body.frequency is not None and body.frequency != schedule.frequency:
        schedule.frequency = body.frequency
        recompute = True
    if "interval_value" in body.model_fields_set:
        schedule.interval_value = body.interval_value
        recompute = True
    if body.start_at is not None and body.start_at != schedule.start_at:
        schedule.start_at = body.start_at
        recompute = True
    if body.enabled is not None:
        schedule.enabled = body.enabled

    # Cross-field validation after merge: INTERVAL must have interval_value,
    # others must not.
    from app.models.schedule import FREQUENCY_INTERVAL

    if schedule.frequency == FREQUENCY_INTERVAL:
        if schedule.interval_value is None or schedule.interval_value <= 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="interval_value must be a positive integer when frequency is INTERVAL",
            )
    else:
        if schedule.interval_value is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="interval_value must not be set unless frequency is INTERVAL",
            )

    if recompute:
        # PATCH never triggers execution itself — only recomputes timing.
        schedule.next_run_at = schedule.start_at

    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return schedule


@router.delete(
    "/{project_id}/schedules/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_schedule(
    project_id: int,
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    schedule = _get_owned_schedule(project, schedule_id, db)

    # Deletes ONLY the Schedule row. No cascade to Run/results — Run has no
    # FK back to Schedule at all (only Schedule -> Run via last_run_id),
    # and nothing in this app deletes Run rows.
    db.delete(schedule)
    db.commit()
    return None


@router.post(
    "/{project_id}/schedules/{schedule_id}/enable",
    response_model=ScheduleResponse,
)
def enable_schedule(
    project_id: int,
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    schedule = _get_owned_schedule(project, schedule_id, db)

    # Re-enabling recalculates next_run_at from "now" forward using the
    # schedule's own frequency, rather than leaving a possibly long-stale
    # next_run_at in place (which would otherwise fire immediately/
    # repeatedly for every missed tick while it was disabled). This does
    # NOT create a Run itself — it only makes the schedule eligible again;
    # the very next scheduler tick will pick it up if that recalculated
    # time is already <= now (e.g. for a frequency whose next occurrence
    # from "now" is immediate, such as INTERVAL/HOURLY/DAILY/WEEKLY, their
    # next_run_at is simply now + their period, so it will NOT be
    # immediately due). ONCE schedules re-enabled this way become due again
    # at "now" since they have no periodic recomputation semantics of their
    # own — documented edge case: re-enabling a ONCE schedule effectively
    # re-arms it to fire ~immediately.
    now = datetime.utcnow()
    if schedule.frequency == FREQUENCY_ONCE:
        schedule.next_run_at = now
    else:
        schedule.next_run_at = compute_next_run_at(
            schedule.frequency, schedule.interval_value, now,
        )
    schedule.enabled = True

    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return schedule


@router.post(
    "/{project_id}/schedules/{schedule_id}/disable",
    response_model=ScheduleResponse,
)
def disable_schedule(
    project_id: int,
    schedule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    schedule = _get_owned_schedule(project, schedule_id, db)

    schedule.enabled = False

    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    return schedule


@router.post(
    "/{project_id}/schedules/{schedule_id}/run-now",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_now(
    project_id: int,
    schedule_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manual immediate execution. Does NOT alter next_run_at/last_run_at —
    the schedule's recurring timing is unaffected by a manual run-now.

    Best-effort duplicate-rapid-fire protection: if this schedule's
    last_run_id Run is still QUEUED/RUNNING, reject with 409 rather than
    stacking overlapping manual runs (same overlap policy the scheduler
    tick itself applies, reused here for consistency; lower priority than
    the scheduler's own DB-conditional-claim mechanism per the brief, and
    this is a plain read-check — a genuinely simultaneous double-click
    could in theory both pass this check, which is accepted as a known,
    documented best-effort limitation for this manual endpoint only).
    """

    project = _get_owned_project(project_id, db, current_user)
    schedule = _get_owned_schedule(project, schedule_id, db)

    if schedule.last_run_id is not None:
        active_run = db.scalar(
            select(Run).where(
                Run.id == schedule.last_run_id,
                Run.status.in_(("QUEUED", "RUNNING")),
            )
        )
        if active_run is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This schedule already has an active Run in progress.",
            )

    run = Run(
        project_id=project.id,
        status="QUEUED",
        browser=schedule.browser,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    result = db.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == "QUEUED")
        .values(status="RUNNING", started_at=datetime.utcnow())
    )
    db.commit()
    db.refresh(run)

    if result.rowcount != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run could not be started.",
        )

    schedule.last_run_id = run.id
    db.add(schedule)
    db.commit()

    background_tasks.add_task(execution_service.execute_run, run.id)

    return run
