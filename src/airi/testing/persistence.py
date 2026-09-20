from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from airi.infrastructure.database import Base


class MetricTestRunRow(Base):
    __tablename__ = "metric_test_runs"
    test_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_run_id: Mapped[str] = mapped_column(ForeignKey("execution_runs.execution_run_id"))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("development_artifacts.artifact_id"))
    status: Mapped[str] = mapped_column(String(32))
    total: Mapped[int]
    passed: Mapped[int]
    warnings: Mapped[int]
    failed: Mapped[int]
    skipped: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime] = mapped_column(DateTime)
    report_json: Mapped[dict] = mapped_column(JSON)


class MetricTestResultRow(Base):
    __tablename__ = "metric_test_results"
    test_case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    test_run_id: Mapped[str] = mapped_column(ForeignKey("metric_test_runs.test_run_id"))
    test_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    failure_category: Mapped[str | None] = mapped_column(String(32))
    expected_summary: Mapped[str] = mapped_column(String(1000))
    actual_summary: Mapped[str] = mapped_column(String(1000))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime)
