from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=2,
        max_length=150,
    )

    frequency: str = Field(
        ...,
        min_length=1,
        max_length=50,
    )

    browser: str = Field(
        ...,
        min_length=1,
        max_length=30,
    )


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(
        None,
        min_length=2,
        max_length=150,
    )

    frequency: str | None = Field(
        None,
        min_length=1,
        max_length=50,
    )

    browser: str | None = Field(
        None,
        min_length=1,
        max_length=30,
    )


class ProjectResponse(BaseModel):
    id: int
    name: str
    frequency: str
    browser: str
    owner_id: int
    is_active: bool

    class Config:
        from_attributes = True