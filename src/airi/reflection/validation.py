import re

from airi.reflection.evidence import ReflectionError

ACTIONS = {
    "window_review": "evaluate_alternative_window",
    "aggregation_review": "study_aggregation",
    "coverage_investigation": "investigate_coverage",
    "null_handling_review": "review_null_handling",
    "deduplication_review": "review_duplicates",
    "source_field_review": "review_source_fields",
    "business_rule_review": "confirm_business_rules",
    "threshold_review": "review_existing_candidates",
    "additional_dataset_validation": "validate_additional_dataset",
    "additional_time_snapshot_validation": "validate_additional_snapshot",
}
UNSAFE = re.compile(
    r"\b(select|insert|update|delete|drop|alter|truncate|create|merge|deploy|production|execute|"
    r"proven|confirmed|stable|winner|best)\b|```|上线|投产|发布策略|执行SQL|已证实|已验证|稳定性很好|指标稳定|最佳指标",
    re.IGNORECASE,
)


def safe_narrative(text):
    # Conservative fail-closed narrative contract: no model-authored numerical facts.
    if UNSAFE.search(text) or re.search(r"\d", text):
        raise ReflectionError("reflection_output_rejected")


class RefinementProposalValidator:
    def validate(self, output, context):
        if len(output.proposals) > context.policy.max_proposals:
            raise ReflectionError("too_many_proposals")
        findings = {f.finding_id: f for f in context.diagnostics}
        known = {r.ref for r in context.references} | set(findings)
        targets = {}
        for e in context.evidence:
            targets.setdefault(e.evaluation.metric_name, set()).add(e.experiment_run_id)
        for text in [output.summary, *output.missing_evidence]:
            safe_narrative(text)
        if len({h.hypothesis_id for h in output.hypotheses}) != len(output.hypotheses):
            raise ReflectionError("duplicate_hypothesis")
        for h in output.hypotheses:
            safe_narrative(h.statement)
            if not re.search(
                r"\b(may|might|could|hypothesis)\b|可能|或许|待验证", h.statement, re.I
            ):
                raise ReflectionError("hypothesis_must_express_uncertainty")
            if not set(h.evidence_refs) <= known:
                raise ReflectionError("unknown_evidence_reference")
        proposals = []
        for p in output.proposals:
            safe_narrative(p.reason)
            safe_narrative(p.validation_question)
            if p.target not in targets or p.action != ACTIONS[p.proposal_type]:
                raise ReflectionError("unsupported_proposal_action")
            if not set(p.evidence_refs) <= known:
                raise ReflectionError("unknown_evidence_reference")
            cited = [findings[r] for r in p.evidence_refs if r in findings]
            if not cited or any(not (set(f.experiment_run_ids) & targets[p.target]) for f in cited):
                raise ReflectionError("proposal_missing_target_evidence")
            params = p.parameters
            if p.proposal_type == "window_review":
                if (
                    not params.candidate_windows
                    or any(
                        type(w) is not int or not 1 <= w <= 365 for w in params.candidate_windows
                    )
                    or params.candidate_refs
                    or params.candidate_aggregations
                ):
                    raise ReflectionError("invalid_window_proposal")
            elif p.proposal_type == "aggregation_review":
                if (
                    not params.candidate_aggregations
                    or params.candidate_windows
                    or params.candidate_refs
                ):
                    raise ReflectionError("invalid_aggregation_proposal")
            elif p.proposal_type == "threshold_review":
                allowed = {
                    r.ref
                    for r in context.references
                    if ":candidate:" in r.ref and r.experiment_run_id in targets[p.target]
                }
                if (
                    not params.candidate_refs
                    or not set(params.candidate_refs) <= allowed
                    or params.candidate_windows
                    or params.candidate_aggregations
                ):
                    raise ReflectionError("invented_threshold")
            elif params.candidate_windows or params.candidate_aggregations or params.candidate_refs:
                raise ReflectionError("unsupported_proposal_parameters")
            priority = "high" if any(f.severity == "warning" for f in cited) else "medium"
            proposals.append(p.model_copy(update={"priority": priority}))
        return output.model_copy(update={"proposals": proposals})
