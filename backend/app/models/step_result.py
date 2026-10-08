from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StepResult(Base):
    """One row per Selenium IDE step event recorded by the compatibility
    layer's steps.json, belonging to a single TestResult."""

    __tablename__ = "step_results"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    test_result_id: Mapped[int] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    step_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    target: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    value: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    # PASS | FAIL — deliberately distinct vocabulary from
    # TestResult.status (PASSED/FAILED).
    status: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(
        NVARCHAR(None),
        nullable=True,
    )

    # 0-based ordinal position among this test's own events, in steps.json
    # order. NOT the same as step_number (the Selenium IDE step number).
    sequence_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
