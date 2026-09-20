from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class MetricDefinitionRow(Base):
    __tablename__ = "metric_definitions"
    metric_definition_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(2000))
    scenario: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    member_metric_names: Mapped[list] = mapped_column(JSON)
    active_version_id: Mapped[str | None] = mapped_column(String(36))
    definition_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class MetricVersionRow(Base):
    __tablename__ = "metric_versions"
    __table_args__ = (UniqueConstraint("metric_definition_id", "version"),)
    metric_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(
        ForeignKey("metric_definitions.metric_definition_id")
    )
    version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_ir_hash: Mapped[str] = mapped_column(String(64))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("development_artifacts.artifact_id"))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    source_promotion_review_id: Mapped[str] = mapped_column(
        ForeignKey("promotion_reviews.promotion_review_id"), unique=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    version_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class MetricReleaseRow(Base):
    __tablename__ = "metric_releases"
    release_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(
        ForeignKey("metric_definitions.metric_definition_id")
    )
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_versions.metric_version_id"))
    version: Mapped[str] = mapped_column(String(32))
    target_environment: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    expected_active_version_id: Mapped[str | None] = mapped_column(String(36))
    release_policy_version: Mapped[str] = mapped_column(String(32))
    release_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)


class ReleaseValidationRow(Base):
    __tablename__ = "release_validation_results"
    release_id: Mapped[str] = mapped_column(
        ForeignKey("metric_releases.release_id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32))
    release_eligibility: Mapped[str] = mapped_column(String(32))
    report_json: Mapped[dict] = mapped_column(JSON)
    report_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ReleaseReviewRow(Base):
    __tablename__ = "release_reviews"
    release_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    release_id: Mapped[str] = mapped_column(ForeignKey("metric_releases.release_id"), unique=True)
    metric_version_id: Mapped[str] = mapped_column(ForeignKey("metric_versions.metric_version_id"))
    decision: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    comment: Mapped[str | None] = mapped_column(String(2000))
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class RollbackReviewRow(Base):
    __tablename__ = "rollback_reviews"
    rollback_review_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(
        ForeignKey("metric_definitions.metric_definition_id")
    )
    from_version_id: Mapped[str | None] = mapped_column(String(36))
    to_version_id: Mapped[str] = mapped_column(String(36))
    decision: Mapped[str] = mapped_column(String(32))
    reviewer: Mapped[str | None] = mapped_column(String(2000))
    comment: Mapped[str | None] = mapped_column(String(2000))
    review_json: Mapped[dict] = mapped_column(JSON)
    review_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MetricReleaseEventRow(Base):
    __tablename__ = "metric_release_events"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_definition_id: Mapped[str] = mapped_column(String(36))
    metric_version_id: Mapped[str | None] = mapped_column(String(36))
    release_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(2000))
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
