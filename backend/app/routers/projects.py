import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.project import Project
from app.models.user import User
from app.services import project_deletion_service
from app.schemas.project import (
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/projects",
    tags=["Projects"],
)


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    request: CreateProjectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = Project(
        name=request.name.strip(),
        frequency=request.frequency.strip(),
        browser=request.browser.strip(),
        owner_id=current_user.id,
        is_active=True,
    )

    db.add(project)
    db.commit()
    db.refresh(project)

    return project


@router.get(
    "",
    response_model=list[ProjectResponse],
)
def get_my_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = db.scalars(
        select(Project)
        .where(
            Project.owner_id == current_user.id,
            Project.is_active == True,  # noqa: E712
        )
        .order_by(Project.created_at.desc())
    ).all()

    return projects


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


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_project(project_id, db, current_user)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
)
def update_project(
    project_id: int,
    request: UpdateProjectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)

    updates = request.model_dump(exclude_unset=True)

    if "name" in updates:
        project.name = updates["name"].strip()

    if "frequency" in updates:
        project.frequency = updates["frequency"].strip()

    if "browser" in updates:
        project.browser = updates["browser"].strip()

    db.commit()
    db.refresh(project)

    return project

@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently deletes the caller's project and everything it owns (files,
    runs, results, screenshots, diagnostics, schedules). 404 if the project does
    not exist or belongs to someone else; 409 if a run is currently RUNNING.
    See app/services/project_deletion_service.py for the coordination rules."""
    project = _get_owned_project(project_id, db, current_user)

    try:
        project_deletion_service.delete_project(db, project)
    except project_deletion_service.ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    except project_deletion_service.ActiveRunsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This project has a run in progress. Cancel it and wait for it to "
                "stop before deleting the project."
            ),
        ) from exc
    except IntegrityError as exc:
        # A foreign-key conflict means rows appeared while deleting (e.g. a
        # scheduler tick or a retry created a run). Nothing was deleted.
        db.rollback()
        logger.exception("Project %s deletion hit a foreign-key conflict", project_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The project changed while it was being deleted, so nothing was "
                "deleted. Please try again."
            ),
        ) from exc
    except SQLAlchemyError:
        # Anything else is a real server error: undo, keep the SQL Server error
        # in the log, and let it surface as a 500 (never report success).
        db.rollback()
        logger.exception("Project %s deletion failed", project_id)
        raise
