from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from airi.core.schemas import NonEmptyText, VersionedReference
from airi.testing.models import ScenarioTestRules


class CapabilitySkill(VersionedReference):
    type: Literal["capability"] = "capability"
    description: NonEmptyText
    tools: list[VersionedReference] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_tools(self) -> Self:
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("Tool references must be unique")
        return self


class ScenarioSkill(VersionedReference):
    test_rules: ScenarioTestRules = ScenarioTestRules()
    type: Literal["scenario"] = "scenario"
    description: NonEmptyText
    intent: NonEmptyText
    knowledge: list[NonEmptyText] = Field(default_factory=list)
    capabilities: list[VersionedReference] = Field(min_length=1)
    requires_human_review: Literal[True] = True

    @field_validator("requires_human_review", mode="before")
    @classmethod
    def require_boolean_true(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("Human review must be the boolean true")
        return True

    @model_validator(mode="after")
    def unique_capabilities(self) -> Self:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("Capability references must be unique")
        return self


Skill = Annotated[ScenarioSkill | CapabilitySkill, Field(discriminator="type")]
