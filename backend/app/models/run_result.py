from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunResult(Base):

    __tablename__ = "run_results"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    run_id: Mapped[int] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    exit_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    total_tests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    passed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    skipped_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    stdout: Mapped[str | None] = mapped_column(
        NVARCHAR(None),  # NVARCHAR(max) on SQL Server
        nullable=True,
    )

    stderr: Mapped[str | None] = mapped_column(
        NVARCHAR(None),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
