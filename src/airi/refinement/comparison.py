from decimal import Decimal

from airi.refinement.models import MetricComparisonResult
from airi.refinement.transform import RefinementError
from airi.reflection.evidence import ComparisonCompatibilityGuard, NotComparable


def delta(before, after):
    return (
        None
        if before is None or after is None
        else float(Decimal(str(after)) - Decimal(str(before)))
    )


class BaselineCandidateComparison:
    def compare(self, baseline, candidate, policy):
        try:
            ComparisonCompatibilityGuard().validate([baseline, candidate])
        except NotComparable as exc:
            raise RefinementError("comparison_invalid") from exc
        a, b = baseline.evaluation, candidate.evaluation
        coverage, ks, iv = (
            delta(a.coverage, b.coverage),
            delta(a.ks.value, b.ks.value),
            delta(a.iv, b.iv),
        )
        reasons = []
        insufficient = (
            coverage is None
            or ks is None
            or min(a.labeled_sample, b.labeled_sample) < policy.min_labeled_samples
            or not all((a.good_count, a.bad_count, b.good_count, b.bad_count))
            or len(candidate.warnings) > policy.max_experiment_warnings
        )
        outcome = "inconclusive"
        if insufficient:
            reasons.append("Unavailable separation, insufficient sample or warning budget exceeded")
        else:
            c = (
                1
                if coverage > policy.coverage_material_delta
                else -1
                if coverage < -policy.coverage_material_delta
                else 0
            )
            k = 1 if ks > policy.ks_material_delta else -1 if ks < -policy.ks_material_delta else 0
            outcome = (
                "mixed"
                if c * k < 0
                else "improved"
                if max(c, k) > 0
                else "worse"
                if min(c, k) < 0
                else "inconclusive"
            )
            reasons.append(
                f"Policy material changes: coverage={c}, KS={k}; IV is contextual evidence only"
            )
        matches = []
        for x in a.threshold_candidates:
            for y in b.threshold_candidates:
                sources = sorted(set(x.sources) & set(y.sources))
                if sources and x.operator == y.operator:
                    matches.append(
                        {
                            "sources": sources,
                            "operator": x.operator,
                            "baseline_threshold": str(x.threshold),
                            "candidate_threshold": str(y.threshold),
                            "same_numeric_threshold": x.threshold == y.threshold,
                            **{
                                f"{field}_delta": delta(getattr(x, field), getattr(y, field))
                                for field in ("hit_rate", "precision", "recall", "lift")
                            },
                        }
                    )
        return MetricComparisonResult(
            baseline=a,
            candidate=b,
            coverage_delta=coverage,
            ks_delta=ks,
            iv_delta=iv,
            threshold_matches=matches,
            outcome=outcome,
            reasons=reasons,
        )
