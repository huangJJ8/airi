from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class TemporalSeriesRow(Base):
    __tablename__ = "temporal_dataset_series"
    series_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(2000))
    reference_slice_id: Mapped[str] = mapped_column(String(36))
    oot_slice_id: Mapped[str] = mapped_column(String(36))
    series_json: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class TemporalRunRow(Base):
    __tablename__ = "temporal_validation_runs"
    temporal_validation_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_refinement_run_id: Mapped[str] = mapped_column(
        ForeignKey("refinement_runs.refinement_run_id"), unique=True
    )
    baseline_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("development_artifacts.artifact_id")
    )
    candidate_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("development_artifacts.artifact_id")
    )
    series_id: Mapped[str] = mapped_column(ForeignKey("temporal_dataset_series.series_id"))
    status: Mapped[str] = mapped_column(String(32))
    stability_policy_version: Mapped[str] = mapped_column(String(32))
    promotion_policy_version: Mapped[str] = mapped_column(String(32))
    spec_hash: Mapped[str] = mapped_column(String(64))
    spec_json: Mapped[dict] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class TemporalResultRow(Base):
    __tablename__ = "temporal_validation_results"
    temporal_validation_run_id: Mapped[str] = mapped_column(
        ForeignKey("temporal_validation_runs.temporal_validation_run_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32))
    promotion_eligibility: Mapped[str] = mapped_column(String(32))
    report_json: Mapped[dict] = mapped_column(JSON)
    report_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PromotionReviewRow(Base):
    __tablename__ = "promotion_reviews"
    promotion_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    temporal_validation_run_id: Mapped[str] = mapped_column(
        ForeignKey("temporal_validation_runs.temporal_validation_run_id"), unique=True
    )
    candidate_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("development_artifacts.artifact_id")
    )
    decision: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    comment: Mapped[str | None] = mapped_column(String(2000))
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
