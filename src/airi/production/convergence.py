"""Phase 9 convergence: desired vs registry vs runtime.

Phase 8 compared two states (registry, runtime) and could therefore only ever say
"these disagree". It could not say whether anything *needed doing*, which is why a
rollback whose target had already been reached by a human reconciliation still
reported a conflict.

Phase 9 adds the third state - :class:`DesiredRuntimeState` - and answers three
questions instead of two:

    desired  = what a governance decision says the runtime should be
    registry = what the registry pointer says
    runtime  = what the provider reports

The rules that never move:

* a conflict is not a failure. A compare-and-set can fail precisely *because* the
  goal was already met, and that is a success with nothing to do;
* ``no_op`` is a first-class result, not ``None``. "Nothing needed doing" is a
  proven governance outcome and has to be recordable;
* nothing here executes anything, and nothing here may declare which side is
  true. It names the actions that exist and why.

The plan builder itself lives in :mod:`airi.production.reconciliation`, which is
where Phase 8 put it and where callers still import it from. This module holds the
pure comparison helpers it now delegates to.
"""

from collections.abc import Callable

from airi.production.models import (
    PROTECTED_REQUIREMENTS,
    ConflictDiagnosis,
    ConvergenceAction,
    ConvergenceState,
    DeploymentReconciliation,
    DesiredRuntimeState,
    ProductionConvergence,
    ProductionDeployment,
    ProductionHealthStatus,
    RuntimeState,
)
from airi.registry.models import MetricVersion

__all__ = [
    "ACTION_ORDER",
    "PROTECTED_REQUIREMENTS",
    "availability",
    "available_actions",
    "build_convergence",
    "convergence_verdict",
    "diagnose_conflict",
    "health_of",
    "observed_runtime_state",
    "plan_triple",
    "recommend",
    "three_way_state",
]

# Fixed order so the same evidence always yields the same plan.
ACTION_ORDER: tuple[ConvergenceAction, ...] = (
    "no_op",
    "registry_to_runtime",
    "runtime_to_registry",
    "manual_investigation",
)

# ``PROTECTED_REQUIREMENTS`` is re-exported from the vocabulary module so the gate,
# the override model and these helpers all read one list (§44/§71).


# ------------------------------------------------------------------ diagnosis


def diagnose_conflict(
    *,
    expected: str | None,
    actual: str | None,
    desired: str | None,
    runtime_state: RuntimeState = "active",
) -> ConflictDiagnosis:
    """Explain a failed expectation instead of merely raising about it.

    The four categories exist because the four situations need different human
    responses: nothing to do, re-read and retry, escalate, or fix the runtime.
    """
    if runtime_state == "unknown":
        return ConflictDiagnosis(
            expected=expected,
            actual=actual,
            desired=desired,
            category="runtime_unknown",
            safe_to_retry=False,
            requires_manual_review=True,
            detail=(
                "the production runtime did not report a state, so no compare-and-set "
                "outcome can be interpreted"
            ),
        )
    if desired is not None and actual == desired:
        return ConflictDiagnosis(
            expected=expected,
            actual=actual,
            desired=desired,
            category="target_already_satisfied",
            safe_to_retry=False,
            requires_manual_review=False,
            detail=(
                "the registry already points at the desired version; the failed expectation "
                "was stale, not the state. No recovery is required."
            ),
        )
    if actual != expected:
        return ConflictDiagnosis(
            expected=expected,
            actual=actual,
            desired=desired,
            category="unexpected_external_change",
            safe_to_retry=False,
            requires_manual_review=True,
            detail=(
                "the active version changed to something that is neither the expected nor "
                "the desired version; an external actor moved it"
            ),
        )
    return ConflictDiagnosis(
        expected=expected,
        actual=actual,
        desired=desired,
        category="stale_expected_state",
        safe_to_retry=True,
        requires_manual_review=False,
        detail=(
            "the expected version still holds but the pointer move did not apply; the "
            "expectation can be re-read and the operation retried"
        ),
    )


# ------------------------------------------------------------- three-way model


def three_way_state(
    *,
    runtime_state: RuntimeState,
    desired_version_id: str | None,
    registry_version_id: str | None,
    runtime_version_id: str | None,
) -> tuple[ConvergenceState, bool]:
    """Return ``(convergence_state, target_already_satisfied)``.

    With a desired state the goal is exact: all three must name the same version.
    Without one - a Phase 7/8 deployment that never established one - the goal
    degrades to "registry and runtime agree", which is all that was ever asked.
    """
    if runtime_state == "unknown":
        return "unknown", False
    if desired_version_id is None:
        satisfied = registry_version_id == runtime_version_id
    else:
        satisfied = desired_version_id == registry_version_id == runtime_version_id
    return ("converged" if satisfied else "mismatch"), satisfied


