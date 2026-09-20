from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class EnvironmentValidationRow(Base):
    __tablename__ = "environment_validation_runs"
    validation_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    environment: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    engine_version: Mapped[str | None] = mapped_column(String(256))
    session_timezone: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    report_json: Mapped[dict] = mapped_column(JSON)
