"""Phase 9 verification enforcement - a report becomes a gate.

Phase 8 produced a per-section :class:`~airi.production.models.ProductionVerificationReport`
and nothing consulted it. A report that cannot refuse anything is documentation,
not governance, so Phase 9 puts the report in front of the provider call:

    verification gate  ->  deploy

rather than

    deploy  ->  verification.

Three rules keep the gate honest rather than merely strict:

* **action-aware** (§42). A rollback happens when production is *already* broken.
  Requiring a verified telemetry source before a rollback would block the one
  action that exists to stop the bleeding, so the required set depends on the
  action;
* **never a single rule for every environment** (§37/§38). A synthetic or
  non-production environment records the decision and does not block. A real
  production environment enforces;
* **an override waives evidence, never integrity** (§44/§71). Package integrity,
  artifact integrity, metric version identity and actor authentication are checked
  before the override is even considered, and are re-checked afterwards.
"""

from airi.production.convergence import PROTECTED_REQUIREMENTS
from airi.production.models import (
    DEFAULT_ENFORCEMENT_POLICY_VERSION,
    EmergencyOverride,
    GateAction,
    ProductionEnvironmentProfile,
    ProductionVerificationReport,
    VerificationEnforcementMode,
    VerificationGateDecision,
)

# §42: what each action genuinely needs before it may touch production.
GATE_ACTION_REQUIREMENTS: dict[GateAction, tuple[str, ...]] = {
    "deploy": ("environment", "identity", "adapter", "runtime_identity", "shadow"),
    # Deliberately without `monitoring_source`: on a first deploy there is no
    # production telemetry yet (§40), and an emergency rollback must not be
    # blocked by the pipeline it is trying to rescue (§41).
    "rollback": ("environment", "identity", "adapter"),
    "reconciliation": ("environment", "identity", "adapter", "runtime_identity"),
}

# Capabilities that are not report sections. `rollback_capability` is the
# adapter's declaration, never an assumption.
GATE_ACTION_CAPABILITIES: dict[GateAction, tuple[str, ...]] = {
    "deploy": (),
    "rollback": ("rollback_capability",),
    "reconciliation": (),
}

AUTO = "auto"


def resolve_enforcement_mode(
    *, profile: ProductionEnvironmentProfile, configured: str = AUTO
) -> VerificationEnforcementMode:
    """§38: non-production reports, production enforces.

    A synthetic profile is never enforced: a synthetic environment cannot produce
    real evidence, so enforcing there would only ever teach people to bypass it.
    An operator may still pin the mode explicitly.
    """
    if configured in ("report_only", "enforced"):
        return configured
    if profile.synthetic_profile:
        return "report_only"
    if profile.environment_kind != "production":
        return "report_only"
    return "enforced"


def effective_waivers(*, override: EmergencyOverride | None, required: tuple[str, ...]) -> set[str]:
    """The override's waivers, minus anything protected. Defence in depth (§71)."""
    if override is None:
        return set()
    return {name for name in override.waives if name in required} - set(PROTECTED_REQUIREMENTS)


