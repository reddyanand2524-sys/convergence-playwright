from datetime import datetime

from pydantic import BaseModel


class RunResponse(BaseModel):
    id: int
    project_id: int
    status: str
    browser: str
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    class Config:
        from_attributes = True


class RunHistoryItem(BaseModel):
    

    id: int
    project_id: int
    status: str
    browser: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    test_count: int
    passed_count: int
    failed_count: int
    skipped_count: int
    screenshot_count: int

    class Config:
        from_attributes = True


class RunHistoryResponse(BaseModel):
    items: list[RunHistoryItem]
    page: int
    page_size: int
    total: int
    total_pages: int
