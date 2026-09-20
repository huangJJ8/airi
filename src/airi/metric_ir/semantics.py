"""Scenario metric semantics: one declaration per scenario, resolved by shape.

The development and testing workflows must never mention a scenario name. Each
scenario declares the validator that decides whether a Metric IR satisfies *its*
declaration, and everything else resolves through this module:

* ``validate_for_scenario`` — the development path, which already knows which
  scenario the caller asked for.
* ``scenario_for_metric`` — the testing path, which only has a stored artifact
  and must recover the scenario whose declaration that IR satisfies. Exactly one
  declaration may accept an IR: two accepting would mean the scenarios are not
  actually distinct, and zero would mean the artifact is not governed by any
  declaration.

Nothing here resolves "latest", and nothing here repairs an IR.
"""

from collections.abc import Callable

from airi.core.exceptions import UnsupportedMetricError
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.enterprise_relation import validate_enterprise_relation_metric
from airi.metric_ir.invoice import validate_invoice_metric
from airi.metric_ir.models import MetricIR

MetricValidator = Callable[[MetricIR], MetricIR]

# Declaration order is the only ordering; the resolution below is order-insensitive
# because exactly one declaration must accept a well-formed IR.
SCENARIO_VALIDATORS: dict[str, MetricValidator] = {
    "invoice_risk": validate_invoice_metric,
    "enterprise_relation": validate_enterprise_relation_metric,
}


def scenarios() -> tuple[str, ...]:
    return tuple(SCENARIO_VALIDATORS)


def validator_for(scenario: str) -> MetricValidator:
    try:
        return SCENARIO_VALIDATORS[scenario]
    except KeyError:
        raise UnsupportedMetricError(
            f"No metric semantics are declared for scenario {scenario}"
        ) from None


def validate_for_scenario(metric: MetricIR, scenario: str) -> MetricIR:
    return validator_for(scenario)(metric)


def _accepts(validator: MetricValidator, metric: MetricIR) -> bool:
    try:
        validator(metric)
    except UnsupportedMetricError:
        return False
    return True


def scenario_for_metric(metric: MetricIR | DerivedMetricIR) -> str:
    """Recover the scenario declaration a stored metric satisfies."""
    probe = metric.dependencies[0].metric if isinstance(metric, DerivedMetricIR) else metric
    matches = [
        name for name, validator in SCENARIO_VALIDATORS.items() if _accepts(validator, probe)
    ]
    if len(matches) != 1:
        raise UnsupportedMetricError(
            "Metric IR satisfies "
            + (
                "no scenario declaration"
                if not matches
                else f"{len(matches)} scenario declarations"
            )
        )
    return matches[0]
