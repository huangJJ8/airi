"""Business allowlist for the ``enterprise_relation`` scenario.

Schema validation happens first in the workflow; this module is the second,
narrower gate that decides whether the parsed semantics are the *declared*
relationship metric. It never repairs or rewrites LLM output: anything outside
the declaration is rejected with an explicit reason.

The declaration is intentionally one metric: the count of distinct other
enterprises reachable through the two-hop path

    enterprise (base) -> person (association) -> other enterprise (related)

with the self-loop excluded. Relationship types are NOT filtered in v1: every
declared relation type contributes, and the database is the synthetic ``demo``
schema only.
"""

from airi.core.exceptions import UnsupportedMetricError
from airi.metric_ir.joins import validate_join_metric
from airi.metric_ir.models import FieldComparison, JoinSpec, MetricIR, QualifiedField

RELATION_SOURCE_DATABASE = "demo"
RELATION_BASE_TABLE = "enterprise_person_relation"
RELATION_JOINED_TABLE = "person_enterprise_relation"
RELATION_BASE_ALIAS = "ep"
RELATION_JOINED_ALIAS = "pe"
RELATION_METRIC_NAME = "related_enterprise_count"


def enterprise_relation_path() -> tuple[QualifiedField, QualifiedField]:
    """The declared join keys, in (left, right) order."""
    return (
        QualifiedField(alias=RELATION_BASE_ALIAS, field="person_id"),
        QualifiedField(alias=RELATION_JOINED_ALIAS, field="person_id"),
    )


def enterprise_relation_join() -> JoinSpec:
    left, right = enterprise_relation_path()
    return JoinSpec(
        alias=RELATION_JOINED_ALIAS,
        join_type="inner",
        source={
            "database": RELATION_SOURCE_DATABASE,
            "table": RELATION_JOINED_TABLE,
        },
        conditions=[FieldComparison(left=left, operator="=", right=right)],
    )


def self_exclusion() -> FieldComparison:
    """The rule that stops an enterprise counting itself through its own person."""
    return FieldComparison(
        left=QualifiedField(alias=RELATION_JOINED_ALIAS, field="related_enterprise_id"),
        operator="<>",
        right=QualifiedField(alias=RELATION_BASE_ALIAS, field="enterprise_id"),
    )


def validate_enterprise_relation_metric(metric: MetricIR) -> MetricIR:
    """Accept only the declared two-hop relationship count; never repair semantics.

    Two layers, in order: the generic join *mechanism* gate, then this
    scenario's narrower business allowlist.
    """
    metric = validate_join_metric(metric)
    left, right = enterprise_relation_path()
    if not (
        metric.name == RELATION_METRIC_NAME
        and metric.entity_type == "enterprise"
        and metric.entity_key == "enterprise_id"
        and metric.partition_field is None
        and metric.source.catalog is None
        and metric.source.database == RELATION_SOURCE_DATABASE
        and metric.source.table == RELATION_BASE_TABLE
        and metric.source_alias == RELATION_BASE_ALIAS
        and metric.aggregation.function == "count_distinct"
        and metric.aggregation.field == "related_enterprise_id"
        and metric.aggregation_alias == RELATION_JOINED_ALIAS
        and metric.window is None
        and len(metric.joins) == 1
        and metric.joins[0].alias == RELATION_JOINED_ALIAS
        and metric.joins[0].join_type == "inner"
        and metric.joins[0].source.catalog is None
        and metric.joins[0].source.database == RELATION_SOURCE_DATABASE
        and metric.joins[0].source.table == RELATION_JOINED_TABLE
        and len(metric.joins[0].conditions) == 1
        and metric.joins[0].conditions[0].left == left
        and metric.joins[0].conditions[0].operator == "="
        and metric.joins[0].conditions[0].right == right
        and len(metric.column_filters) == 1
        and metric.column_filters[0] == self_exclusion()
        and not metric.filters
        and not metric.dimensions
    ):
        raise UnsupportedMetricError(
            "Only the declared enterprise relationship COUNT DISTINCT over the "
            "two-hop enterprise -> person -> enterprise path is supported"
        )
    return metric
