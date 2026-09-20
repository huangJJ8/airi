"""Refinement is only defined for the invoice amount family.

Window review rewrites a baseline metric into a longer window and then proves
that the rewrite changed nothing but the window. That proof is expressed in the
baseline's own scenario vocabulary, so a baseline from another scenario has no
window-review capability at all: it is reported as ``proposal_manual_only``
rather than being force-fitted into invoice semantics.
"""

from airi.approvals.artifacts import canonical_hash
from airi.core.exceptions import AIRIError
from airi.metric_ir.invoice import validate_invoice_metric
from airi.metric_ir.models import MetricIR
from airi.metric_ir.semantics import scenario_for_metric, validate_for_scenario
from airi.refinement.models import ChangedField, MetricSemanticDiff

WINDOW_REVIEW_SCENARIO = "invoice_risk"
WINDOW_REVIEW_CANDIDATES = ("invoice_amount_60d", "invoice_amount_90d")


class RefinementError(AIRIError):
    status_code = 409
    code = "refinement_invalid"

    def __init__(self, category):
        super().__init__(category)
        self.code = category


def supports_window_review(baseline) -> bool:
    """Only the invoice amount baseline declares a window-review capability."""
    return isinstance(baseline, MetricIR) and baseline.name == "invoice_amount_30d"


def is_window_review_candidate(metric) -> bool:
    """A 60/90-day window rewrite is the only declared candidate family."""
    return isinstance(metric, MetricIR) and metric.name in WINDOW_REVIEW_CANDIDATES


def scenario_for_candidate(metric) -> str | None:
    """The scenario family a declared candidate belongs to, if it is one."""
    return WINDOW_REVIEW_SCENARIO if is_window_review_candidate(metric) else None


def validate_candidate_metric(metric):
    metric = MetricIR.model_validate(metric.model_dump())
    if (
        metric.window is None
        or metric.window.size not in (60, 90)
        or metric.name != f"invoice_amount_{metric.window.size}d"
        or metric.display_name != f"近{metric.window.size}天企业开票金额"
    ):
        raise RefinementError("candidate_semantic_diff_invalid")
    normalized = metric.model_dump()
    normalized.update(name="invoice_amount_30d", display_name="近30天企业开票金额")
    normalized["window"]["size"] = 30
    validate_invoice_metric(MetricIR.model_validate(normalized))
    return metric


def validate_executable_metric(metric):
    """Executability spans the declared candidates plus every scenario declaration."""
    if is_window_review_candidate(metric):
        return validate_candidate_metric(metric)
    return validate_for_scenario(metric, scenario_for_metric(metric))


class CandidateIRTransformer:
    def transform_window(self, baseline, days):
        if not isinstance(baseline, MetricIR) or baseline.name != "invoice_amount_30d":
            raise RefinementError("proposal_manual_only")
        validate_invoice_metric(baseline)
        if type(days) is not int or days not in (60, 90):
            raise RefinementError("proposal_parameter_invalid")
        data = baseline.model_dump()
        data.update(
            name=f"invoice_amount_{days}d",
            display_name=f"近{days}天企业开票金额",
            description=f"统计企业近{days}天开票金额之和",
        )
        data["window"]["size"] = days
        candidate = validate_candidate_metric(MetricIR.model_validate(data))
        return candidate, self.semantic_diff(baseline, candidate)

    def semantic_diff(self, baseline, candidate):
        before, after = baseline.model_dump(), candidate.model_dump()
        changes = [
            ChangedField(field=k, before=before[k], after=after[k])
            for k in ("name", "display_name", "description")
            if before[k] != after[k]
        ]
        changes.append(
            ChangedField(
                field="window.size", before=baseline.window.size, after=candidate.window.size
            )
        )
        for k in ("name", "display_name", "description"):
            after[k] = before[k]
        after["window"]["size"] = before["window"]["size"]
        if canonical_hash(before) != canonical_hash(after):
            raise RefinementError("candidate_semantic_diff_invalid")
        validate_candidate_metric(candidate)
        return MetricSemanticDiff(changed_fields=changes)


class ProposalCapabilityMatrix:
    def windows(self, proposal, baseline):
        if proposal.proposal_type != "window_review" or not supports_window_review(baseline):
            return []
        return sorted(set(proposal.parameters.candidate_windows) & {60, 90})
