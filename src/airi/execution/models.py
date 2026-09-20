from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from airi.core.execution import ExecutionContext
from airi.core.schemas import StrictSchema

FailureCategory = Literal[
    "syntax_error",
    "schema_error",
    "data_type_error",
    "permission_error",
    "timeout",
    "resource_limit",
    "empty_result",
    "duplicate_entity",
    "window_mismatch",
    "boundary_mismatch",
    "reconciliation_mismatch",
    "execution_error",
    "unknown",
    "connection_error",
    "metadata_error",
    "unsupported_source_schema",
    "timezone_mismatch",
    "dialect_mismatch",
    "decimal_mismatch",
    "fixture_mismatch",
    "remote_cancel_unverified",
]


AllowedDatabase = Literal["c_db", "tmp_db", "demo"]


class ExecutionProfile(StrictSchema):
    name: Literal["spark_test"] = "spark_test"
    version: Literal["1.0.0"] = "1.0.0"
    engine: Literal["spark_sql"] = "spark_sql"
    environment: Literal["test"] = "test"
    read_only: Literal[True] = True
    # `c_db` is the invoice test source, `tmp_db` the fixture-mapping target and
    # `demo` the synthetic relationship source. The metric's own declaration still
    # pins the exact table; this profile only bounds the search space.
    allowed_databases: tuple[AllowedDatabase, ...] = ("c_db", "tmp_db", "demo")
    blocked_databases: tuple[Literal["prod_sensitive_db"]] = ("prod_sensitive_db",)
    max_rows: int = Field(default=1000, ge=1, le=1000)
    default_rows: int = Field(default=100, ge=1, le=100)
    max_seconds: int = Field(default=60, ge=1, le=60)


class ExecutionRequest(StrictSchema):
    artifact_id: str = Field(min_length=1, max_length=36)
    approval_id: str = Field(min_length=1, max_length=36)
    execution_profile: Literal["spark_test"] = "spark_test"
    max_rows: int = Field(default=100, ge=1, le=1000)
    timeout_seconds: int = Field(default=60, ge=1, le=60)


class ExecutionPlan(ExecutionRequest):
    execution_run_id: str = Field(default_factory=lambda: str(uuid4()))
    workflow_run_id: str
    request_id: str
    profile_version: Literal["1.0.0"] = "1.0.0"
    engine: Literal["spark_sql"] = "spark_sql"
    execution_context: ExecutionContext
    sql_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResultColumn(StrictSchema):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=128)


Cell = str | int | float | Decimal | None


class QueryOutput(StrictSchema):
    provider_query_id: str | None = None
    cancel_requested: bool | None = None
    remote_cancel_status: Literal["not_verified", "cancelled", "finished"] = "not_verified"
    executed_sql_hash: str | None = None
    source_mapping: str | None = None
    columns: list[ResultColumn] = Field(max_length=16)
    rows: list[dict[str, Cell]] = Field(max_length=1001)
    truncated: bool = False

    @model_validator(mode="after")
    def bounded_cells(self) -> Self:
        if any(
            len(row) > 16 or any(isinstance(v, str) and len(v) > 4096 for v in row.values())
            for row in self.rows
        ):
            raise ValueError("Result exceeds cell limits")
        return self


class ExecutionRun(ExecutionPlan):
    status: Literal["success", "failed", "timeout"]
    started_at: datetime
    finished_at: datetime
    duration_ms: float = Field(ge=0)
    failure_category: FailureCategory | None = None
    error_message: str | None = None


class ExecutionResult(ExecutionRun):
    provider_query_id: str | None = None
    cancel_requested: bool | None = None
    remote_cancel_status: Literal["not_verified", "cancelled", "finished"] = "not_verified"
    executed_sql_hash: str | None = None
    source_mapping: str | None = None
    columns: list[ResultColumn] = Field(default_factory=list)
    rows: list[dict[str, Cell]] = Field(default_factory=list, max_length=1000)
    row_count: int = Field(ge=0, le=1000)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.row_count != len(self.rows):
            raise ValueError("row_count is the number of returned rows")
        if (self.status == "success") != (self.failure_category is None):
            raise ValueError("Failure classification must match execution status")
        return self
