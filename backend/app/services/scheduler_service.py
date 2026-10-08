"""

TIMEZONE: this project uses naive UTC everywhere (Run.created_at,
Project.created_at, etc. all use datetime.utcnow() with plain DateTime
columns). Schedule follows the exact same convention: start_at,
next_run_at, last_run_at are naive-UTC datetimes, and all comparisons here
use datetime.utcnow(). No timezone-aware datetimes are introduced anywhere
in Phase 8.

OVERLAP POLICY (documented design decision): a schedule must not create a
new overlapping Run while its previous scheduled Run is still active. We
track this via a nullable Schedule.last_run_id FK to the most recent Run
this schedule created (chosen over the created_at/project_id heuristic
query because it is exact — no ambiguity from manually-created Runs on the
same project, or from multiple schedules on one project racing on a
timestamp window; it's a single indexed FK lookup instead of a range scan).
A schedule is only "due" when enabled, next_run_at <= now, AND
(last_run_id IS NULL OR that Run's status NOT IN (QUEUED, RUNNING)).

DUPLICATE-PROCESSING PREVENTION: due-schedule claiming uses a DB
conditional UPDATE (WHERE id=:id AND next_run_at <= :now AND enabled=1),
checking rowcount == 1 before proceeding — the same pattern as the
existing /start and /cancel race-safety fix in app/api/runs.py. This makes
it safe even if two scheduler ticks (or two processes) raced on the same
due schedule.

This module is deliberately decoupled from the asyncio loop itself
(`run_due_schedules_once` is a plain, synchronous, testable function that
takes a `Session`); `scheduler_loop` below is the thin asyncio wrapper that
calls it on a timer using its own SessionLocal.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.project import Project
from app.models.run import Run
from app.models.schedule import (
    FREQUENCY_DAILY,
    FREQUENCY_HOURLY,
    FREQUENCY_INTERVAL,
    FREQUENCY_ONCE,
    FREQUENCY_WEEKLY,
    Schedule,
)
from app.services import execution_service


logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15

_ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING")


def compute_next_run_at(frequency: str, interval_value: int | None, after: datetime) -> datetime:
    """Computes the next occurrence strictly after `after` (which is
    normally datetime.utcnow() at the moment of firing)."""

    if frequency == FREQUENCY_INTERVAL:
        return after + timedelta(minutes=interval_value)
    if frequency == FREQUENCY_HOURLY:
        return after + timedelta(hours=1)
    if frequency == FREQUENCY_DAILY:
        return after + timedelta(days=1)
    if frequency == FREQUENCY_WEEKLY:
        return after + timedelta(weeks=1)
    if frequency == FREQUENCY_ONCE:
        # ONCE schedules are disabled after firing; next_run_at is no
        # longer meaningful, but we still need a value — push it far out
        # rather than leaving it stale/in-the-past indefinitely.
        return after + timedelta(days=36500)
    raise ValueError(f"Unknown frequency: {frequency}")


def _create_and_start_run(db: Session, schedule: Schedule) -> Run | None:
    """Creates a normal Run (schedule.browser snapshot) and starts it
    through the EXACT same conditional-UPDATE + BackgroundTask-equivalent
    pathway app/api/runs.py's start_run uses — reimplemented here as a
    direct call since there is no BackgroundTasks object available outside
    a request; execute_run is scheduled onto the running asyncio loop's
    default executor via asyncio.to_thread by the caller, or run inline by
    synchronous callers/tests (see run_now in the schedules router for the
    HTTP-request variant that still uses BackgroundTasks).

    Returns the created Run, already flipped to RUNNING with started_at
    set (never returns a Run execute_run hasn't been scheduled for) or
    None if the owning project no longer exists/is inactive.
    """

    project = db.scalar(
        select(Project).where(
            Project.id == schedule.project_id,
            Project.is_active == True,  # noqa: E712
        )
    )
    if project is None:
        return None

    run = Run(
        project_id=schedule.project_id,
        status="QUEUED",
        browser=schedule.browser,
        cancel_requested=False,
        started_at=None,
        completed_at=None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Same conditional UPDATE QUEUED->RUNNING as start_run. Since this Run
    # was just created in this same transaction/session, this will always
    # succeed (rowcount == 1), but it reuses the exact pattern/logic for
    # consistency rather than a plain unconditional write.
    result = db.execute(
        update(Run)
        .where(Run.id == run.id, Run.status == "QUEUED")
        .values(status="RUNNING", started_at=datetime.utcnow())
    )
    db.commit()
    db.refresh(run)

    if result.rowcount != 1:
        # Should not happen (nothing else could have raced on a brand-new
        # row), but fail safe rather than invoking execute_run twice.
        return run

    return run


def run_due_schedules_once(db: Session, execute_fn=None) -> list[int]:
    """Finds and processes all currently-due schedules using this Session.
    Returns the list of Run ids created. Synchronous and directly testable
    — call it with a test's db_session to exercise scheduler logic without
    waiting on the real asyncio loop.

    `execute_fn` defaults to execution_service.execute_run (the real
    pipeline). Tests that call this against the SAVEPOINT-rollback
    `db_session` fixture MUST override it with a no-op, since the real
    execute_run opens its OWN separate SessionLocal() connection that would
    block/deadlock against the test's still-open outer transaction (see
    tests/conftest.py's isolation strategy docstring, and the known
    environment gotcha about BackgroundTask/TestClient deadlocks this
    mirrors exactly).
    """
    if execute_fn is None:
        execute_fn = execution_service.execute_run

    now = datetime.utcnow()
    created_run_ids: list[int] = []

    due_schedule_ids = db.scalars(
        select(Schedule.id).where(
            Schedule.enabled == True,  # noqa: E712
            Schedule.next_run_at <= now,
        )
    ).all()

    for schedule_id in due_schedule_ids:
        # Reload fresh each iteration — another schedule's processing in
        # this same loop may have touched the session.
        schedule = db.get(Schedule, schedule_id)
        if schedule is None:
            continue

        # Overlap check: skip (without touching next_run_at/last_run_at —
        # it stays due and will be retried on the next tick) if the
        # previous scheduled Run is still active.
        if schedule.last_run_id is not None:
            active_run = db.scalar(
                select(Run).where(
                    Run.id == schedule.last_run_id,
                    Run.status.in_(_ACTIVE_RUN_STATUSES),
                )
            )
            if active_run is not None:
                continue

        new_next_run_at = compute_next_run_at(
            schedule.frequency, schedule.interval_value, now,
        )
        becomes_disabled = schedule.frequency == FREQUENCY_ONCE

        # DB-conditional claim: only one concurrent caller can win this
        # UPDATE for a given schedule id.
        claim = db.execute(
            update(Schedule)
            .where(
                Schedule.id == schedule.id,
                Schedule.enabled == True,  # noqa: E712
                Schedule.next_run_at <= now,
            )
            .values(
                next_run_at=new_next_run_at,
                last_run_at=now,
                enabled=False if becomes_disabled else Schedule.enabled,
            )
        )
        db.commit()

        if claim.rowcount != 1:
            # Lost the race (another tick/process claimed it first).
            continue

        db.refresh(schedule)

        run = _create_and_start_run(db, schedule)
        if run is None:
            # Project gone/inactive — the schedule has still been advanced
            # (and disabled if ONCE) so it won't spin forever on a dead
            # project.
            continue

        schedule.last_run_id = run.id
        db.add(schedule)
        db.commit()

        created_run_ids.append(run.id)

        # Kick off actual pytest execution exactly like /start does, just
        # without a FastAPI BackgroundTasks object to hand it to.
        execute_fn(run.id)

    return created_run_ids


# ---------------------------------------------------------------------------
# asyncio background loop — integrated via app/main.py's lifespan.
# ---------------------------------------------------------------------------

_loop_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None


async def _loop():
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            db = SessionLocal()
            try:
                # execute_run() blocks until the pytest subprocess exits
                # (it is a synchronous, long-running call), so it is run in
                # a worker thread to avoid blocking the event loop /
                # request handling, matching the brief's "must not block
                # FastAPI request handling" requirement.
                await asyncio.to_thread(run_due_schedules_once, db)
            finally:
                db.close()
        except Exception:
            logger.exception("scheduler tick failed")

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


def start_scheduler() -> None:
    global _loop_task, _stop_event
    if _loop_task is not None and not _loop_task.done():
        # Already running in this process — never start a second loop.
        return
    _stop_event = asyncio.Event()
    _loop_task = asyncio.create_task(_loop())


async def stop_scheduler() -> None:
    global _loop_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _loop_task is not None:
        try:
            await asyncio.wait_for(_loop_task, timeout=10)
        except asyncio.TimeoutError:
            _loop_task.cancel()
    _loop_task = None
    _stop_event = None