def observed_runtime_state(reconciliation: DeploymentReconciliation) -> RuntimeState:
    """The runtime state a comparison is allowed to use.

    ``DeploymentReconciliation.status == "unknown"`` records the outcome "the
    runtime never answered for itself". A raw ``production_runtime_state`` that
    still reads ``active`` is then a stale field rather than evidence, and planning
    a recovery from a reading nobody actually obtained is how a system talks itself
    into touching production it cannot see.
    """
    if reconciliation.status == "unknown":
        return "unknown"
    return reconciliation.production_runtime_state


def availability(
    *,
    state: ConvergenceState,
    registry_version_id: str | None,
    runtime_version_id: str | None,
    desired_version_id: str | None,
    deployment_version_id: str,
    runtime_version_known: bool,
    adapter_authoritative: bool,
    adapter_supports_recovery: bool,
) -> dict[ConvergenceAction, bool]:
    """Which of the four actions this evidence supports. Order-free and pure."""
    if state == "unknown":
        return {
            "no_op": False,
            "registry_to_runtime": False,
            "runtime_to_registry": False,
            "manual_investigation": True,
        }
    if state == "converged":
        return {
            "no_op": True,
            "registry_to_runtime": False,
            "runtime_to_registry": False,
            "manual_investigation": False,
        }

    # What the runtime is supposed to end up at. Without a desired state the only
    # target this system can legitimately push is the package the deployment holds.
    runtime_target = desired_version_id if desired_version_id is not None else deployment_version_id
    registry_to_runtime = (
        registry_version_id is not None
        and registry_version_id == runtime_target
        and adapter_authoritative
        and adapter_supports_recovery
    )
    # A version the registry never produced cannot be adopted as its pointer. This
    # stays available even when it disagrees with the desired state: "the runtime
    # turns out to be right" is a governance decision a human is allowed to make,
    # and the plan is forbidden from pre-empting it (§13).
    runtime_to_registry = runtime_version_id is not None and runtime_version_known
    return {
        "no_op": False,
        "registry_to_runtime": registry_to_runtime,
        "runtime_to_registry": runtime_to_registry,
        "manual_investigation": True,
    }


def available_actions(
    *,
    state: ConvergenceState,
    registry_version_id: str | None,
    runtime_version_id: str | None,
    desired_version_id: str | None,
    deployment_version_id: str,
    runtime_version_known: bool,
    adapter_authoritative: bool,
    adapter_supports_recovery: bool,
) -> list[ConvergenceAction]:
    """§14: only four actions exist, and only when they are genuinely available."""
    flags = availability(
        state=state,
        registry_version_id=registry_version_id,
        runtime_version_id=runtime_version_id,
        desired_version_id=desired_version_id,
        deployment_version_id=deployment_version_id,
        runtime_version_known=runtime_version_known,
        adapter_authoritative=adapter_authoritative,
        adapter_supports_recovery=adapter_supports_recovery,
    )
    return [action for action in ACTION_ORDER if flags[action]]


def recommend(
    *,
    state: ConvergenceState,
    registry_to_runtime_available: bool,
    runtime_to_registry_available: bool,
) -> tuple[ConvergenceAction, str]:
    """Pick an order of preference. A recommendation is never a verdict."""
    if state == "converged":
        return (
            "no_op",
            "The desired state is already satisfied by the registry pointer and by the "
            "production runtime. Nothing needs to be executed, and this no-op is itself "
            "the recorded governance outcome.",
        )
    if state == "unknown":
        return (
            "manual_investigation",
            "The production runtime did not report a state, so neither the registry "
            "nor the runtime can be compared. Investigate the runtime before choosing.",
        )
    if registry_to_runtime_available:
        return (
            "registry_to_runtime",
            "The registry points at the version this deployment holds a package for, so "
            "the runtime can be re-aligned to it through the production adapter. This is a "
            "recommendation, not a verdict: the registry is not automatically the truth.",
        )
    if runtime_to_registry_available:
        return (
            "runtime_to_registry",
            "The registry holds no package for its own active version, so the runtime cannot "
            "be re-aligned to it. The runtime reports a version the registry knows about, so "
            "the registry pointer can be moved to match. A human must still decide.",
        )
    return (
        "manual_investigation",
        "Neither side can be aligned safely: the runtime reports a version the registry "
        "has never produced, or no recovery channel is available.",
    )


