from airi.core.exceptions import RegistryNotFoundError, SkillDependencyError
from airi.core.schemas import VersionedReference
from airi.skills.models import CapabilitySkill, ScenarioSkill
from airi.skills.registry import SkillRegistry
from airi.tools.registry import ToolRegistry


class SkillDependencyValidator:
    """Resolve declarative references; independent of either Planner implementation."""

    def validate(
        self,
        scenario_ref: VersionedReference,
        capability_refs: list[VersionedReference],
        skills: SkillRegistry,
        tools: ToolRegistry,
    ) -> list[CapabilitySkill]:
        try:
            scenario = skills.get(scenario_ref.name, scenario_ref.version)
            if not isinstance(scenario, ScenarioSkill):
                raise SkillDependencyError("Scenario reference does not refer to a scenario")
            if len(capability_refs) != len(set(capability_refs)):
                raise SkillDependencyError("Duplicate planned capabilities")
            if not set(capability_refs).issubset(set(scenario.capabilities)):
                raise SkillDependencyError(
                    "Plan does not match the scenario's required capabilities"
                )
            resolved = []
            for reference in capability_refs:
                skill = skills.get(reference.name, reference.version)
                if not isinstance(skill, CapabilitySkill):
                    raise SkillDependencyError("Capability reference has the wrong skill type")
                for tool in skill.tools:
                    tools.get(tool.name, tool.version)
                resolved.append(skill)
            return resolved
        except RegistryNotFoundError as exc:
            raise SkillDependencyError(exc.message) from exc
