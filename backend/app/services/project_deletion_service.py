"""Permanent deletion of a project and everything it owns.

Ownership / authorization is checked by the caller (the router); this module
only performs the deletion and assumes `project` is the caller's own project.

Safety design
-------------
* One transaction, rolled back on any exception; nothing is committed until
  every dependent row has been deleted.
* Foreign keys are never disabled and cascades are never relied on: every
  dependent table is deleted explicitly, children first (see _DELETE_ORDER).
* Coordination with the scheduler and the execution engine:
    1. A conditional UPDATE on the project row (is_active -> 0) takes a row
       lock held until commit/rollback. The scheduler's _create_and_start_run
       re-reads the project before creating a Run, and any concurrent Run
       INSERT needs a shared lock on this parent row for its foreign-key
       check, so both wait for this transaction and then find the project
       gone (the scheduler returns None; the INSERT fails its FK check).
    2. Schedules are disabled so no tick can claim them.
    3. QUEUED runs have no process: they are flipped to CANCELLED with the same
       conditional UPDATE the cancel endpoint uses, so a concurrent /start
       (which only updates rows still QUEUED) cannot launch them.
    4. A RUNNING run owns a live pytest/browser subprocess whose lifecycle
       belongs to its background thread, so deletion is refused (409) instead
       of deleting rows out from under it. The user cancels and waits first.
"""
import logging
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.failure_diagnostic import FailureDiagnostic
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.run import Run
from app.models.run_result import RunResult
from app.models.schedule import Schedule
from app.models.screenshot import Screenshot
from app.models.step_result import StepResult
from app.models.test_result import TestResult

logger = logging.getLogger(__name__)


class ProjectNotFoundError(Exception):
    """The project row no longer exists (e.g. a concurrent delete won)."""


class ActiveRunsError(Exception):
    """The project has RUNNING runs, which cannot be safely deleted."""

    def __init__(self, run_ids: list[int]):
        self.run_ids = run_ids
        super().__init__(f"Project has {len(run_ids)} running run(s): {run_ids}")


def delete_project(db: Session, project: Project) -> dict[str, int]:
    """Deletes `project` and all data it owns. Commits on success.

    Raises ProjectNotFoundError / ActiveRunsError after rolling back. Any other
    failure is rolled back here too and re-raised unchanged (the original SQL
    Server error is never swallowed). Returns rows deleted per table.
    """
    try:
        project_id = project.id

        # 1. Lock the project row and mark it inactive (rolled back on failure).
        locked = db.execute(
            update(Project).where(Project.id == project_id).values(is_active=False)
        )
        if locked.rowcount != 1:
            db.rollback()
            raise ProjectNotFoundError(project_id)

        # 2. Nothing may schedule new runs from here on.
        db.execute(
            update(Schedule)
            .where(Schedule.project_id == project_id, Schedule.enabled == True)  # noqa: E712
            .values(enabled=False)
        )

        # 3. QUEUED runs have no process; cancel them (same conditional UPDATE as
        #    the cancel endpoint) so a racing /start cannot launch them.
        db.execute(
            update(Run)
            .where(Run.project_id == project_id, Run.status == "QUEUED")
            .values(status="CANCELLED", cancel_requested=True, completed_at=datetime.utcnow())
        )

        # 4. A RUNNING run has a live subprocess owned by its background thread.
        running = list(
            db.scalars(
                select(Run.id).where(Run.project_id == project_id, Run.status == "RUNNING")
            )
        )
        if running:
            db.rollback()
            raise ActiveRunsError(running)

        # 5. Explicit, child-first deletion scoped to this project only.
        run_ids = select(Run.id).where(Run.project_id == project_id)
        test_result_ids = select(TestResult.id).where(TestResult.run_id.in_(run_ids))

        steps = (
            ("screenshots", delete(Screenshot).where(Screenshot.test_result_id.in_(test_result_ids))),
            ("failure_diagnostics", delete(FailureDiagnostic).where(FailureDiagnostic.test_result_id.in_(test_result_ids))),
            ("step_results", delete(StepResult).where(StepResult.test_result_id.in_(test_result_ids))),
            ("test_results", delete(TestResult).where(TestResult.run_id.in_(run_ids))),
            ("run_results", delete(RunResult).where(RunResult.run_id.in_(run_ids))),
            # schedules.last_run_id references runs, so schedules go before runs.
            ("schedules", delete(Schedule).where(Schedule.project_id == project_id)),
            ("runs", delete(Run).where(Run.project_id == project_id)),
            ("project_files", delete(ProjectFile).where(ProjectFile.project_id == project_id)),
            ("projects", delete(Project).where(Project.id == project_id)),
        )

        counts: dict[str, int] = {}
        for table, statement in steps:
            counts[table] = db.execute(
                statement.execution_options(synchronize_session=False)
            ).rowcount

        if counts["projects"] != 1:
            db.rollback()
            raise ProjectNotFoundError(project_id)

        db.commit()
        db.expire_all()
        logger.info("Deleted project %s and dependents: %s", project_id, counts)
        return counts
    except Exception:
        # Any failure (SQL Server error, simulated fault, bug) leaves nothing
        # half-deleted; the original exception is re-raised, never swallowed.
        db.rollback()
        raise
