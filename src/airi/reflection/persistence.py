from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class ReflectionRunRow(Base):
    __tablename__ = "reflection_runs"
    reflection_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reflection_policy_version: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(256))
    input_evidence_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReflectionReportRow(Base):
    __tablename__ = "reflection_reports"
    reflection_run_id: Mapped[str] = mapped_column(
        ForeignKey("reflection_runs.reflection_run_id"), primary_key=True
    )
    report_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    proposal_count: Mapped[int] = mapped_column(Integer)
    requires_human_review: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime)
