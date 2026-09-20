from typing import Literal

from airi.core.schemas import VersionedReference
from airi.refinement.transform import validate_candidate_metric
from airi.skills.models import CapabilitySkill, ScenarioSkill
from airi.tools.base import ToolSpec
from airi.tools.spark_sql import GenerateSparkSQLMetric, SparkSQLInput


class CandidateSQLInput(SparkSQLInput):
    window_size: Literal[60, 90]
    aggregation: Literal["sum"]


class GenerateCandidateSQL(GenerateSparkSQLMetric):
    spec = ToolSpec(
        name="generate_spark_sql_metric",
        version="1.2.0",
        description="Controlled enterprise amount window candidate SQL",
    )
    input_model = CandidateSQLInput


def candidate_input(metric, context):
    validate_candidate_metric(metric)
    return CandidateSQLInput(
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


def candidate_skills():
    from airi.skills.invoice import invoice_skills

    scenario = next(s for s in invoice_skills() if isinstance(s, ScenarioSkill))
    skills = [
        CapabilitySkill(
            name=name,
            version="1.1.0",
            description="Controlled window research",
            tools=[VersionedReference(name="generate_spark_sql_metric", version="1.2.0")],
        )
        for name in ("metric_window", "metric_sum", "spark_sql_generator")
    ]
    return [
        *skills,
        scenario.model_copy(
            update={
                "version": "1.1.0",
                "capabilities": [
                    VersionedReference(name=s.name, version=s.version) for s in skills
                ],
            }
        ),
    ]
