"""Phase 9 telemetry expectation evaluation - deterministic, scheduler-free.

Phase 8 let an operator *declare* when telemetry should arrive. Nothing ever
checked. Phase 9 adds the check, but still does not add a clock: evaluating is an
explicit command that a cron job, Argo workflow or any existing scheduler calls.
AIRI owns the question, not the ticking.

Two findings stay apart, because they are different operational problems:

* ``telemetry_missing`` - the window closed and nothing arrived. The producer is
  silent, and no metric reading exists to argue about;
* ``telemetry_late`` - a reading arrived, but its observation-to-receipt lag
  exceeds policy. The pipeline is slow, and the reading is stale.

Neither is a statement about the metric. A broken transport never explains a
metric, and a metric never explains a broken transport (§29).
"""

from datetime import datetime, timedelta

from airi.approvals.artifacts import canonical_hash
from airi.production.models import (
    DEFAULT_EXPECTATION_POLICY_VERSION,
    MonitoringExpectation,
    MonitoringExpectationStatus,
    MonitoringSnapshot,
    TelemetrySourceHealth,
    TrustedTelemetrySource,
)

# Where a window sits relative to now. `due_soon` exists so that crossing the
# deadline does not flip a pipeline straight from fine to critical (§22), and so
# an external scheduler can pre-warn instead of only post-alarming.
SATISFIED = "satisfied"
DUE_SOON = "due_soon"
OVERDUE = "overdue"
# The expectation has never been met at all - a declaration, not a late delivery.
NO_SNAPSHOT = "no_snapshot"


def expectation_window(
    *,
    expectation: MonitoringExpectation,
    last_observation_time: datetime | None,
) -> tuple[datetime, datetime]:
    """Return ``(arrival_due, grace_until)`` for the window that is open now.

    The window starts where the last reading was taken; before the first reading
    it starts when the expectation was declared. Both bounds move forward with
    every arrival, which is what makes a new window a genuinely new window.
    """
    anchor = last_observation_time or expectation.created_at
    arrival_due = anchor + timedelta(hours=expectation.expected_interval_hours)
    grace_until = arrival_due + timedelta(hours=expectation.grace_hours)
    return arrival_due, grace_until


def expectation_window_key(
    *,
    deployment_id: str,
    monitoring_expectation_id: str,
    last_observation_time: datetime | None,
    arrival_due: datetime,
) -> str:
    """A stable id for one missed window.

    Re-evaluating the same window must be idempotent; once a reading arrives the
    anchor moves and the key changes, so the next missed window opens its own
    alert instead of silently incrementing the previous one's counter.
    """
    return canonical_hash(
        {
            "deployment_id": deployment_id,
            "monitoring_expectation_id": monitoring_expectation_id,
            "last_observation_time": (
                None if last_observation_time is None else last_observation_time.isoformat()
            ),
            "arrival_due": arrival_due.isoformat(),
        }
    )


def telemetry_finding_dedup_key(*, deployment_id: str, finding: str, window_key: str) -> str:
    """One alert per deployment/finding/window. Repetition of a window increments."""
    return canonical_hash({"deployment_id": deployment_id, "type": finding, "window": window_key})


