"""Which static validator owns which rendered SQL shape.

Pure structure, no scenario names: a derived metric is validated as a CTE, a
metric that declares joins is validated against the joined grammar, and
everything else against the single-source grammar.
"""

from airi.metric_ir.derived import DerivedMetricIR
from airi.workflows.development.derived_validation import DerivedSQLValidator
from airi.workflows.development.join_validation import JoinSQLValidator
from airi.workflows.development.validation import SQLValidator

SQLValidatorProtocol = SQLValidator | DerivedSQLValidator | JoinSQLValidator


def validator_for_metric(metric) -> SQLValidatorProtocol:
    if isinstance(metric, DerivedMetricIR):
        return DerivedSQLValidator()
    if getattr(metric, "joins", None):
        return JoinSQLValidator()
    return SQLValidator()
