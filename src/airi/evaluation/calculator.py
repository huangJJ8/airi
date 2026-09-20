"""Single-feature, labeled-population statistics. No model training or decisions."""

from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal, localcontext

from airi.evaluation.models import (
    EvaluationBin,
    KSResult,
    MetricDistribution,
    MetricEvaluation,
    ThresholdCandidate,
)


def quantile(values, percentile):
    if not values:
        return None
    position = Decimal(len(values) - 1) * Decimal(percentile) / 100
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def ratio(numerator, denominator):
    return float(Decimal(numerator) / Decimal(denominator)) if denominator else None


def lift(bad_rate, overall):
    return bad_rate / overall if bad_rate is not None and overall else None


def calculate(metric_name, rows, total_sample, policy):
    # rows contain (Decimal | None, is_bad). Entity IDs never enter statistical reports.
    with localcontext() as context:
        context.prec = 50
        return _calculate(metric_name, rows, total_sample, policy)


def _calculate(metric_name, rows, total_sample, policy):
    values = sorted(value for value, _ in rows if value is not None)
    null_rows = [row for row in rows if row[0] is None]
    bad = sum(label for _, label in rows)
    good = len(rows) - bad
    overall = ratio(bad, len(rows))
    warnings = []
    if len(rows) < policy.min_labeled_samples:
        warnings.append("insufficient_sample")
    if not good or not bad:
        warnings.append("class_imbalance_extreme: KS/IV require both classes")
    if not values:
        warnings.append("metric_coverage_too_low: no non-null metric values")
    if not bad:
        warnings.append("overall_bad_rate_zero: lift not_available")
    distribution = MetricDistribution(
        count=len(values),
        null_count=len(null_rows),
        min=values[0] if values else None,
        max=values[-1] if values else None,
        mean=sum(values) / len(values) if values else None,
        **{f"p{p}": quantile(values, p) for p in (25, 50, 75, 90, 95, 99)},
    )
    boundaries = (
        sorted({quantile(values, Decimal(i) * 100 / policy.bins) for i in range(1, policy.bins)})
        if values
        else []
    )
    groups = defaultdict(list)
    for value, label in rows:
        if value is not None:
            groups[bisect_right(boundaries, value)].append((value, label))
    ordered = [groups[key] for key in sorted(groups)]
    bins = []
    for index, group in enumerate(ordered + ([null_rows] if null_rows else [])):
        is_null = group[0][0] is None
        nbad = sum(label for _, label in group)
        rate = ratio(nbad, len(group))
        bins.append(
            EvaluationBin(
                bucket=index,
                is_null=is_null,
                lower=None if is_null else min(v for v, _ in group),
                upper=None if is_null else max(v for v, _ in group),
                count=len(group),
                good=len(group) - nbad,
                bad=nbad,
                bad_rate=rate,
                lift=lift(rate, overall),
            )
        )
    iv = None
    if good and bad:
        epsilon = Decimal(str(policy.epsilon))
        iv = Decimal(0)
        smoothed = []
        for bucket in bins:
            gp = (Decimal(bucket.good) + epsilon) / (Decimal(good) + epsilon * len(bins))
            bp = (Decimal(bucket.bad) + epsilon) / (Decimal(bad) + epsilon * len(bins))
            woe = (gp / bp).ln()
            part = (gp - bp) * woe
            iv += part
            smoothed.append(
                bucket.model_copy(
                    update={
                        "good_pct": float(gp),
                        "bad_pct": float(bp),
                        "woe": float(woe),
                        "iv_component": float(part),
                    }
                )
            )
        bins = smoothed
    numeric = [b for b in bins if not b.is_null]
    ngood, nbad = sum(b.good for b in numeric), sum(b.bad for b in numeric)
    ks = KSResult(
        status="not_available",
        value=None,
        ascending=None,
        descending=None,
        bucket=None,
        direction="undetermined",
        split=None,
    )
    if ngood and nbad:
        cumgood = cumbad = 0
        gaps = []
        for b in numeric:
            cumgood += b.good
            cumbad += b.bad
            gaps.append(Decimal(cumgood) / ngood - Decimal(cumbad) / nbad)
        # Ascending and descending absolute KS are mathematically equal on identical bins.
        # Resolve direction by the signed CDF gap, not an arbitrary greater-value assumption.
        index = max(range(len(gaps)), key=lambda i: abs(gaps[i]))
        maximum = abs(gaps[index])
        direction = (
            "higher_is_riskier"
            if gaps[index] > 0
            else "lower_is_riskier"
            if gaps[index] < 0
            else "undetermined"
        )
        split = (
            numeric[index + 1].lower
            if direction == "higher_is_riskier" and index + 1 < len(numeric)
            else numeric[index].upper
            if direction == "lower_is_riskier"
            else None
        )
        ks = KSResult(
            status="available",
            value=float(maximum),
            ascending=float(maximum),
            descending=float(maximum),
            bucket=numeric[index].bucket,
            direction=direction,
            split=split,
        )
    else:
        warnings.append("KS not_available on non-null population")
    candidates = []
    if ks.direction != "undetermined":
        sources = defaultdict(list)
        for p in policy.threshold_percentiles:
            sources[quantile(values, p)].append(f"p{p}")
        if ks.split is not None:
            sources[ks.split].append("ks_best_split")
        high = ks.direction == "higher_is_riskier"
        for threshold, origins in sorted(sources.items()):
            hits = [
                label
                for value, label in rows
                if value is not None and (value >= threshold if high else value <= threshold)
            ]
            hit_bad = sum(hits)
            rate = ratio(hit_bad, len(hits))
            candidates.append(
                ThresholdCandidate(
                    threshold=threshold,
                    operator=">=" if high else "<=",
                    sources=origins,
                    hit_count=len(hits),
                    hit_rate=ratio(len(hits), len(rows)),
                    bad_count=hit_bad,
                    good_count=len(hits) - hit_bad,
                    bad_rate=rate,
                    precision=rate,
                    recall=ratio(hit_bad, bad),
                    lift=lift(rate, overall),
                )
            )
    else:
        warnings.append("risk_direction_undetermined: no directional threshold recommendation")
    return MetricEvaluation(
        metric_name=metric_name,
        total_sample=total_sample,
        labeled_sample=len(rows),
        good_count=good,
        bad_count=bad,
        non_null_metric=len(values),
        null_metric=len(null_rows),
        coverage=ratio(len(values), len(rows)),
        bad_rate=overall,
        distribution=distribution,
        requested_bins=policy.bins,
        actual_bins=len(numeric),
        bins=bins,
        ks=ks,
        iv=float(iv) if iv is not None else None,
        iv_status="available" if iv is not None else "not_available",
        epsilon=policy.epsilon,
        threshold_candidates=candidates,
        warnings=warnings,
    )
