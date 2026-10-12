from datetime import datetime

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


class EvidenceItemSchema(BaseModel):
    label: str
    value: str


class TimelineStepSchema(BaseModel):
    role: str  # failed | last_successful | preceding
    sequence_index: int
    step_number: int | None
    action: str
    target: str | None
    status: str
    duration_seconds: float | None
    occurred_at: datetime | None
    error: str | None
    screenshot_ids: list[int]

    class Config:
        from_attributes = True


class FailureAnalysisSchema(BaseModel):
    """Rule-based explanation of a failed test (see failure_analysis_service).
    `evidence` holds recorded facts only; `likely_cause` and
    `recommended_actions` are hedged hypotheses/suggestions, never confirmed
    root causes."""

    failure_category: str
    failure_title: str
    summary: str
    likely_cause: str
    confidence: str  # high | medium | low (strength of the category evidence)
    confidence_note: str
    category_basis: str
    evidence: list[EvidenceItemSchema]
    recommended_actions: list[str]
    test_status: str
    exception_type: str | None
    exception_message: str | None
    file_name: str | None
    line_number: int | None
    source_line: str | None
    failed_step: TimelineStepSchema | None
    last_successful_step: TimelineStepSchema | None
    timeline: list[TimelineStepSchema]
    page_url: str | None
    page_url_source: str | None
    timestamp: datetime | None
    browser: str | None
    duration_seconds: float | None
    traceback: str | None
    failure_screenshot: ScreenshotRefSchema | None = None

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
    analysis: FailureAnalysisSchema | None = None

    class Config:
        from_attributes = True


class RunIssueSchema(BaseModel):
    """Why a finished run has no test results (derived from pytest's real exit code and
    output; never a test result)."""
    code: str
    title: str
    detail: str
    hints: list[str] = []
    exit_code: int | None = None
    output_tail: str | None = None


class RunResultsResponse(BaseModel):
    run: RunResponse
    summary: RunResultsSummarySchema | None
    tests: list[TestResultSchema] | None
    issue: RunIssueSchema | None = None

    class Config:
        from_attributes = True
