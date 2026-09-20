"""Constrained join SQL generation.

One tool family (``generate_spark_sql_metric``) gains a new pinned version that
renders a declarative join path. Nothing scenario-specific exists here: the tool
receives a base source, an ordered list of equality joins and a list of
cross-column exclusions, and renders them with a fixed template. The business
path (which tables, which keys, which exclusion) comes from the Metric IR, which
comes from the scenario skill.
"""

from typing import Literal

from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import Field

from airi.core.schemas import Identifier, StrictSchema, VersionedReference
from airi.metric_ir.enterprise_relation import validate_enterprise_relation_metric
from airi.metric_ir.models import MetricIR
from airi.tools.spark_sql import GenerateSparkSQLMetric, SQLDraft

TECHNICAL_WARNINGS = [
    "关系表按关系记录存储：同一自然人重复关系记录由 COUNT DISTINCT 去重，"
    "但自然人身份合并（重名、证件变更）不属于本指标口径。",
    "未做关系类型筛选，所有已声明关系类型都会计入关联企业数。",
    "关系数据缺失会使关联企业数偏低；这是数据覆盖问题，不是口径问题。",
    "需确认两表 person_id 类型与取值口径一致，且关联企业标识与主体企业标识同源可比。",
]


class EqualityPair(StrictSchema):
    left_alias: Identifier
    left_field: Identifier
    right_alias: Identifier
    right_field: Identifier


class InequalityPair(EqualityPair):
    """A cross-column exclusion: the left value must differ from the right value."""


class JoinInput(StrictSchema):
    alias: Identifier
    join_type: Literal["inner", "left"]
    source_catalog: Identifier | None = None
    source_database: Identifier
    source_table: Identifier
    conditions: list[EqualityPair] = Field(min_length=1, max_length=4)


class JoinMetricSQLInput(StrictSchema):
    metric_code: Identifier
    entity_field: Identifier
    base_alias: Identifier
    source_catalog: Identifier | None = None
    source_database: Identifier
    source_table: Identifier
    aggregation: Literal["count_distinct"]
    value_field: Identifier
    value_alias: Identifier
    joins: list[JoinInput] = Field(min_length=1, max_length=2)
    exclusions: list[InequalityPair] = Field(min_length=1, max_length=4)


def metric_to_join_input(metric: MetricIR) -> JoinMetricSQLInput:
    """Map a validated join Metric IR onto the tool's input. No SQL text here."""
    metric = validate_enterprise_relation_metric(metric)
    assert metric.source_alias and metric.aggregation_alias and metric.aggregation.field
    return JoinMetricSQLInput(
        metric_code=metric.name,
        entity_field=metric.entity_key,
        base_alias=metric.source_alias,
        source_catalog=metric.source.catalog,
        source_database=metric.source.database,
        source_table=metric.source.table,
        aggregation="count_distinct",
        value_field=metric.aggregation.field,
        value_alias=metric.aggregation_alias,
        joins=[
            JoinInput(
                alias=join.alias,
                join_type=join.join_type,
                source_catalog=join.source.catalog,
                source_database=join.source.database,
                source_table=join.source.table,
                conditions=[
                    EqualityPair(
                        left_alias=condition.left.alias,
                        left_field=condition.left.field,
                        right_alias=condition.right.alias,
                        right_field=condition.right.field,
                    )
                    for condition in join.conditions
                ],
            )
            for join in metric.joins
        ],
        exclusions=[
            InequalityPair(
                left_alias=filter_.left.alias,
                left_field=filter_.left.field,
                right_alias=filter_.right.alias,
                right_field=filter_.right.field,
            )
            for filter_ in metric.column_filters
        ],
    )


class GenerateJoinMetricSQL(GenerateSparkSQLMetric):
    """Same tool name, new pinned version: adds the declarative join path."""

    spec = GenerateSparkSQLMetric.spec.model_copy(
        update={
            "version": "1.3.0",
            "description": (
                "Render a deterministic COUNT DISTINCT entity metric over constrained joins"
            ),
        }
    )
    input_model = JoinMetricSQLInput
    output_model = SQLDraft

    def __init__(self) -> None:
        environment = Environment(
            loader=PackageLoader("airi.tools", "templates"),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self.template = environment.get_template("spark/join_metric.sql.j2")

    def execute(self, payload: JoinMetricSQLInput) -> SQLDraft:
        code = self.template.render(**payload.model_dump())
        return SQLDraft(
            code=code,
            warnings=list(TECHNICAL_WARNINGS),
            template=VersionedReference(name="join_metric", version="1.0.0"),
        )
