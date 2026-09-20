from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class ProposalDecisionRow(Base):
    __tablename__ = "proposal_decisions"
    __table_args__ = (UniqueConstraint("reflection_run_id", "proposal_id"),)
    decision_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reflection_run_id: Mapped[str] = mapped_column(ForeignKey("reflection_runs.reflection_run_id"))
    proposal_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(40))
    proposal_hash: Mapped[str] = mapped_column(String(64))
    decision_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RefinementRunRow(Base):
    __tablename__ = "refinement_runs"
    refinement_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reflection_run_id: Mapped[str] = mapped_column(ForeignKey("reflection_runs.reflection_run_id"))
    decision_id: Mapped[str] = mapped_column(ForeignKey("proposal_decisions.decision_id"))
    proposal_id: Mapped[str] = mapped_column(String(64))
    baseline_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("development_artifacts.artifact_id")
    )
    baseline_experiment_run_id: Mapped[str] = mapped_column(
        ForeignKey("experiment_runs.experiment_run_id")
    )
    candidate_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("development_artifacts.artifact_id")
    )
    status: Mapped[str] = mapped_column(String(32))
    final_decision_recorded: Mapped[bool] = mapped_column(default=False)
    outcome: Mapped[str | None] = mapped_column(String(32))
    candidate_test_run_id: Mapped[str | None] = mapped_column(String(36))
    candidate_experiment_run_id: Mapped[str | None] = mapped_column(String(36))
    comparison_policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class RefinementReportRow(Base):
    __tablename__ = "refinement_reports"
    refinement_run_id: Mapped[str] = mapped_column(
        ForeignKey("refinement_runs.refinement_run_id"), primary_key=True
    )
    report_json: Mapped[dict] = mapped_column(JSON)
    outcome: Mapped[str | None] = mapped_column(String(32))
    coverage_delta: Mapped[float | None] = mapped_column(Float)
    ks_delta: Mapped[float | None] = mapped_column(Float)
    iv_delta: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime)
