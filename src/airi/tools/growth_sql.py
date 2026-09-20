from jinja2 import Environment, PackageLoader, StrictUndefined

from airi.core.execution import ExecutionContext
from airi.core.schemas import StrictSchema, VersionedReference
from airi.metric_ir.derived import DerivedMetricIR
from airi.tools.base import Tool, ToolSpec
from airi.tools.spark_sql import GenerateSparkSQLMetric, SQLDraft, metric_to_tool_input
from airi.workflows.development.derived_planning import DerivedMetricPlanner

GROWTH_WARNINGS = [
    "FULL OUTER JOIN 保留任一周期出现的企业；缺失周期金额及 NULL 聚合金额按 0 处理。",
    "previous period amount = 0 时增长率定义为 NULL，不定义为 0 或 100%。",
    "主体采用空值安全连接；两个周期的 NULL 主体归为同一未知企业组，需人工确认。",
]


class GrowthSQLInput(StrictSchema):
    metric_ir: DerivedMetricIR
    execution_context: ExecutionContext


class GenerateGrowthRateSQL(Tool[GrowthSQLInput, SQLDraft]):
    spec = ToolSpec(
        name="generate_growth_rate_sql",
        version="1.0.0",
        description="Compile two atomic periods and a guarded growth-rate expression",
    )
    input_model = GrowthSQLInput
    output_model = SQLDraft

    def __init__(self, atomic_tool: GenerateSparkSQLMetric | None = None):
        self.atomic_tool = atomic_tool if atomic_tool is not None else GenerateSparkSQLMetric()

    def execute(self, payload: GrowthSQLInput) -> SQLDraft:
        plan = DerivedMetricPlanner().plan(payload.metric_ir, payload.execution_context)
        atomic = self.atomic_tool
        outputs = [
            atomic.invoke(metric_to_tool_input(item.metric_ir, item.execution_context).model_dump())
            for item in plan.base_metrics
        ]
        template = Environment(
            loader=PackageLoader("airi.tools", "templates"),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        ).get_template("spark/growth_rate.sql.j2")
        code = template.render(
            current_sql=outputs[0].code.strip().removesuffix(";"),
            previous_sql=outputs[1].code.strip().removesuffix(";"),
            metric_code=payload.metric_ir.name,
        )
        return SQLDraft(
            code=code,
            warnings=[*outputs[0].warnings, *GROWTH_WARNINGS],
            template=VersionedReference(name="growth_rate", version="1.0.0"),
        )
