import pytest
from pydantic import ValidationError

from airi.metric_ir.models import Aggregation, DataSource, FilterCondition, MetricIR, TimeWindow


def test_metric_json_roundtrip(metric_payload):
    metric = MetricIR.model_validate(metric_payload)
    assert MetricIR.model_validate_json(metric.model_dump_json()) == metric
    assert metric.source.table == "payment_events"
    assert metric.window.size == 30


@pytest.mark.parametrize(
    "update",
    [
        {"sql": "SELECT * FROM payments"},
        {"schema_version": "2.0.0"},
        {"name": "metric; DROP TABLE x"},
        {"description": "  "},
        {"dimensions": ["channel", "channel"]},
        {"dimensions": ["user_id"]},
    ],
)
def test_metric_rejects_invalid_semantics(metric_payload, update):
    with pytest.raises(ValidationError):
        MetricIR.model_validate(metric_payload | update)


@pytest.mark.parametrize("size", [0, -1, "30", True, 1.5, 3661])
def test_window_requires_bounded_integer(size):
    with pytest.raises(ValidationError):
        TimeWindow(size=size, unit="day", time_field="event_time")


@pytest.mark.parametrize("function", ["count_distinct", "sum", "avg", "min", "max"])
def test_aggregation_requires_field(function):
    with pytest.raises(ValidationError):
        Aggregation(function=function)
    assert Aggregation(function=function, field="amount").field == "amount"


def test_source_rejects_sql_fragments():
    with pytest.raises(ValidationError):
        DataSource(database="risk", table="events e JOIN users u")


@pytest.mark.parametrize(
    "operator,value",
    [
        ("eq", "failed"),
        ("gte", 10),
        ("in", [1, 2]),
        ("not_in", ["a", "b"]),
        ("is_null", None),
        ("not_null", None),
    ],
)
def test_filter_valid_operands(operator, value):
    assert FilterCondition(field="status", operator=operator, value=value).value == value


@pytest.mark.parametrize(
    "operator,value",
    [
        ("eq", None),
        ("eq", float("nan")),
        ("in", [float("inf")]),
        ("gt", [1]),
        ("in", []),
        ("in", "a"),
        ("in", [1, "2"]),
        ("is_null", 1),
        ("not_null", False),
        ("raw_sql", "1=1"),
    ],
)
def test_filter_invalid_operands(operator, value):
    with pytest.raises(ValidationError):
        FilterCondition(field="status", operator=operator, value=value)


def test_collections_not_shared(metric_payload):
    first = MetricIR.model_validate(metric_payload)
    second = MetricIR.model_validate(metric_payload)
    first.dimensions.append("channel")
    assert second.dimensions == []


def test_metric_schema_forbids_unknown_nested_fields(metric_payload):
    metric_payload["source"]["query"] = "select 1"
    with pytest.raises(ValidationError):
        MetricIR.model_validate(metric_payload)
