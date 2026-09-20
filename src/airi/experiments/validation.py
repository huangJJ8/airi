from decimal import Decimal

from airi.approvals.artifacts import canonical_hash
from airi.core.exceptions import AIRIError


class ExperimentError(AIRIError):
    code = "experiment_invalid"
    status_code = 409

    def __init__(self, category):
        super().__init__(category)
        self.category = category.split(":", 1)[0]


class LeakageValidator:
    def validate(self, spec):
        if spec.observation_time != spec.anchor_time or spec.anchor_time >= spec.label_window.start:
            raise ExperimentError("leakage_detected")


def dataset_checksum(rows, entity_key="seller_tax_no"):
    return canonical_hash(sorted(rows, key=lambda row: str(row.get(entity_key))))


def evaluation_dataset(metric_rows, dataset_rows, metric_name, snapshot, label):
    entity_key = snapshot.entity_key
    if (
        len(dataset_rows) != snapshot.row_count
        or dataset_checksum(dataset_rows, entity_key) != snapshot.checksum
    ):
        raise ExperimentError("dataset_invalid")
    if label.entity_key != entity_key:
        raise ExperimentError("dataset_invalid: label entity key differs from the snapshot")
    entities, labels = set(), {}
    for row in dataset_rows:
        if not {entity_key, label.label_field, "dt"}.issubset(row):
            raise ExperimentError("dataset_invalid")
        entity = row[entity_key]
        if entity is None or not isinstance(entity, str) or row["dt"] != snapshot.partition.value:
            raise ExperimentError("dataset_invalid")
        if entity in entities:
            raise ExperimentError("duplicate_entity")
        entities.add(entity)
        value = row[label.label_field]
        if value is None:
            continue
        if type(value) is not int or value not in (label.good_value, label.bad_value):
            raise ExperimentError("label_invalid")
        labels[entity] = value == label.bad_value
    metrics = {}
    for row in metric_rows:
        if "entity_id" not in row or metric_name not in row:
            raise ExperimentError("evaluation_failed")
        entity = row["entity_id"]
        if entity in metrics:
            raise ExperimentError("duplicate_entity")
        value = row[metric_name]
        try:
            value = None if value is None else Decimal(str(value))
            if value is not None and not value.is_finite():
                raise ValueError
        except (ValueError, ArithmeticError):
            raise ExperimentError("evaluation_failed") from None
        metrics[entity] = value
    matched = labels.keys() & metrics.keys()
    return [(metrics.get(entity), bad) for entity, bad in labels.items()], {
        "label_entity_count": len(labels),
        "matched_count": len(matched),
        "label_only": len(labels.keys() - metrics.keys()),
        "metric_only": len(metrics.keys() - labels.keys()),
        "unknown_label_excluded": len(entities) - len(labels),
        "metric_non_null_count": sum(metrics[entity] is not None for entity in matched),
    }
