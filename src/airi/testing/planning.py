from airi.testing.models import MetricTestCase, MetricTestPlan, ScenarioTestRules


class MetricTestPlanner:
    """Builds the plan from the scenario's declared test types, in declared order.

    The scenario owns the question ("which checks apply to this metric family");
    this planner only materialises the declared list.
    """

    def plan(self, artifact_id, execution_run_id, rules: ScenarioTestRules) -> MetricTestPlan:
        return MetricTestPlan(
            artifact_id=artifact_id,
            execution_run_id=execution_run_id,
            tests=[
                MetricTestCase(type=kind, severity=rules.severity_for(kind))
                for kind in rules.test_types
            ],
        )
