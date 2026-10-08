from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProjectFile(Base):
    __tablename__ = "project_files"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # NOTE: This is now a *logical* label only (e.g. "projects/3/test_x.py"),
    # kept so existing API responses keep the same shape. No real file is
    # written to disk at this path anymore — the database is the source of
    # truth. See `file_content` below.
    file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    # The actual uploaded Python source code. This is what the test
    # execution step (future work) will read to build a temporary runtime
    # file for Pytest/Selenium — it does NOT read from disk.
    file_content: Mapped[str] = mapped_column(
        NVARCHAR(None),  # renders as NVARCHAR(max) on SQL Server
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )