"""Deterministic release diagnostics. Multi-evidence; never a single KS/PSI cut-off."""

from airi.registry.models import ReleasePolicy
from airi.registry.shadow import shadow_findings

BLOCKING = frozenset(
    {
        "staging_execution_failed",
        "staging_schema_invalid",
        "staging_result_truncated",
        "shadow_execution_failed",
        "shadow_duplicate_entity",
        "shadow_entity_loss",
        "shadow_null_increase",
    }
)
SOFT = frozenset(
    {
        "staging_inconclusive",
        "staging_empty_result",
        "shadow_missing",
        "shadow_no_matched_entities",
    }
)


class ReleaseDiagnostics:
    """Turns staging execution plus shadow comparison into one release eligibility."""

    def assess(self, staging, shadow, policy: ReleasePolicy, *, synthetic_data: bool):
        findings = []
        warnings = list(staging.warnings)
        missing = []
        if staging.status == "failed":
            findings.append({"type": "staging_execution_failed"})
        if staging.status == "inconclusive":
            findings.append({"type": "staging_inconclusive"})
        if not staging.schema_ok:
            findings.append({"type": "staging_schema_invalid"})
        if staging.truncated:
            findings.append({"type": "staging_result_truncated"})
        if staging.row_count == 0:
            findings.append({"type": "staging_empty_result"})
        if shadow is None:
            if policy.require_shadow_validation:
                findings.append({"type": "shadow_missing"})
                missing.append("shadow validation not performed")
        else:
            findings.extend(shadow_findings(shadow, policy))
            warnings.extend(shadow.warnings)
        kinds = {item["type"] for item in findings}
        if kinds & BLOCKING:
            eligibility = "not_eligible"
        elif kinds & SOFT:
            eligibility = "need_more_evidence"
        else:
            eligibility = "eligible_for_release_review"
        if synthetic_data:
            # Synthetic evidence can never exceed a research release review.
            warnings.append(
                "synthetic evidence only; research release demonstration, not a real release"
            )
            missing.extend(["real Spark not verified", "real production traffic not verified"])
        warnings = sorted(set(warnings))
        # The report status mirrors the eligibility gate so a report can never claim
        # "passed_with_warnings" while also demanding more evidence.
        status = (
            "failed"
            if eligibility == "not_eligible"
            else "inconclusive"
            if eligibility == "need_more_evidence"
            else "passed_with_warnings"
            if findings or warnings
            else "passed"
        )
        return findings, eligibility, status, warnings, sorted(set(missing))
