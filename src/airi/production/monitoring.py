"""Phase 7 runtime monitoring: deterministic findings, never a model opinion.

Batch PSI (Phase 5 temporal validation) and runtime PSI (Phase 7 monitoring) share
one epsilon-smoothed implementation, :func:`airi.temporal.statistics.psi_bins`.
Monitoring compares a snapshot distribution against a frozen
:class:`~airi.production.models.MonitoringBaseline`, and every threshold comes
from :class:`~airi.production.models.MonitoringPolicy`.
"""

from bisect import bisect_right
from datetime import datetime

from airi.approvals.artifacts import canonical_hash
from airi.production.models import (
    MonitoringBaseline,
    MonitoringDistribution,
    MonitoringDistributionBin,
    MonitoringFinding,
    MonitoringPolicy,
    MonitoringSnapshot,
)
from airi.temporal.statistics import psi_bins

# Fixed evaluation order so a snapshot always yields findings in the same sequence.
FINDING_ORDER = (
    "execution_failure",
    "latency_degradation",
    "coverage_drop",
    "null_rate_increase",
    "duplicate_increase",
    "population_shift",
    "missing_monitoring_data",
)


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def bins_from_values(values, *, bin_count: int = 10) -> MonitoringDistribution:
    """Equal-width bins plus an explicit NULL bin. Aggregate only, never rows."""
    numeric = sorted(float(value) for value in values if value is not None)
    total = len(values)
    if total == 0:
        return MonitoringDistribution(bins=[], total=0)
    nulls = total - len(numeric)
    bins: list[MonitoringDistributionBin] = []
    if numeric:
        low, high = numeric[0], numeric[-1]
        edges = (
            []
            if high <= low
            else [low + (high - low) * index / bin_count for index in range(1, bin_count)]
        )
        counts = [0] * (len(edges) + 1)
        for value in numeric:
            counts[bisect_right(edges, value)] += 1
        bounds: list[float | None] = [None, *edges, None]
        bins.extend(
            MonitoringDistributionBin(lower=bounds[index], upper=bounds[index + 1], count=count)
            for index, count in enumerate(counts)
        )
    bins.append(MonitoringDistributionBin(count=nulls, is_null=True))
    return MonitoringDistribution(bins=bins, total=total)


def runtime_psi(
    baseline: MonitoringDistribution | None,
    current: MonitoringDistribution,
    epsilon: float,
) -> tuple[float | None, str]:
    """Reuse the Phase 5 smoothing over aligned bin counts."""
    if baseline is None or not baseline.bins or not current.bins:
        return None, "not_available"
    if len(baseline.bins) != len(current.bins):
        return None, "baseline_mismatch"
    try:
        bins = psi_bins(
            [item.count for item in baseline.bins],
            [item.count for item in current.bins],
            epsilon,
        )
    except ValueError:
        return None, "not_available"
    return sum(item.contribution for item in bins), "available"


def finding_dedup_key(deployment_id: str, finding_type: str, severity: str) -> str:
    """One alert row per deployment/type/severity. Repetition increments a counter."""
    return canonical_hash(
        {"deployment_id": deployment_id, "type": finding_type, "severity": severity}
    )


def recommended_action(severity: str) -> str:
    return {
        "critical": "consider_rollback",
        "warning": "investigate",
        "info": "observe",
    }[severity]


def telemetry_freshness(
    observation_time: datetime, received_at: datetime, policy: MonitoringPolicy
) -> tuple[str, float]:
    """Classify observation-to-receipt lag. Returns ``(freshness, lag_hours)``.

    A snapshot observed long ago but only just received is never treated as a
    current reading - that is the whole point of keeping the two clocks apart.
    """
    lag_hours = max(0.0, (received_at - observation_time).total_seconds() / 3600)
    if lag_hours <= policy.telemetry_fresh_hours:
        return "fresh", lag_hours
    if lag_hours <= policy.telemetry_stale_hours:
        return "stale", lag_hours
    return "late", lag_hours


def _escalate(value: float, threshold: float) -> str:
    return "critical" if value > 2 * threshold else "warning"


