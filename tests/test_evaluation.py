import math
from decimal import Decimal

import pytest

from airi.evaluation.calculator import calculate, lift, ratio
from airi.evaluation.models import EvaluationPolicy


def evaluate(values, labels, bins=2):
    rows = [
        (None if v is None else Decimal(str(v)), bool(label))
        for v, label in zip(values, labels, strict=True)
    ]
    return calculate("metric", rows, len(rows), EvaluationPolicy(bins=bins))


def test_hand_calculated_perfect_separation():
    result = evaluate([1, 2, 3, 4], [0, 0, 1, 1])
    assert result.coverage == 1
    assert result.bad_rate == 0.5
    assert result.distribution.mean == Decimal("2.5")
    assert result.distribution.p25 == Decimal("1.75")
    assert result.ks.value == 1
    assert result.ks.direction == "higher_is_riskier"
    assert result.ks.split == 3
    assert [b.lift for b in result.bins] == [0, 2]
    # Independent hand formula: two pure bins, 2 observations per class, additive epsilon.
    e = 1e-6
    a, b = (2 + e) / (2 + 2 * e), e / (2 + 2 * e)
    assert result.iv == pytest.approx(2 * (a - b) * math.log(a / b))
    best = next(c for c in result.threshold_candidates if "ks_best_split" in c.sources)
    assert (best.hit_count, best.bad_count, best.good_count) == (2, 2, 0)
    assert (best.hit_rate, best.precision, best.recall, best.lift) == (0.5, 1, 1, 2)


def test_reversed_direction():
    result = evaluate([1, 2, 3, 4], [1, 1, 0, 0])
    assert result.ks.value == 1
    assert result.ks.direction == "lower_is_riskier"
    assert result.ks.split == 2
    assert all(c.operator == "<=" for c in result.threshold_candidates)


def test_no_separation_and_ties():
    result = evaluate([1, 1, 2, 2], [0, 1, 0, 1], bins=10)
    assert result.ks.value == 0
    assert result.iv == 0
    assert result.actual_bins == 2
    assert all(b.count for b in result.bins)
    constant = evaluate([7] * 4, [0, 1, 0, 1], bins=10)
    assert constant.actual_bins == 1 and constant.iv == 0
    assert constant.ks.direction == "undetermined"


@pytest.mark.parametrize("labels", [[0] * 4, [1] * 4])
def test_single_class(labels):
    result = evaluate([1, 2, 3, 4], labels)
    assert result.ks.value is None
    assert result.iv is None
    assert result.iv_status == "not_available"
    assert result.warnings
    if not any(labels):
        assert all(b.lift is None for b in result.bins)


def test_empty():
    result = evaluate([], [])
    assert result.coverage is None
    assert result.distribution.min is None
    assert result.ks.status == "not_available"
    assert result.threshold_candidates == []


def test_null_bucket_population():
    result = evaluate([1, 2, 3, None], [0, 0, 1, 1])
    assert result.coverage == 0.75
    assert result.null_metric == 1
    assert result.bins[-1].is_null
    assert result.bins[-1].bad_rate == 1
    assert result.ks.value == 0.5  # bins [1] and [2,3]: cumulative good=1/2, bad=0
    assert result.iv is not None
    assert all(c.hit_count <= 3 for c in result.threshold_candidates)


def test_threshold_dedup_and_precision():
    result = evaluate([0, 0, 10, 10, 10], [0, 0, 1, 1, 1])
    thresholds = [c.threshold for c in result.threshold_candidates]
    assert len(thresholds) == len(set(thresholds))
    assert any(len(c.sources) > 1 for c in result.threshold_candidates)
    precise = evaluate(["100000000000000000.01", "100000000000000000.03"], [0, 1])
    assert precise.distribution.mean == Decimal("100000000000000000.02")
    assert lift(None, 0.5) is None and ratio(0, 0) is None
