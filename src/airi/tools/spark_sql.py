from datetime import timedelta
from typing import Literal

from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import Field, model_validator

from airi.core.execution import SHANGHAI, ExecutionContext
from airi.core.schemas import Identifier, StrictSchema, VersionedReference
from airi.metric_ir.invoice import validate_invoice_metric
from airi.metric_ir.models import MetricIR
from airi.tools.base import Tool, ToolSpec

TECHNICAL_WARNINGS = [
    "dt 的格式及与 invoice_date 的关系未确认；草稿未添加分区过滤，可能全表扫描。",
    "需确认 invoice_amt 为数值类型、invoice_date 为 DATE；若为 TIMESTAMP，"
    "需确认 Spark 会话时区为 Asia/Shanghai；字符串日期需另行确认格式。",
]


class SparkSQLInput(StrictSchema):
    metric_code: Identifier
    entity_field: Identifier
    source_catalog: Identifier | None = None
    source_database: Identifier
    source_table: Identifier
    aggregation: Literal["sum", "count"]
    value_field: Identifier | None
    time_field: Identifier
    partition_field: Identifier
    window_size: int = Field(ge=30, le=30)
    window_unit: Literal["day"]
    execution_context: ExecutionContext

    @model_validator(mode="after")
    def aggregation_field(self):
        if (self.aggregation == "sum" and self.value_field is None) or (
            self.aggregation == "count" and self.value_field is not None
        ):
            raise ValueError("SUM requires a field; COUNT must count rows")
        return self


class SQLDraft(StrictSchema):
    type: Literal["sql"] = "sql"
    language: Literal["spark_sql"] = "spark_sql"
    status: Literal["draft"] = "draft"
    code: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    template: VersionedReference


def metric_to_tool_input(metric: MetricIR, context: ExecutionContext) -> SparkSQLInput:
    metric = validate_invoice_metric(metric)
    return SparkSQLInput(
        metric_code=metric.name,
        entity_field=metric.entity_key,
        source_catalog=metric.source.catalog,
        source_database=metric.source.database,
        source_table=metric.source.table,
        aggregation=metric.aggregation.function,
        value_field=metric.aggregation.field,
        time_field=metric.window.time_field,
        partition_field=metric.partition_field,
        window_size=metric.window.size,
        window_unit=metric.window.unit,
        execution_context=context,
    )


class GenerateSparkSQLMetric(Tool[SparkSQLInput, SQLDraft]):
    spec = ToolSpec(
        name="generate_spark_sql_metric",
        version="1.1.0",
        description="Render a deterministic SUM or COUNT(*) metric Spark SQL draft",
    )
    input_model = SparkSQLInput
    output_model = SQLDraft

    def __init__(self):
        environment = Environment(
            loader=PackageLoader("airi.tools", "templates"),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self.template = environment.get_template("spark/aggregation_metric.sql.j2")

    def execute(self, payload: SparkSQLInput) -> SQLDraft:
        end = payload.execution_context.anchor_time.astimezone(SHANGHAI).date()
        start = end - timedelta(days=payload.window_size)
        code = self.template.render(
            **payload.model_dump(exclude={"execution_context"}),
            window_start=start.isoformat(),
            window_end=end.isoformat(),
        )
        return SQLDraft(
            code=code,
            warnings=list(TECHNICAL_WARNINGS),
            template=VersionedReference(name="aggregation_metric", version="1.1.0"),
        )
