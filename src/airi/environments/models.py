from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import Field

from airi.core.schemas import StrictSchema

ValidationStatus = Literal["passed", "passed_with_warnings", "failed", "not_verified"]


class Evidence(StrictSchema):
    evidence_type: str
    source: Literal["spark_test", "operator_attestation", "python_sqlite"]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    summary: str = Field(max_length=1000)
    status: ValidationStatus


class PartitionAdvisory(StrictSchema):
    partition_field: Literal["dt"] = "dt"
    available: bool = False
    type: str | None = None
    format: str | None = None
    is_partition: bool | None = None
    relation_to_business_time: Literal["unknown"] = "unknown"
    recommendation: Literal["manual_review_required"] = "manual_review_required"


class SourceSchema(StrictSchema):
    decimal_precision: int | None = None
    decimal_scale: int | None = None
    database: str
    table: str
    columns: dict[str, str]
    partition: PartitionAdvisory


class SemanticParityReport(StrictSchema):
    result_types: dict[str, str] = Field(default_factory=dict)
    metric_name: str
    status: ValidationStatus
    sqlite_vs_python: bool | None = None
    spark_vs_python: bool | None = None
    spark_vs_sqlite: bool | None = None
    differences: list[str] = Field(default_factory=list)


class EnvironmentStatus(StrictSchema):
    environment: Literal["spark_test", "spark_production", "production"] = "spark_test"
    reachable: bool
    engine_version: str | None = None
    session_timezone: str | None = None
    database: str
    read_only_expected: Literal[True] = True


class EnvironmentValidationReport(StrictSchema):
    validation_run_id: str = Field(default_factory=lambda: str(uuid4()))
    environment: Literal["spark_test", "spark_production", "production"] = "spark_test"
    profile_version: Literal["1.0.0"] = "1.0.0"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    overall: ValidationStatus
    engine_version: str | None = None
    session_timezone: str | None = None
    connectivity: ValidationStatus
    capabilities: dict[str, ValidationStatus] = Field(default_factory=dict)
    source_schema: SourceSchema | None = None
    fixture_parity: list[SemanticParityReport] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    requires_human_review: Literal[True] = True