def plan_triple(
    *,
    desired: DesiredRuntimeState | None,
    reconciliation,
    deployment_version_id: str,
    version_lookup: Callable[[str], MetricVersion | None],
    adapter_authoritative: bool,
    adapter_supports_recovery: bool,
) -> tuple[ConvergenceState, bool, list[ConvergenceAction], ConvergenceAction, str]:
    """One call for the whole comparison, so both surfaces compute it identically."""
    registry_active = reconciliation.registry_active_version_id
    runtime_active = reconciliation.production_active_version_id
    desired_active = None if desired is None else desired.desired_metric_version_id
    runtime_version = version_lookup(runtime_active) if runtime_active else None

    state, satisfied = three_way_state(
        runtime_state=observed_runtime_state(reconciliation),
        desired_version_id=desired_active,
        registry_version_id=registry_active,
        runtime_version_id=runtime_active,
    )
    possible = available_actions(
        state=state,
        registry_version_id=registry_active,
        runtime_version_id=runtime_active,
        desired_version_id=desired_active,
        deployment_version_id=deployment_version_id,
        runtime_version_known=runtime_version is not None,
        adapter_authoritative=adapter_authoritative,
        adapter_supports_recovery=adapter_supports_recovery,
    )
    action, rationale = recommend(
        state=state,
        registry_to_runtime_available="registry_to_runtime" in possible,
        runtime_to_registry_available="runtime_to_registry" in possible,
    )
    return state, satisfied, possible, action, rationale


def build_convergence(
    *,
    deployment: ProductionDeployment,
    reconciliation,
    desired: DesiredRuntimeState | None,
    version_lookup: Callable[[str], MetricVersion | None],
    adapter_authoritative: bool,
    adapter_supports_recovery: bool,
    conflict: ConflictDiagnosis | None = None,
) -> ProductionConvergence:
    """The read-only three-way view. Observes; never repairs."""
    state, satisfied, possible, action, rationale = plan_triple(
        desired=desired,
        reconciliation=reconciliation,
        deployment_version_id=deployment.metric_version_id,
        version_lookup=version_lookup,
        adapter_authoritative=adapter_authoritative,
        adapter_supports_recovery=adapter_supports_recovery,
    )
    registry_active = reconciliation.registry_active_version_id
    runtime_active = reconciliation.production_active_version_id
    registry_version = version_lookup(registry_active) if registry_active else None
    runtime_version = version_lookup(runtime_active) if runtime_active else None
    return ProductionConvergence(
        deployment_id=deployment.deployment_id,
        metric_definition_id=deployment.metric_definition_id,
        metric_version_id=deployment.metric_version_id,
        metric_key=deployment.metric_key,
        environment_id=deployment.environment_id,
        desired_state_id=None if desired is None else desired.desired_state_id,
        desired_metric_version_id=None if desired is None else desired.desired_metric_version_id,
        desired_version=None if desired is None else desired.desired_version,
        desired_source=None if desired is None else desired.source,
        registry_active_version_id=registry_active,
        registry_active_version=None if registry_version is None else registry_version.version,
        runtime_active_version_id=runtime_active,
        runtime_active_version=None if runtime_version is None else runtime_version.version,
        runtime_state=observed_runtime_state(reconciliation),
        runtime_identity=reconciliation.runtime_identity,
        environment_fingerprint_hash=reconciliation.environment_fingerprint_hash,
        convergence_state=state,
        target_already_satisfied=satisfied,
        conflict=conflict,
        possible_actions=possible,
        recommended_action=action,
        rationale=rationale,
    )


def convergence_verdict(*, state: ConvergenceState, executed: bool) -> str:
    """§16: the five outcomes, kept separate from the execution status."""
    if state == "converged":
        return "converged" if executed else "already_converged"
    if state == "unknown":
        return "not_verified"
    return "still_mismatch" if executed else "manual_intervention_required"


def health_of(
    *,
    workflow_status: str,
    runtime_state: RuntimeState,
    convergence: ConvergenceState,
) -> tuple[ProductionHealthStatus, list[str]]:
    """§53: an aggregate view only. It never authorises anything by itself."""
    reasons: list[str] = []
    if workflow_status == "failed":
        return "unknown", ["the deployment workflow failed; production health is unknown"]
    if convergence == "unknown" or runtime_state == "unknown":
        return "unknown", ["the runtime did not report a state"]
    if convergence == "mismatch":
        reasons.append("the registry and the runtime disagree with the desired state")
    if runtime_state in ("failed", "rolled_back", "mismatch"):
        reasons.append(f"the runtime reports {runtime_state}")
    if workflow_status in (
        "rollback_reconciliation_required",
        "reconciliation_verification_required",
    ):
        reasons.append(f"the workflow is waiting on a named recovery: {workflow_status}")
    return ("degraded" if reasons else "healthy"), reasons
