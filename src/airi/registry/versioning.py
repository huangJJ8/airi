"""Deterministic version semantics. No LLM, no client-supplied version numbers."""

import json
import re
from typing import TYPE_CHECKING, Literal, NamedTuple

from airi.metric_ir.models import MetricIR
from airi.registry.models import VersionChangedField

if TYPE_CHECKING:
    from airi.registry.models import MetricVersion

ChangeKind = Literal["patch", "minor", "major"]

# Changing any of these changes what the number means to the business.
MAJOR_FIELDS = frozenset(
    {
        "source.catalog",
        "source.database",
        "source.table",
        "entity_key",
        "entity_type",
        "partition_field",
        "aggregation.function",
        "aggregation.field",
        "window.size",
        "window.unit",
        "window.time_field",
        "window.timezone",
        "window",
        "filters",
        "schema_version",
        # Where the entity value comes from is part of what the number means:
        # adding, removing or re-keying a join is never a silent patch.
        "source_alias",
        "aggregation_alias",
        "joins",
        "column_filters",
    }
)
# Compatible business enhancement: adds analytical context without moving the entity value.
MINOR_FIELDS = frozenset({"dimensions"})
# Labels only. A rename is not a semantic change.
PATCH_FIELDS = frozenset({"name", "display_name", "description"})

FIELD_ORDER = (
    "schema_version",
    "name",
    "display_name",
    "description",
    "entity_type",
    "entity_key",
    "partition_field",
    "source.catalog",
    "source.database",
    "source.table",
    "aggregation.function",
    "aggregation.field",
    "dimensions",
    "filters",
    "window",
    "window.size",
    "window.unit",
    "window.time_field",
    "window.timezone",
    "source_alias",
    "aggregation_alias",
    "joins",
    "column_filters",
)


class VersionPlan(NamedTuple):
    kind: Literal["initial", "patch", "minor", "major"]
    version: str
    changed_fields: list[VersionChangedField]
    unchanged: list[str]


def semantic_leaves(metric: MetricIR) -> dict[str, object]:
    """Flatten the IR into comparable deterministic leaves in a stable order."""
    leaves: dict[str, object] = {
        "schema_version": metric.schema_version,
        "name": metric.name,
        "display_name": metric.display_name,
        "description": metric.description,
        "entity_type": metric.entity_type,
        "entity_key": metric.entity_key,
        "partition_field": metric.partition_field,
        "source.catalog": metric.source.catalog,
        "source.database": metric.source.database,
        "source.table": metric.source.table,
        "aggregation.function": metric.aggregation.function,
        "aggregation.field": metric.aggregation.field,
        "dimensions": sorted(metric.dimensions),
        "filters": [
            json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in metric.filters
        ],
        "window": metric.window is not None,
        "source_alias": metric.source_alias,
        "aggregation_alias": metric.aggregation_alias,
        "joins": [
            json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in metric.joins
        ],
        "column_filters": [
            json.dumps(item.model_dump(mode="json"), sort_keys=True)
            for item in metric.column_filters
        ],
    }
    if metric.window is not None:
        leaves.update(
            {
                "window.size": metric.window.size,
                "window.unit": metric.window.unit,
                "window.time_field": metric.window.time_field,
                "window.timezone": metric.window.timezone,
            }
        )
    return leaves


def semantic_diff(before: MetricIR, after: MetricIR):
    """Return (changed_fields, unchanged) using the project's ChangedField shape."""
    left, right = semantic_leaves(before), semantic_leaves(after)
    changed, unchanged = [], []
    for field in FIELD_ORDER:
        if field not in left and field not in right:
            continue
        if left.get(field) == right.get(field):
            unchanged.append(field)
        else:
            changed.append(
                VersionChangedField(field=field, before=left.get(field), after=right.get(field))
            )
    return changed, unchanged


class VersionChangeClassifier:
    """Advisory classification. The server still owns the resulting version number."""

    def classify(self, changed_fields) -> ChangeKind:
        fields = {item.field for item in changed_fields}
        if fields & MAJOR_FIELDS:
            return "major"
        if fields & MINOR_FIELDS:
            return "minor"
        return "patch"


class MetricFamilyKey:
    """Definition identity strategy: a trailing ``_<n>d`` window suffix is not identity.

    ``invoice_amount_30d`` and ``invoice_amount_60d`` are two versions of the
    ``invoice_amount`` family. Historical metric names are never rewritten.
    """

    WINDOW_SUFFIX = re.compile(r"_\d+d$")

    def derive(self, metric_name: str) -> str:
        return self.WINDOW_SUFFIX.sub("", metric_name) or metric_name

    def display_name(self, metric_key: str) -> str:
        return metric_key.replace("_", " ")


class MetricVersionPlanner:
    """Plan the next server-side version number from the previous version, if any."""

    def __init__(self):
        self.classifier = VersionChangeClassifier()

    def plan(self, metric: MetricIR, previous: "MetricVersion | None") -> VersionPlan:
        if previous is None:
            return VersionPlan("initial", "1.0.0", [], list(FIELD_ORDER))
        changed, unchanged = semantic_diff(previous.metric_ir, metric)
        kind = self.classifier.classify(changed)
        return VersionPlan(kind, bump(previous.version, kind), changed, unchanged)


def bump(current: str, kind: ChangeKind) -> str:
    major, minor, patch = (int(part) for part in current.split("."))
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"
