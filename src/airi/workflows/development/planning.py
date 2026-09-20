from pydantic import Field

from airi.core.exceptions import PlanningError
from airi.core.schemas import StrictSchema, VersionedReference
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR
from airi.skills.models import CapabilitySkill, ScenarioSkill

# Capability names the planner reasons about. Declared here once instead of
# spelling mechanism names inside the planner body.
WINDOW_CAPABILITY = "metric_window"
JOIN_CAPABILITY = "metric_join"
GENERATOR_CAPABILITY = "spark_sql_generator"
GROWTH_CAPABILITY = "metric_growth_rate"

# Which mechanism realises which aggregation. An aggregation absent from this map
# has no declared capability anywhere, so the planner refuses instead of guessing.
AGGREGATION_CAPABILITIES = {
    "sum": "metric_sum",
    "count": "metric_count",
    # DISTINCT is a counting mechanism: the count capability covers both the row
    # count and the distinct count, whichever the IR declares.
    "count_distinct": "metric_count",
}

# An aggregation whose meaning is defined over a period. A window-less SUM or
# COUNT is not a metric, so its absence is a planning error rather than a
# silently window-less plan. A state-of-record DISTINCT count is not windowed.
WINDOWED_AGGREGATIONS = {"sum", "count"}


class SkillPlan(StrictSchema):
    scenario_skill: VersionedReference
    capabilities: list[VersionedReference] = Field(min_length=1)


class ToolPlan(StrictSchema):
    tools: list[VersionedReference] = Field(min_length=1, max_length=2)


class SkillPlanner:
    def plan(self, scenario: ScenarioSkill, metric: MetricIR | DerivedMetricIR) -> SkillPlan:
        names = self.required_capabilities(metric)
        declared = {reference.name: reference for reference in scenario.capabilities}
        if len(declared) != len(scenario.capabilities):
            raise PlanningError("A capability cannot be required at multiple versions")
        missing = [name for name in names if name not in declared]
        if missing:
            raise PlanningError(f"Scenario lacks required capabilities: {', '.join(missing)}")
        return SkillPlan(
            scenario_skill=VersionedReference(name=scenario.name, version=scenario.version),
            capabilities=[declared[name] for name in names],
        )

    def required_capabilities(self, metric: MetricIR | DerivedMetricIR) -> list[str]:
        """Derive the capabilities from the IR shape, not from the scenario name."""
        if isinstance(metric, DerivedMetricIR):
            return [
                WINDOW_CAPABILITY,
                AGGREGATION_CAPABILITIES["sum"],
                GENERATOR_CAPABILITY,
                GROWTH_CAPABILITY,
            ]
        function = metric.aggregation.function
        if function not in AGGREGATION_CAPABILITIES:
            raise PlanningError(
                f"Aggregation {function} has no declared capability; SUM, COUNT and "
                "COUNT DISTINCT are supported"
            )
        names = [AGGREGATION_CAPABILITIES[function]]
        if metric.joins:
            names.append(JOIN_CAPABILITY)
        if metric.window is not None or function in WINDOWED_AGGREGATIONS:
            if metric.window is None:
                raise PlanningError("Atomic metrics require SUM or COUNT and a time window")
            names.insert(0, WINDOW_CAPABILITY)
        names.append(GENERATOR_CAPABILITY)
        return names


class ToolPlanner:
    """Resolve the plan's pinned tools. Nothing here resolves "latest"."""

    ATOMIC = VersionedReference(name="generate_spark_sql_metric", version="1.1.0")
    CANDIDATE = VersionedReference(name="generate_spark_sql_metric", version="1.2.0")
    JOIN = VersionedReference(name="generate_spark_sql_metric", version="1.3.0")
    GROWTH = VersionedReference(name="generate_growth_rate_sql", version="1.0.0")

    # Most specific mechanism first. When a plan pins several renderers, the first
    # one present renders the metric SQL and the others are its inputs: the growth
    # tool consumes the atomic draft, the join tool replaces it.
    PRECEDENCE = (JOIN, CANDIDATE, GROWTH, ATOMIC)

    def plan(self, capabilities: list[CapabilitySkill]) -> ToolPlan:
        selected = list(dict.fromkeys(tool for skill in capabilities for tool in skill.tools))
        generator = next((ref for ref in self.PRECEDENCE if ref in selected), None)
        if generator is None:
            raise PlanningError(
                "Only the declared atomic, candidate, growth and join SQL tools are supported"
            )
        # Same-family base renderers collapse onto the plan's pinned version: the
        # capability still declares the tool family, the plan pins exactly one.
        others = [ref for ref in selected if ref != generator and ref.name != generator.name]
        return ToolPlan(tools=[*others, generator])
