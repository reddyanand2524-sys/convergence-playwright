from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FailureDiagnostic(Base):

    __tablename__ = "failure_diagnostics"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        autoincrement=True,
    )

    test_result_id: Mapped[int] = mapped_column(
        ForeignKey("test_results.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    exception_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    exception_message: Mapped[str | None] = mapped_column(
        NVARCHAR(None),
        nullable=True,
    )

    source_file: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    source_line_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    source_line: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
    )

    raw_traceback: Mapped[str | None] = mapped_column(
        NVARCHAR(None),
        nullable=True,
    )
