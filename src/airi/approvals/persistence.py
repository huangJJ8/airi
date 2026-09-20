from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class DevelopmentArtifactRow(Base):
    __tablename__ = "development_artifacts"
    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), unique=True)
    artifact_version: Mapped[str] = mapped_column(String(32))
    metric_name: Mapped[str] = mapped_column(String(64), index=True)
    metric_ir_hash: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ApprovalRecordRow(Base):
    __tablename__ = "approval_records"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_approval_artifact"),
        Index("ix_approval_decision_created", "decision", "created_at"),
    )
    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(String(36), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("development_artifacts.artifact_id"))
    artifact_version: Mapped[str] = mapped_column(String(32))
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_ir_hash: Mapped[str] = mapped_column(String(64))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": revision}
