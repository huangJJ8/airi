from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class ExecutionRunRow(Base):
    __tablename__ = "execution_runs"
    execution_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("development_artifacts.artifact_id"))
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_records.approval_id"))
    execution_profile: Mapped[str] = mapped_column(String(32))
    engine: Mapped[str] = mapped_column(String(32))
    sql_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(default=False)
    failure_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    metadata_json: Mapped[dict] = mapped_column(JSON)
