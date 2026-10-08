from pydantic import BaseModel

from app.schemas.run import RunResponse


class RunResultsSummarySchema(BaseModel):
    exit_code: int | None
    total_tests: int
    passed_count: int
    failed_count: int
    skipped_count: int
    duration_seconds: float

    class Config:
        from_attributes = True


class ScreenshotRefSchema(BaseModel):
    id: int
    file_name: str
    mime_type: str
    capture_reason: str
    url: str

    class Config:
        from_attributes = True


class StepResultSchema(BaseModel):
    id: int
    sequence_index: int
    step_number: int | None
    action: str
    target: str | None
    value: str | None
    status: str
    duration_seconds: float | None
    error: str | None
    screenshots: list[ScreenshotRefSchema]

    class Config:
        from_attributes = True


class FailureDiagnosticSchema(BaseModel):
    exception_type: str | None
    exception_message: str | None
    source_file: str | None
    source_line_number: int | None
    source_line: str | None
    raw_traceback: str | None

    class Config:
        from_attributes = True


class TestResultSchema(BaseModel):
    id: int
    nodeid: str
    file_name: str
    class_name: str | None
    method_name: str
    status: str
    duration_seconds: float | None
    steps: list[StepResultSchema]
    failure: FailureDiagnosticSchema | None
    screenshots: list[ScreenshotRefSchema] = []

    class Config:
        from_attributes = True


class RunResultsResponse(BaseModel):
    run: RunResponse
    summary: RunResultsSummarySchema | None
    tests: list[TestResultSchema] | None

    class Config:
        from_attributes = True
