from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from airi.core.execution import SHANGHAI

TOLERANCE = Decimal("1e-8")


def reference_calculate(metric_name, context, rows):
    """Independent Python oracle: no generated SQL, Jinja, or SQL parser."""
    anchor = context.anchor_time.astimezone(SHANGHAI).date()
    current, previous = defaultdict(Decimal), defaultdict(Decimal)
    count = defaultdict(int)
    current_nonnull = set()
    window = {"invoice_amount_60d": 60, "invoice_amount_90d": 90}.get(metric_name, 30)
    for row in rows:
        day = date.fromisoformat(row["invoice_date"])
        key = row["seller_tax_no"]
        amount = Decimal(str(row["invoice_amt"])) if row["invoice_amt"] is not None else Decimal(0)
        if anchor - timedelta(days=window) <= day < anchor:
            current[key] += amount
            count[key] += 1
            if row["invoice_amt"] is not None:
                current_nonnull.add(key)
        elif anchor - timedelta(days=60) <= day < anchor - timedelta(days=30):
            previous[key] += amount
    if metric_name in ("invoice_amount_30d", "invoice_amount_60d", "invoice_amount_90d"):
        return [
            {"entity_id": k, metric_name: v if k in current_nonnull else None}
            for k, v in current.items()
        ]
    if metric_name == "invoice_count_30d":
        return [{"entity_id": k, metric_name: v} for k, v in count.items()]
    result = []
    for key in current.keys() | previous.keys():
        c, p = current[key], previous[key]
        result.append(
            {
                "entity_id": key,
                "current_amount": c,
                "previous_amount": p,
                metric_name: None if p == 0 else (c - p) / p,
            }
        )
    return result


def reference_join_count(metric, tables):
    """Independent Python oracle for a joined DISTINCT count.

    Driven entirely by the Metric IR (base source, key direction, exclusion
    direction, aggregate field) and plain dictionaries — no generated SQL, Jinja
    or SQL parser is involved, so it is a genuinely independent reference.
    """
    if not metric.joins:
        raise KeyError("A joined metric is required")
    base_rows = tables[metric.source.table]
    joined_rows = tables[metric.joins[0].source.table]
    key_pairs = []
    for condition in metric.joins[0].conditions:
        if condition.left.alias == metric.source_alias:
            key_pairs.append((condition.left.field, condition.right.field))
        else:
            key_pairs.append((condition.right.field, condition.left.field))
    exclusion = metric.column_filters[0] if metric.column_filters else None
    matched: dict[tuple, set] = defaultdict(set)
    for row in joined_rows:
        matched[tuple(row[joined_field] for _, joined_field in key_pairs)].add(
            row[metric.aggregation.field]
        )
    counts: dict[str, set] = defaultdict(set)
    for row in base_rows:
        for target in matched.get(tuple(row[base_field] for base_field, _ in key_pairs), ()):
            if exclusion is not None and target == row[exclusion.right.field]:
                continue
            counts[row[metric.entity_key]].add(target)
    return [
        {"entity_id": enterprise, metric.name: len(targets)}
        for enterprise, targets in counts.items()
        if targets
    ]


def reconciles(actual, expected):
    if len(actual) != len(expected):
        return False
    indexed = {r.get("entity_id"): r for r in actual}
    if len(indexed) != len(actual):
        return False
    for row in expected:
        other = indexed.get(row["entity_id"])
        if other is None or other.keys() != row.keys():
            return False
        for key, value in row.items():
            if key == "entity_id":
                continue
            observed = other[key]
            if value is None or observed is None:
                if observed is not value:
                    return False
            else:
                try:
                    delta = abs(Decimal(str(value)) - Decimal(str(observed)))
                    if not delta.is_finite() or delta > TOLERANCE:
                        return False
                except (ValueError, ArithmeticError):
                    return False
    return True
