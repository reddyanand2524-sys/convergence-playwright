from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    # QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED
    # Plain string column (same approach as Project.frequency/browser) —
    # the execution engine (future phase) is what will ever change this
    # after creation. Phase 3 only ever writes "QUEUED".
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="QUEUED",
    )

    # Snapshot of the project's browser at the moment the Run was created.
    # Deliberately NOT a foreign key / live read of Project.browser — a
    # Run must keep showing the browser it was actually created with, even
    # if the project's configuration changes afterward.
    browser: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
    )
