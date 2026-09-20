"""Generic, mechanism-level validation for joined metrics.

This module knows what a *join metric* is — aliased sources, anchored equality
joins, a DISTINCT aggregate taken from a joined alias, cross-column exclusions
and no time window — and absolutely nothing about any business path. It is the
shared gate every joined metric passes before a scenario declaration adds its
own, narrower allowlist (see :mod:`airi.metric_ir.enterprise_relation`).

The rules are deliberately structural: they cannot be satisfied by renaming a
field, and they never repair an input. Anything outside the mechanism is
rejected with the reason.
"""

from airi.core.exceptions import UnsupportedMetricError
from airi.metric_ir.models import MetricIR

JOIN_AGGREGATION = "count_distinct"
MAX_JOINS = 2


def alias_set(metric: MetricIR) -> set[str]:
    """Every alias a predicate may reference."""
    return {metric.source_alias, *(join.alias for join in metric.joins)} - {None}


def anchored_join_aliases(metric: MetricIR) -> set[str]:
    """Join aliases that are connected to the base source by an equality key."""
    anchored = set()
    for join in metric.joins:
        sides = {
            side.alias
            for condition in join.conditions
            for side in (condition.left, condition.right)
        }
        if join.alias in sides and metric.source_alias in sides:
            anchored.add(join.alias)
    return anchored


def validate_join_metric(metric: MetricIR) -> MetricIR:
    """Accept only the declared join *mechanism*; never repair the semantics."""
    metric = MetricIR.model_validate(metric.model_dump())
    problems: list[str] = []
    if not metric.joins:
        problems.append("no join is declared")
    if len(metric.joins) > MAX_JOINS:
        problems.append(f"at most {MAX_JOINS} joins are supported")
    if metric.source_alias is None or metric.aggregation_alias is None:
        problems.append("the base source and the aggregate must both be aliased")
    if metric.aggregation.function != JOIN_AGGREGATION or metric.aggregation.field is None:
        problems.append(f"the only supported join aggregation is {JOIN_AGGREGATION} over a field")
    if metric.window is not None or metric.partition_field is not None:
        problems.append("the join mechanism is not windowed and takes no partition")
    if metric.filters or metric.dimensions:
        problems.append("the join mechanism supports no row filters and no dimensions")
    if metric.source.catalog is not None:
        problems.append("the base source must not name a catalog")
    if metric.source_alias is not None and metric.aggregation_alias == metric.source_alias:
        problems.append("the aggregated value must come from a joined source, not the base source")
    unanchored = sorted(
        join.alias for join in metric.joins if join.alias not in anchored_join_aliases(metric)
    )
    for alias in unanchored:
        problems.append(f"joined source {alias} is not keyed to the base source")
    if problems:
        raise UnsupportedMetricError(
            "Joined metric is outside the supported join mechanism: " + "; ".join(problems)
        )
    return metric
