from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.test_result import TestResult
from app.models.user import User
from app.schemas.project_file import (
    ProjectFileContentResponse,
    ProjectFileResponse,
    UpdateProjectFileRequest,
)


router = APIRouter(
    prefix="/api/projects",
    tags=["Project Files"],
)


def _get_owned_project(
    project_id: int,
    db: Session,
    current_user: User,
) -> Project:
    """Shared ownership check: a project only "exists" for its owner."""

    project = db.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.owner_id == current_user.id,
            Project.is_active == True,
        )
    )

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project


def _get_owned_file(
    project: Project,
    file_id: int,
    db: Session,
) -> ProjectFile:
    project_file = db.scalar(
        select(ProjectFile).where(
            ProjectFile.id == file_id,
            ProjectFile.project_id == project.id,
        )
    )

    if not project_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    return project_file


@router.post(
    "/{project_id}/files",
    status_code=status.HTTP_201_CREATED,
)
async def upload_project_files(
    project_id: int,
    files: Annotated[
        list[UploadFile],
        File(description="Select one or more Python files"),
    ],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)

    saved_files = []

    for uploaded_file in files:
        if not uploaded_file.filename:
            continue

        if not uploaded_file.filename.lower().endswith(".py"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only Python files are allowed: "
                    f"{uploaded_file.filename}"
                ),
            )

        raw_bytes = await uploaded_file.read()

        try:
            file_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"File must be valid UTF-8 text: "
                    f"{uploaded_file.filename}"
                ),
            )

        # Logical label only (NOT a real filesystem path). The database
        # is the source of truth: the file's actual source code lives in
        # `file_content` below, not on disk.
        logical_path = f"projects/{project_id}/{uploaded_file.filename}"

        project_file = ProjectFile(
            project_id=project.id,
            file_name=uploaded_file.filename,
            file_path=logical_path,
            file_content=file_content,
        )

        db.add(project_file)

        saved_files.append(
            {
                "file_name": uploaded_file.filename,
                "file_path": logical_path,
            }
        )

    db.commit()

    return {
        "message": "Project files uploaded successfully",
        "project_id": project.id,
        "files": saved_files,
    }


@router.get(
    "/{project_id}/files",
    response_model=list[ProjectFileResponse],
)
def get_project_files(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)

    files = db.scalars(
        select(ProjectFile)
        .where(ProjectFile.project_id == project.id)
        .order_by(ProjectFile.created_at.desc())
    ).all()

    return files


@router.get(
    "/{project_id}/files/{file_id}",
    response_model=ProjectFileContentResponse,
)
def get_project_file_content(
    project_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    return _get_owned_file(project, file_id, db)


@router.patch(
    "/{project_id}/files/{file_id}",
    response_model=ProjectFileContentResponse,
)
def update_project_file_content(
    project_id: int,
    file_id: int,
    request: UpdateProjectFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    project_file = _get_owned_file(project, file_id, db)

    # Saving code only ever saves code — this endpoint never executes,
    # imports, or evaluates the submitted content in any way.
    project_file.file_content = request.file_content

    db.commit()
    db.refresh(project_file)

    return project_file


@router.delete(
    "/{project_id}/files/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project_file(
    project_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = _get_owned_project(project_id, db, current_user)
    project_file = _get_owned_file(project, file_id, db)

    # Only this one ProjectFile row is removed - the Project, its other
    # files and every Run record are left untouched.
    #
    # Past executions DO reference the file row: test_results.project_file_id
    # is a foreign key to project_files.id. Deleting a file that has ever been
    # run therefore failed with a SQL Server FK violation (HTTP 500). The
    # column is nullable, result persistence already stores NULL when no file
    # matches, and each test_result keeps its own file_name/nodeid, so only the
    # link to this file row is cleared (in the same transaction); no result,
    # step, screenshot or diagnostic is deleted.
    db.execute(
        update(TestResult)
        .where(TestResult.project_file_id == project_file.id)
        .values(project_file_id=None)
    )
    db.delete(project_file)
    db.commit()