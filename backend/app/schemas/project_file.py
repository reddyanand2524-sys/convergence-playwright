from datetime import datetime

from pydantic import BaseModel, Field


class UpdateProjectFileRequest(BaseModel):
    """Only file_content may be updated — no path, no project_id, no
    other field is accepted here."""

    file_content: str = Field(
        ...,
        min_length=1,
    )


class ProjectFileResponse(BaseModel):
    """Metadata only — does NOT include file_content."""

    id: int
    project_id: int
    file_name: str
    file_path: str
    created_at: datetime

    class Config:
        from_attributes = True


class ProjectFileContentResponse(BaseModel):
    """Full file record, including its source code from the database."""

    id: int
    project_id: int
    file_name: str
    file_path: str
    file_content: str
    created_at: datetime

    class Config:
        from_attributes = True
