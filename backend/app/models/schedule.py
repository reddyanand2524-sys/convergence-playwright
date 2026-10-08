from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# Supported frequency values. Plain string column (same style as
# Project.frequency / Run.status / Run.browser) — validated at the Pydantic
# schema layer, not via a DB-level CHECK constraint, matching this
# project's established convention of app-layer validation.
FREQUENCY_ONCE = "ONCE"
FREQUENCY_INTERVAL = "INTERVAL"
FREQUENCY_HOURLY = "HOURLY"
FREQUENCY_DAILY = "DAILY"
FREQUENCY_WEEKLY = "WEEKLY"

VALID_FREQUENCIES = (
    FREQUENCY_ONCE,
    FREQUENCY_INTERVAL,
    FREQUENCY_HOURLY,
    FREQUENCY_DAILY,
    FREQUENCY_WEEKLY,
)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    # Snapshot-style browser value, same convention as Run.browser: the
    # value the scheduler will stamp onto every Run it creates for this
    # schedule, independent of the live Project.browser.
    browser: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    # ONCE | INTERVAL | HOURLY | DAILY | WEEKLY
    frequency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Only used/required for frequency == INTERVAL. Minutes. Nullable for
    # all other frequencies (rejected by the schema if supplied otherwise).
    interval_value: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
    )

    start_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    next_run_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        default=None,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    # Overlap-prevention design (see scheduler_service.py docstring for full
    # rationale): nullable FK to the most recent Run this schedule created.
    # Deliberately NO ondelete=CASCADE in either direction — deleting a
    # Schedule must never touch the Run table, and a Run is never deleted by
    # this app at all, so this column only ever needs to be read, not
    # cascaded.
    last_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id"),
        nullable=True,
        default=None,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
