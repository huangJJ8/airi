import re

from airi.core.exceptions import AIRIError
from airi.execution.models import ExecutionProfile
from airi.metric_ir.derived import DerivedMetricIR, validate_derived_metric
from airi.refinement.transform import validate_executable_metric
from airi.workflows.development.dispatch import validator_for_metric


class SandboxViolation(AIRIError):
    code = "sandbox_violation"
    status_code = 403


DENIED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|REPLACE|LOAD|EXPORT|IMPORT|"
    r"CACHE|UNCACHE|REFRESH|SET|ADD|DFS|reflect|java_method|xpath|input_file_name)\b",
    re.I,
)


def validate_sql(code, metric, context):
    return validator_for_metric(metric).validate(code, "spark_sql", metric, context)


def declared_sources(metric):
    """Every source the statement reads: the base source plus every joined source."""
    metrics = (
        [dependency.metric for dependency in metric.dependencies]
        if isinstance(metric, DerivedMetricIR)
        else [metric]
    )
    return [
        source
        for item in metrics
        for source in (item.source, *(join.source for join in getattr(item, "joins", [])))
    ]


class SandboxGuard:
    """Closed grammar admits only the reviewed metric templates, not general SQL."""

    def validate(self, code, metric, context, profile: ExecutionProfile) -> None:
        if DENIED.search(code) or ";" in code.strip().rstrip(";"):
            raise SandboxViolation("Forbidden operation or multiple statements")
        for source in declared_sources(metric):
            if (
                source.database not in profile.allowed_databases
                or source.database in profile.blocked_databases
            ):
                raise SandboxViolation("Source database is not allowed")
        if isinstance(metric, DerivedMetricIR):
            validate_derived_metric(metric)
        else:
            validate_executable_metric(metric)
        if not validate_sql(code, metric, context).valid:
            raise SandboxViolation("SQL does not match approved metric and execution context")
