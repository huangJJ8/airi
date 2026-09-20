"""Shadow validation: run a new version beside the incumbent on the same input.

A changed value is expected when semantics change. The question is whether the
execution completed, the schema held, no subject was lost and no NULL spike appeared.
"""

from datetime import UTC, datetime
from decimal import Decimal

from airi.evaluation.calculator import quantile, ratio
from airi.registry.models import (
    ReleasePolicy,
    ShadowComparisonResult,
    ShadowReference,
)


def metric_values(rows, metric_name: str):
    """Extract {entity: Decimal | None} from raw execution rows; count duplicate subjects."""
    values, duplicates = {}, 0
    for row in rows:
        entity = row.get("entity_id")
        if entity is None:
            continue
        if entity in values:
            duplicates += 1
            continue
        raw = row.get(metric_name)
        if raw is None:
            values[entity] = None
            continue
        try:
            values[entity] = Decimal(str(raw))
        except (ArithmeticError, ValueError):
            values[entity] = None
    return values, duplicates


def distribution_summary(values: dict) -> dict:
    numeric = sorted(value for value in values.values() if value is not None)
    return {
        "entities": len(values),
        "non_null": len(numeric),
        "null_rate": ratio(len(values) - len(numeric), len(values)),
        "min": numeric[0] if numeric else None,
        "max": numeric[-1] if numeric else None,
        "mean": sum(numeric) / len(numeric) if numeric else None,
        **{f"p{p}": quantile(numeric, p) for p in (50, 90, 95)},
    }


def distribution_delta(before: dict, after: dict) -> dict:
    delta = {}
    for key in ("entities", "non_null", "null_rate", "min", "max", "mean", "p50", "p90", "p95"):
        left, right = before.get(key), after.get(key)
        delta[key] = (
            None
            if left is None or right is None
            else float(Decimal(str(right)) - Decimal(str(left)))
        )
    baseline_p90 = before.get("p90")
    delta["p90_relative_shift"] = (
        None
        if baseline_p90 in (None, 0) or after.get("p90") is None
        else float(
            (Decimal(str(after["p90"])) - Decimal(str(baseline_p90))) / Decimal(str(baseline_p90))
        )
    )
    return delta


class ShadowComparator:
    def compare(
        self,
        *,
        baseline: ShadowReference,
        candidate: ShadowReference,
        baseline_rows,
        candidate_rows,
        dataset_snapshot_id: str,
        anchor_time: datetime,
        policy: ReleasePolicy,
        baseline_duplicates: int = 0,
        candidate_duplicates: int = 0,
    ) -> ShadowComparisonResult:
        left, right = (
            metric_values(baseline_rows, baseline.metric_name),
            metric_values(candidate_rows, candidate.metric_name),
        )
        (left_values, left_duplicates), (right_values, right_duplicates) = left, right
        matched = left_values.keys() & right_values.keys()
        equal = changed = new_null = resolved_null = 0
        for entity in matched:
            before, after = left_values[entity], right_values[entity]
            if after is None and before is not None:
                new_null += 1
            elif before is None and after is not None:
                resolved_null += 1
            elif before == after:
                equal += 1
            else:
                changed += 1
        duplicates = left_duplicates + right_duplicates + baseline_duplicates + candidate_duplicates
        loss_fraction = (
            ratio(len(left_values.keys() - right_values.keys()), len(left_values))
            if left_values
            else None
        )
        null_fraction = ratio(new_null, len(matched)) if matched else None
        delta = distribution_delta(
            distribution_summary(left_values), distribution_summary(right_values)
        )
        warnings = []
        if duplicates:
            warnings.append("duplicate_entity_in_execution")
        if not matched:
            warnings.append("no_matched_entities")
        if loss_fraction is not None and loss_fraction > policy.max_entity_loss_fraction:
            warnings.append("subject_loss_exceeds_policy")
        if null_fraction is not None and null_fraction > policy.max_new_null_fraction:
            warnings.append("new_null_exceeds_policy")
        if delta["p90_relative_shift"] is not None and (
            abs(delta["p90_relative_shift"]) > policy.max_distribution_shift
        ):
            # Expected when semantics change; recorded as evidence, never as an error.
            warnings.append("distribution_shifted_as_expected_for_semantic_change")
        failed = (
            duplicates
            or "subject_loss_exceeds_policy" in warnings
            or ("new_null_exceeds_policy" in warnings)
        )
        status = (
            "inconclusive"
            if not matched
            else "failed"
            if failed
            else "passed_with_warnings"
            if warnings
            else "passed"
        )
        return ShadowComparisonResult(
            status=status,
            baseline=baseline,
            candidate=candidate,
            dataset_snapshot_id=dataset_snapshot_id,
            anchor_time=anchor_time,
            sample_count=len(right_values),
            matched_entities=len(matched),
            baseline_only=len(left_values.keys() - right_values.keys()),
            candidate_only=len(right_values.keys() - left_values.keys()),
            value_equal=equal,
            value_changed=changed,
            new_null=new_null,
            resolved_null=resolved_null,
            difference_rate=ratio(changed + new_null + resolved_null, len(matched))
            if matched
            else None,
            entity_loss_fraction=loss_fraction,
            new_null_fraction=null_fraction,
            distribution_delta=delta,
            warnings=warnings,
        )

    def failed(
        self, reason: str, *, baseline: ShadowReference, candidate: ShadowReference
    ) -> ShadowComparisonResult:
        now = datetime.now(UTC)
        return ShadowComparisonResult(
            status="failed",
            baseline=baseline,
            candidate=candidate,
            dataset_snapshot_id="unavailable",
            anchor_time=now,
            sample_count=0,
            matched_entities=0,
            baseline_only=0,
            candidate_only=0,
            value_equal=0,
            value_changed=0,
            new_null=0,
            resolved_null=0,
            difference_rate=None,
            entity_loss_fraction=None,
            new_null_fraction=None,
            warnings=[reason],
        )


def shadow_findings(result: ShadowComparisonResult, policy: ReleasePolicy) -> list[dict]:
    findings = []
    if "duplicate_entity_in_execution" in result.warnings:
        findings.append({"type": "shadow_duplicate_entity"})
    if result.entity_loss_fraction is not None and (
        result.entity_loss_fraction > policy.max_entity_loss_fraction
    ):
        findings.append({"type": "shadow_entity_loss"})
    if result.new_null_fraction is not None and (
        result.new_null_fraction > policy.max_new_null_fraction
    ):
        findings.append({"type": "shadow_null_increase"})
    if result.matched_entities == 0:
        findings.append({"type": "shadow_no_matched_entities"})
    if "execution_failed" in result.warnings:
        findings.append({"type": "shadow_execution_failed"})
    return findings