def evaluate_verification_gate(
    *,
    action: GateAction,
    deployment_id: str,
    environment_id: str,
    environment_kind: str,
    profile: ProductionEnvironmentProfile,
    report: ProductionVerificationReport | None,
    capabilities: dict[str, bool],
    integrity_failures: list[str],
    actor_authenticated: bool,
    actor_trusted: bool,
    trust_boundary_configured: bool,
    configured_mode: str = AUTO,
    override: EmergencyOverride | None = None,
    policy_version: str = DEFAULT_ENFORCEMENT_POLICY_VERSION,
    decided_by: str | None = None,
) -> VerificationGateDecision:
    """§46: allow, block, or allow with a recorded override - and say why."""
    mode = resolve_enforcement_mode(profile=profile, configured=configured_mode)
    required = GATE_ACTION_REQUIREMENTS[action]
    sections = {} if report is None else {item.name: item.status for item in report.sections}

    # Protected requirements are evaluated first and answer to nobody.
    protected_failures: list[str] = []
    if not actor_authenticated:
        protected_failures.append("actor_authentication")
    elif trust_boundary_configured and not actor_trusted:
        protected_failures.append("actor_authentication")
    protected_failures.extend(integrity_failures)
    if report is not None and report.metric_version_id and report.deployment_id != deployment_id:
        protected_failures.append("metric_version_identity")

    missing = [name for name in required if sections.get(name) != "verified"]
    missing.extend(name for name in GATE_ACTION_CAPABILITIES[action] if not capabilities.get(name))
    missing = sorted(set(missing))

    if protected_failures:
        return VerificationGateDecision(
            action=action,
            deployment_id=deployment_id,
            environment_id=environment_id,
            environment_kind=environment_kind,
            mode=mode,
            result="blocked",
            required_sections=list(required),
            missing_requirements=sorted(set(missing) | set(protected_failures)),
            integrity_requirements=sorted(protected_failures),
            evidence_refs=_evidence(report, override, capabilities),
            report_id=None if report is None else report.production_verification_run_id,
            policy_version=policy_version,
            override_id=None if override is None else override.emergency_override_id,
            detail=(
                "a protected requirement failed: "
                + ", ".join(sorted(protected_failures))
                + ". No emergency override may waive integrity or authentication."
            ),
            decided_by=decided_by,
        )

    waivers = effective_waivers(override=override, required=required)
    remaining = sorted(name for name in missing if name not in waivers)

    if mode == "report_only":
        return VerificationGateDecision(
            action=action,
            deployment_id=deployment_id,
            environment_id=environment_id,
            environment_kind=environment_kind,
            mode=mode,
            result="allowed",
            required_sections=list(required),
            missing_requirements=missing,
            integrity_requirements=[],
            evidence_refs=_evidence(report, override, capabilities),
            report_id=None if report is None else report.production_verification_run_id,
            policy_version=policy_version,
            override_id=None if override is None else override.emergency_override_id,
            detail=(
                "report_only: the verification result is recorded and does not block this "
                "action in a non-production or synthetic environment"
            ),
            decided_by=decided_by,
        )

    if not remaining:
        waived = sorted(set(missing) & waivers)
        return VerificationGateDecision(
            action=action,
            deployment_id=deployment_id,
            environment_id=environment_id,
            environment_kind=environment_kind,
            mode=mode,
            result="allowed_with_override" if waived else "allowed",
            required_sections=list(required),
            missing_requirements=missing,
            integrity_requirements=[],
            evidence_refs=_evidence(report, override, capabilities),
            report_id=None if report is None else report.production_verification_run_id,
            policy_version=policy_version,
            override_id=None if override is None else override.emergency_override_id,
            detail=(
                "enforced: the required evidence is present"
                + (f"; waived by override: {', '.join(waived)}" if waived else "")
            ),
            decided_by=decided_by,
        )

    return VerificationGateDecision(
        action=action,
        deployment_id=deployment_id,
        environment_id=environment_id,
        environment_kind=environment_kind,
        mode=mode,
        result="blocked",
        required_sections=list(required),
        missing_requirements=remaining,
        integrity_requirements=[],
        evidence_refs=_evidence(report, override, capabilities),
        report_id=None if report is None else report.production_verification_run_id,
        policy_version=policy_version,
        override_id=None if override is None else override.emergency_override_id,
        detail=(
            "enforced: this production action is missing verified evidence for "
            + ", ".join(remaining)
        ),
        decided_by=decided_by,
    )


def _evidence(report, override, capabilities) -> dict:
    return {
        "report_id": None if report is None else report.production_verification_run_id,
        "report_overall": None if report is None else report.overall,
        "report_sections": (
            {} if report is None else {item.name: item.status for item in report.sections}
        ),
        "capabilities": dict(capabilities),
        "override_id": None if override is None else override.emergency_override_id,
        "override_waives": [] if override is None else sorted(override.waives),
    }