class MonitoringAssessor:
    """Turns one snapshot plus its window into deterministic findings."""

    def assess(
        self,
        *,
        snapshot: MonitoringSnapshot,
        baseline: MonitoringBaseline | None,
        policy: MonitoringPolicy,
        window_executions: tuple[str, ...] = (),
        previous_observation_time: datetime | None = None,
        expectation_overdue: bool = False,
    ) -> tuple[list[MonitoringFinding], list[dict], list[str]]:
        findings: list[dict] = []
        warnings: list[str] = []
        executions = (*window_executions, snapshot.execution.status)
        failures = sum(1 for status in executions if status != "success")
        failure_rate = _rate(failures, len(executions))
        if failure_rate is not None and failure_rate > policy.max_execution_failure_rate:
            findings.append(
                {
                    "type": "execution_failure",
                    "severity": "critical"
                    if failure_rate >= 1.0
                    else _escalate(failure_rate, policy.max_execution_failure_rate),
                    "evidence": {
                        "failure_rate": failure_rate,
                        "failures": failures,
                        "observations": len(executions),
                        "threshold": policy.max_execution_failure_rate,
                    },
                }
            )

        duration = snapshot.execution.duration_ms
        if duration > policy.latency_review_above_ms:
            findings.append(
                {
                    "type": "latency_degradation",
                    "severity": _escalate(duration, policy.latency_review_above_ms),
                    "evidence": {
                        "duration_ms": duration,
                        "threshold_ms": policy.latency_review_above_ms,
                        "baseline_latency_ms": None if baseline is None else baseline.latency_ms,
                    },
                }
            )

        baseline_coverage = None if baseline is None else baseline.coverage
        current_coverage = snapshot.quality.coverage
        if baseline_coverage is not None and current_coverage is not None:
            drop = baseline_coverage - current_coverage
            if drop > policy.coverage_drop_review:
                findings.append(
                    {
                        "type": "coverage_drop",
                        "severity": _escalate(drop, policy.coverage_drop_review),
                        "evidence": {
                            "drop": drop,
                            "coverage": current_coverage,
                            "baseline_coverage": baseline_coverage,
                            "threshold": policy.coverage_drop_review,
                        },
                    }
                )

        baseline_null = None if baseline is None else baseline.null_rate
        current_null = snapshot.quality.null_rate
        if baseline_null is not None and current_null is not None:
            increase = current_null - baseline_null
            if increase > policy.null_rate_increase_review:
                findings.append(
                    {
                        "type": "null_rate_increase",
                        "severity": _escalate(increase, policy.null_rate_increase_review),
                        "evidence": {
                            "increase": increase,
                            "null_rate": current_null,
                            "baseline_null_rate": baseline_null,
                            "threshold": policy.null_rate_increase_review,
                        },
                    }
                )

        duplicate_rate = snapshot.quality.duplicate_rate
        if duplicate_rate is not None and duplicate_rate > policy.duplicate_rate_review:
            findings.append(
                {
                    "type": "duplicate_increase",
                    "severity": _escalate(duplicate_rate, policy.duplicate_rate_review),
                    "evidence": {
                        "duplicate_rate": duplicate_rate,
                        "threshold": policy.duplicate_rate_review,
                    },
                }
            )

        if snapshot.psi is not None and snapshot.psi > policy.psi_review_above:
            findings.append(
                {
                    "type": "population_shift",
                    "severity": "critical"
                    if snapshot.psi > policy.psi_critical_above
                    else "warning",
                    "evidence": {
                        "psi": snapshot.psi,
                        "review_threshold": policy.psi_review_above,
                        "critical_threshold": policy.psi_critical_above,
                        "epsilon": policy.psi_epsilon,
                    },
                }
            )

        if previous_observation_time is not None:
            gap_hours = (
                snapshot.observation_time - previous_observation_time
            ).total_seconds() / 3600
            if gap_hours > policy.missing_snapshot_hours:
                findings.append(
                    {
                        "type": "missing_monitoring_data",
                        "severity": "warning",
                        "evidence": {
                            "gap_hours": round(gap_hours, 3),
                            "threshold_hours": policy.missing_snapshot_hours,
                        },
                    }
                )
        if snapshot.psi_status == "not_available":
            findings.append(
                {
                    "type": "missing_monitoring_data",
                    "severity": "warning",
                    "evidence": {"reason": "no_monitoring_baseline_distribution"},
                }
            )
        elif snapshot.psi_status == "baseline_mismatch":
            findings.append(
                {
                    "type": "missing_monitoring_data",
                    "severity": "warning",
                    "evidence": {"reason": "distribution_bins_not_aligned"},
                }
            )
            warnings.append("distribution_bins_not_aligned")

        # Phase 8: a stale/late arrival and an unmet expectation are both named.
        # Neither changes what the snapshot says; they qualify when it was said.
        if snapshot.telemetry_freshness == "late":
            findings.append(
                {
                    "type": "missing_monitoring_data",
                    "severity": "warning",
                    "evidence": {
                        "reason": "telemetry_arrived_late",
                        "observation_lag_hours": snapshot.observation_lag_hours,
                        "stale_threshold_hours": policy.telemetry_stale_hours,
                    },
                }
            )
        if expectation_overdue:
            findings.append(
                {
                    "type": "missing_monitoring_data",
                    "severity": "warning",
                    "evidence": {"reason": "monitoring_expectation_overdue"},
                }
            )

        ordered = sorted(findings, key=lambda item: FINDING_ORDER.index(item["type"]))
        return (
            [
                MonitoringFinding(
                    type=item["type"],
                    severity=item["severity"],
                    evidence=item["evidence"],
                    dedup_key=finding_dedup_key(
                        snapshot.deployment_id, item["type"], item["severity"]
                    ),
                )
                for item in _merge_by_dedup_key(ordered)
            ],
            ordered,
            warnings,
        )


def _merge_by_dedup_key(findings: list[dict]) -> list[dict]:
    """One finding per (type, severity), so one snapshot counts as one occurrence.

    Several checks can independently report the same finding type - for example a
    snapshot can have no baseline *and* arrive late. Emitting both would increment
    the alert's occurrence counter twice for a single observation, which would
    misstate how often the condition was actually seen.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in findings:
        grouped.setdefault((item["type"], item["severity"]), []).append(item)
    merged: list[dict] = []
    for (finding_type, severity), items in grouped.items():
        if len(items) == 1:
            merged.append(items[0])
            continue
        merged.append(
            {
                "type": finding_type,
                "severity": severity,
                "evidence": {"reasons": [item["evidence"] for item in items]},
            }
        )
    return sorted(merged, key=lambda item: FINDING_ORDER.index(item["type"]))
