from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Screenshot(Base):
    """A single PNG captured by the Selenium compatibility layer, linked to
    the TestResult it belongs to and, when matched, the specific StepResult
    event that produced it."""

    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    test_result_id: Mapped[int] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # NO explicit ondelete here (defaults to NO ACTION): SQL Server refuses
    # a second CASCADE path to the same ancestor (runs -> test_results ->
    # screenshots AND runs -> test_results -> step_results -> screenshots).
    # test_result_id's CASCADE already guarantees screenshots are removed
    # when their TestResult (and therefore Run) is deleted.
    step_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("step_results.id"),
        nullable=True,
        index=True,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    mime_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="image/png",
    )

    # STEP_FAILURE | TEST_FAILURE | ALL_MODE
    capture_reason: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    content: Mapped[bytes] = mapped_column(
        LargeBinary,  # VARBINARY(MAX) on SQL Server
        nullable=False,
    )

    size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
