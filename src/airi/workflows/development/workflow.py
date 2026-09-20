from airi.agents.requirement_parser.parser import RequirementParser
from airi.approvals.artifacts import identify_artifact
from airi.core.exceptions import PlanningError, SQLValidationError
from airi.metric_ir.derived import DerivedMetricIR, validate_derived_metric
from airi.metric_ir.semantics import validate_for_scenario
from airi.skills.dependencies import SkillDependencyValidator
from airi.skills.models import ScenarioSkill
from airi.skills.registry import SkillRegistry
from airi.tools.registry import ToolRegistry
from airi.tools.spark_sql import SQLDraft, metric_to_tool_input
from airi.workflows.development.derived_planning import DerivedMetricPlanner
from airi.workflows.development.dispatch import validator_for_metric
from airi.workflows.development.planning import SkillPlanner, ToolPlanner
from airi.workflows.development.schemas import DevelopmentRequest, DevelopmentResult


class DevelopmentWorkflow:
    def __init__(self, parser: RequirementParser, skills: SkillRegistry, tools: ToolRegistry):
        self.parser = parser
        self.skills = skills
        self.tools = tools
        self.skill_planner = SkillPlanner()
        self.dependencies = SkillDependencyValidator()
        self.tool_planner = ToolPlanner()

    def run(self, request: DevelopmentRequest) -> DevelopmentResult:
        parsed = self.parser.parse(request.requirement, request.scenario)
        metric = (
            validate_derived_metric(parsed)
            if isinstance(parsed, DerivedMetricIR)
            else validate_for_scenario(parsed, request.scenario)
        )
        return self.run_structured(metric, request.execution_context, request.scenario)

    def run_structured(self, metric, context, scenario_name="invoice_risk", *, candidate=False):
        from airi.refinement.transform import validate_candidate_metric
        from airi.tools.candidate_sql import candidate_input
        from airi.tools.join_sql import metric_to_join_input

        if candidate:
            metric = validate_candidate_metric(metric)
        elif isinstance(metric, DerivedMetricIR):
            metric = validate_derived_metric(metric)
        else:
            # The scenario's own declaration decides, whichever caller produced the IR.
            metric = validate_for_scenario(metric, scenario_name)
        scenario = self.skills.get(scenario_name, "1.1.0" if candidate else "1.0.0")
        if not isinstance(scenario, ScenarioSkill):
            raise PlanningError("Expected a Scenario Skill")
        skill_plan = self.skill_planner.plan(scenario, metric)
        capabilities = self.dependencies.validate(
            skill_plan.scenario_skill, skill_plan.capabilities, self.skills, self.tools
        )
        tool_plan = self.tool_planner.plan(capabilities)
        reference = tool_plan.tools[-1]
        derived_plan = None
        if isinstance(metric, DerivedMetricIR):
            derived_plan = DerivedMetricPlanner().plan(metric, context)
            payload = {
                "metric_ir": metric.model_dump(),
                "execution_context": context.model_dump(),
            }
        elif candidate:
            payload = candidate_input(metric, context).model_dump()
        elif metric.joins:
            # The joined shape maps onto the join tool's own input; still no SQL text.
            payload = metric_to_join_input(metric).model_dump()
        else:
            payload = metric_to_tool_input(metric, context).model_dump()
        output = self.tools.get(reference.name, reference.version).invoke(payload)
        artifact = SQLDraft.model_validate(output.model_dump())
        validation = validator_for_metric(metric).validate(
            artifact.code, artifact.language, metric, context
        )
        if not validation.valid:
            raise SQLValidationError("SQL draft rejected: " + "; ".join(validation.errors))
        artifact = SQLDraft.model_validate(
            artifact.model_dump()
            | {
                "warnings": [*artifact.warnings, *scenario.knowledge],
            }
        )
        return DevelopmentResult(
            metric_ir=metric,
            skill_plan=skill_plan,
            tool_plan=tool_plan,
            artifact=identify_artifact(artifact, metric.name),
            derived_plan=derived_plan,
            validation=validation,
            execution_context=context,
            prompt_version=(
                "1.0.0" if candidate else self.parser.prompt_version_for(scenario_name)
            ),
            requires_human_review=True,
        )
