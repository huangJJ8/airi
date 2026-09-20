from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class DatasetSnapshotRow(Base):
    __tablename__ = "dataset_snapshots"
    dataset_snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class LabelDefinitionRow(Base):
    __tablename__ = "label_definitions"
    label_definition_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ExperimentSpecRow(Base):
    __tablename__ = "experiment_specs"
    experiment_spec_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("development_artifacts.artifact_id"))
    dataset_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_snapshots.dataset_snapshot_id")
    )
    label_definition_id: Mapped[str] = mapped_column(
        ForeignKey("label_definitions.label_definition_id")
    )
    anchor_time: Mapped[datetime] = mapped_column(DateTime)
    spec_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ExperimentRunRow(Base):
    __tablename__ = "experiment_runs"
    experiment_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    experiment_spec_id: Mapped[str] = mapped_column(
        ForeignKey("experiment_specs.experiment_spec_id")
    )
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    run_json: Mapped[dict] = mapped_column(JSON)


class ExperimentEvaluationRow(Base):
    __tablename__ = "experiment_evaluations"
    experiment_run_id: Mapped[str] = mapped_column(
        ForeignKey("experiment_runs.experiment_run_id"), primary_key=True
    )
    metric_name: Mapped[str] = mapped_column(String(64))
    coverage: Mapped[float | None] = mapped_column(Float)
    bad_rate: Mapped[float | None] = mapped_column(Float)
    ks: Mapped[float | None] = mapped_column(Float)
    iv: Mapped[float | None] = mapped_column(Float)
    risk_direction: Mapped[str] = mapped_column(String(32))
    report_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
