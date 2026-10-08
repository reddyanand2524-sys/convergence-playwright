from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TestResult(Base):
    """One row per pytest testcase found in a Run's JUnit report."""

    __tablename__ = "test_results"
    __table_args__ = (
        Index("ix_test_results_run_id_nodeid", "run_id", "nodeid", unique=True),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    project_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_files.id"),
        nullable=True,
        index=True,
    )

    nodeid: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    class_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    method_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # PASSED | FAILED | SKIPPED | ERROR
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
