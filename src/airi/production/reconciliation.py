"""Reconciliation planning - the Phase 8 surface, evolved in place by Phase 9.

Phase 7 detected a registry/runtime disagreement and stopped. Phase 8 went one
step further - plan a recovery, let a human choose, execute it through the same
governed surfaces, then *verify* that the system actually converged.

Phase 9 adds the third state. The plan is no longer "registry vs runtime"; it is
"desired vs registry vs runtime", and a state that already satisfies the desired
state yields a ``no_op`` plan rather than a plan that proposes to fix nothing.
The rules from Phase 8 do not move:

* the plan cannot declare which side is true. It produces the actions that are
  actually available and says why, and a human picks one; and
* convergence is confirmed by re-observing the runtime, never by trusting the
  recovery action's own return value.

The comparison helpers live in :mod:`airi.production.convergence`; this module is
where callers import the builder from, and keeps that import path stable.
"""

from collections.abc import Callable

from airi.production.convergence import (
    ACTION_ORDER,
    available_actions,
    plan_triple,
    recommend,
    three_way_state,
)
from airi.production.models import (
    DeploymentReconciliation,
    DesiredRuntimeState,
    ProductionDeployment,
    ProductionDeploymentEvidence,
    ReconciliationPlan,
)
from airi.registry.models import MetricVersion

__all__ = [
    "ACTION_ORDER",
    "available_actions",
    "build_reconciliation_plan",
    "convergence_status",
    "plan_triple",
    "recommend",
    "three_way_state",
]

DECISION_TO_ACTION = {
    "align_runtime_to_registry": "registry_to_runtime",
    "align_registry_to_runtime": "runtime_to_registry",
    "keep_mismatch_for_investigation": "manual_investigation",
}


def build_reconciliation_plan(
    *,
    deployment: ProductionDeployment,
    reconciliation: DeploymentReconciliation,
    evidence: ProductionDeploymentEvidence | None,
    latest_deployment_review_id: str | None,
    rollback_review_ids: list[str],
    alert_ids: list[str],
    version_lookup: Callable[[str], MetricVersion | None],
    adapter_name: str,
    adapter_authoritative: bool,
    adapter_supports_recovery: bool,
    created_by: str,
    desired: DesiredRuntimeState | None = None,
) -> ReconciliationPlan:
    """Enumerate the recoveries that are genuinely available for this mismatch.

    ``version_lookup`` resolves a runtime-reported version id against the
    registry; a runtime running something the registry never produced is not
    something this system may "align" to.
    """
    registry_active = reconciliation.registry_active_version_id
    runtime_active = reconciliation.production_active_version_id
    runtime_version = version_lookup(runtime_active) if runtime_active else None
    desired_active = None if desired is None else desired.desired_metric_version_id

    state, satisfied, possible, recommended, rationale = plan_triple(
        desired=desired,
        reconciliation=reconciliation,
        deployment_version_id=deployment.metric_version_id,
        version_lookup=version_lookup,
        adapter_authoritative=adapter_authoritative,
        adapter_supports_recovery=adapter_supports_recovery,
    )
    # Phase 8 callers read `verdict`; "consistent" maps onto "converged" so the
    # vocabulary is the converging one without changing what is reported.
    verdict = "converged" if reconciliation.status == "consistent" else state

    return ReconciliationPlan(
        deployment_id=deployment.deployment_id,
        metric_definition_id=deployment.metric_definition_id,
        metric_version_id=deployment.metric_version_id,
        metric_key=deployment.metric_key,
        verdict=verdict,
        registry_active_version_id=registry_active,
        registry_active_version=reconciliation.registry_active_version,
        production_runtime_state=reconciliation.production_runtime_state,
        production_active_version_id=runtime_active,
        production_active_version=None if runtime_version is None else runtime_version.version,
        environment_id=deployment.environment_id,
        environment_fingerprint_hash=deployment.environment_fingerprint_hash,
        runtime_identity=reconciliation.runtime_identity,
        provider_application_id=reconciliation.provider_application_id,
        deployment_evidence_id=None if evidence is None else evidence.deployment_evidence_id,
        latest_deployment_review_id=latest_deployment_review_id,
        rollback_review_ids=list(rollback_review_ids),
        alert_ids=list(alert_ids),
        possible_actions=possible,
        recommended_action=recommended,
        rationale=rationale,
        created_by=created_by,
        desired_state_id=None if desired is None else desired.desired_state_id,
        desired_metric_version_id=desired_active,
        desired_version=None if desired is None else desired.desired_version,
        desired_source=None if desired is None else desired.source,
        convergence_state=state,
        target_already_satisfied=satisfied,
    )


def convergence_status(after: DeploymentReconciliation) -> str:
    """A recovery is only verified by re-observation, never by its own return value."""
    if after.status == "consistent":
        return "verified"
    return "still_mismatch" if after.status == "mismatch" else "not_verified"