def evaluate_expectation(
    *,
    expectation: MonitoringExpectation,
    last_snapshot: MonitoringSnapshot | None,
    now: datetime,
    snapshot_count: int | None = None,
    policy_version: str = DEFAULT_EXPECTATION_POLICY_VERSION,
) -> MonitoringExpectationStatus:
    """Compare the latest observation against a declared expectation.

    Pure: it reads two facts and returns a verdict. It opens no alert and changes
    no state - an alert is a separate, explicit decision (§24).
    """
    last_observation_time = None if last_snapshot is None else last_snapshot.observation_time
    arrival_due, grace_until = expectation_window(
        expectation=expectation, last_observation_time=last_observation_time
    )
    window_key = expectation_window_key(
        deployment_id=expectation.deployment_id,
        monitoring_expectation_id=expectation.monitoring_expectation_id,
        last_observation_time=last_observation_time,
        arrival_due=arrival_due,
    )
    count = snapshot_count if snapshot_count is not None else (0 if last_snapshot is None else 1)

    if count == 0:
        # A stricter statement than "overdue": the expectation has never been met
        # at all. Phase 8 callers already read this status, and reporting
        # "satisfied" about a window in which nothing has ever arrived would claim
        # a delivery that never happened.
        status = NO_SNAPSHOT
        detail = "no telemetry has ever been observed for this deployment"
    elif now < arrival_due:
        status = SATISFIED
        detail = "telemetry within declared expectation"
    elif now < grace_until:
        status = DUE_SOON
        detail = "the next reading is due; still inside the declared grace window"
    else:
        status = OVERDUE
        detail = "telemetry overdue against declared expectation"

    # The finding is about the *window*, not about the label: a declaration made
    # five minutes ago is not yet evidence that a producer is silent.
    findings: list[str] = []
    if status == OVERDUE or (status == NO_SNAPSHOT and now >= grace_until):
        findings.append("telemetry_missing")
    if last_snapshot is not None and last_snapshot.telemetry_freshness == "late":
        findings.append("telemetry_late")

    hours = (
        None
        if last_observation_time is None
        else max(0.0, (now - last_observation_time).total_seconds() / 3600)
    )
    return MonitoringExpectationStatus(
        deployment_id=expectation.deployment_id,
        monitoring_expectation_id=expectation.monitoring_expectation_id,
        status=status,
        expected_interval_hours=expectation.expected_interval_hours,
        grace_hours=expectation.grace_hours,
        last_observation_time=last_observation_time,
        last_freshness=None if last_snapshot is None else last_snapshot.telemetry_freshness,
        hours_since_last_observation=hours,
        due_at=arrival_due,
        snapshot_count=count,
        detail=detail,
        telemetry_source_id=expectation.telemetry_source_id,
        findings=findings,
        window_key=window_key if findings else None,
        policy_version=policy_version,
    )


def telemetry_source_health(
    *,
    source: TrustedTelemetrySource,
    latest_snapshot: MonitoringSnapshot | None,
    snapshot_count: int,
    overdue_deployment_ids: list[str],
    silent_deployment_ids: list[str],
    observed_at: datetime,
) -> TelemetrySourceHealth:
    """Aggregate a producer's operational state. Never a metric verdict (§29)."""
    if not source.verified:
        status = "unknown"
        detail = (
            "the producer has not been attested, so no health claim can be made about "
            "the pipeline it feeds"
        )
    elif snapshot_count == 0:
        status = "silent"
        detail = "the producer is attested but has never delivered a reading"
    elif overdue_deployment_ids or (
        latest_snapshot is not None and latest_snapshot.telemetry_freshness in ("stale", "late")
    ):
        status = "degraded"
        detail = (
            "the pipeline is delivering late or has missed a declared window; the metric "
            "may still be correct"
        )
    else:
        status = "healthy"
        detail = "the producer is attested and delivering within its declared windows"
    return TelemetrySourceHealth(
        telemetry_source_id=source.telemetry_source_id,
        environment_id=source.environment_id,
        source_system=source.source_system,
        verified=source.verified,
        synthetic=source.synthetic,
        status=status,
        latest_observation_time=(
            None if latest_snapshot is None else latest_snapshot.observation_time
        ),
        latest_received_at=None if latest_snapshot is None else latest_snapshot.received_at,
        latest_freshness=None if latest_snapshot is None else latest_snapshot.telemetry_freshness,
        snapshot_count=snapshot_count,
        overdue_deployment_ids=sorted(overdue_deployment_ids),
        silent_deployment_ids=sorted(silent_deployment_ids),
        detail=detail,
        observed_at=observed_at,
    )
