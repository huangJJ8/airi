"""Fixed reference distributions. Never persist subjects or reoptimize on OOT."""

from bisect import bisect_right
from decimal import Decimal, localcontext
from statistics import fmean, pstdev

from airi.evaluation.calculator import lift, quantile, ratio
from airi.evaluation.models import ThresholdCandidate
from airi.temporal.models import PSIBin, PSIResult, ThresholdTemporalResult


def fixed_boundaries(values, bins):
    numeric = sorted(v for v in values if v is not None)
    return (
        sorted({quantile(numeric, Decimal(i) * 100 / bins) for i in range(1, bins)})
        if numeric
        else []
    )


def bucket_counts(values, boundaries):
    counts = [0] * (len(boundaries) + 2)
    for value in values:
        counts[-1 if value is None else bisect_right(boundaries, value)] += 1
    return counts


def psi_bins(expected_counts, actual_counts, epsilon):
    """Epsilon-smoothed PSI contributions over aligned bin counts.

    The single smoothing rule behind both the Phase 5 temporal PSI and the
    Phase 7 runtime PSI. Phase 7 must not grow a second implementation.
    """
    if len(expected_counts) != len(actual_counts) or not expected_counts:
        raise ValueError("PSI counts must be aligned and non-empty")
    if not 0 < epsilon <= 0.01:
        raise ValueError("Invalid PSI epsilon")
    expected_total, actual_total = sum(expected_counts), sum(actual_counts)
    if expected_total <= 0 or actual_total <= 0:
        raise ValueError("PSI counts must have a positive total")
    results = []
    with localcontext() as context:
        context.prec = 50
        eps = Decimal(str(epsilon))
        size = len(expected_counts)
        for index, (expected, observed) in enumerate(
            zip(expected_counts, actual_counts, strict=True)
        ):
            # Add epsilon to proportions, then renormalize. NULL is always included.
            e = (Decimal(expected) / expected_total + eps) / (1 + eps * size)
            a = (Decimal(observed) / actual_total + eps) / (1 + eps * size)
            contribution = (a - e) * (a / e).ln()
            results.append(
                PSIBin(
                    bucket=index,
                    is_null=index == size - 1,
                    expected_count=expected,
                    actual_count=observed,
                    expected_pct=float(e),
                    actual_pct=float(a),
                    contribution=float(contribution),
                )
            )
    return results


def psi(reference, actual, boundaries, epsilon):
    if boundaries != sorted(set(boundaries)):
        raise ValueError("PSI boundaries must be sorted and unique")
    if not 0 < epsilon <= 0.01:
        raise ValueError("Invalid PSI epsilon")
    if not reference or not actual:
        return PSIResult(
            status="not_available",
            value=None,
            boundaries=boundaries,
            epsilon=epsilon,
            bins=[],
            warnings=["empty_distribution"],
        )
    results = psi_bins(
        bucket_counts(reference, boundaries), bucket_counts(actual, boundaries), epsilon
    )
    return PSIResult(
        status="available",
        value=sum(b.contribution for b in results),
        boundaries=boundaries,
        epsilon=epsilon,
        bins=results,
        warnings=[],
    )


def frozen_threshold(rows, reference):
    high = reference.operator == ">="
    hits = [
        bad
        for value, bad in rows
        if value is not None
        and (value >= reference.threshold if high else value <= reference.threshold)
    ]
    bad = sum(label for _, label in rows)
    rate = ratio(sum(hits), len(hits))
    result = ThresholdCandidate(
        threshold=reference.threshold,
        operator=reference.operator,
        sources=reference.sources,
        hit_count=len(hits),
        hit_rate=ratio(len(hits), len(rows)),
        bad_count=sum(hits),
        good_count=len(hits) - sum(hits),
        bad_rate=rate,
        precision=rate,
        recall=ratio(sum(hits), bad),
        lift=lift(rate, ratio(bad, len(rows))),
    )
    deltas = {
        key: None
        if getattr(result, key) is None or getattr(reference, key) is None
        else getattr(result, key) - getattr(reference, key)
        for key in ("hit_rate", "precision", "recall", "lift")
    }
    return ThresholdTemporalResult(performance=result, deltas=deltas)


def summary(values):
    available = [v for v in values if v is not None]
    return {
        "available_slices": len(available),
        "missing_slices": len(values) - len(available),
        "min": min(available) if available else None,
        "max": max(available) if available else None,
        "range": max(available) - min(available) if available else None,
        "mean": fmean(available) if available else None,
        "std": pstdev(available) if available else None,
    }


def direction_consistency(expected, directions):
    unknown = sum(d == "undetermined" for d in directions)
    flips = (
        sum(d not in (expected, "undetermined") for d in directions)
        if expected != "undetermined"
        else 0
    )
    return {
        "expected_direction": expected,
        "consistent_slices": sum(d == expected for d in directions)
        if expected != "undetermined"
        else 0,
        "inconsistent_slices": flips,
        "undetermined_slices": unknown,
        "status": "unstable"
        if flips
        else "inconclusive"
        if unknown or expected == "undetermined"
        else "consistent",
    }
