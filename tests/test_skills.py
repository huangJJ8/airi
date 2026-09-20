import pytest
from pydantic import TypeAdapter, ValidationError

from airi.core.exceptions import DuplicateRegistrationError, RegistryNotFoundError
from airi.core.schemas import VersionedReference
from airi.skills.models import CapabilitySkill, ScenarioSkill, Skill
from airi.skills.registry import SkillRegistry


def reference(name="render_sql"):
    return VersionedReference(name=name, version="1.0.0")


def capability():
    return CapabilitySkill(
        name="sql_rendering",
        version="1.0.0",
        description="Render approved templates",
        tools=[reference()],
    )


def scenario():
    return ScenarioSkill(
        name="payment_risk",
        version="1.0.0",
        description="Payment risk metric development",
        intent="Analyze payment failures",
        capabilities=[reference("sql_rendering")],
    )


@pytest.mark.parametrize("skill", [capability(), scenario()])
def test_skill_discriminated_json_roundtrip(skill):
    assert TypeAdapter(Skill).validate_json(skill.model_dump_json()) == skill


def test_skill_registry_versions_and_isolation():
    registry = SkillRegistry()
    original = capability()
    registry.register(original)
    registry.register(scenario())
    original.tools.clear()
    retrieved = registry.get("sql_rendering", "1.0.0")
    assert retrieved.tools == [reference()]
    retrieved.tools.clear()
    assert registry.get("sql_rendering", "1.0.0").tools == [reference()]
    assert len(registry.list_skills()) == 2
    with pytest.raises(DuplicateRegistrationError):
        registry.register(capability())
    with pytest.raises(RegistryNotFoundError):
        registry.get("sql_rendering", "2.0.0")
    newer = capability().model_copy(update={"version": "1.1.0"})
    registry.register(newer)
    assert registry.get("sql_rendering", "1.1.0").version == "1.1.0"
    assert SkillRegistry().list_skills() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("requires_human_review", False),
        ("requires_human_review", 1),
        ("requires_human_review", "true"),
        ("capabilities", []),
        ("capabilities", [reference(), reference()]),
        ("intent", " "),
        ("type", "agent"),
    ],
)
def test_invalid_scenario(field, value):
    with pytest.raises(ValidationError):
        ScenarioSkill.model_validate(scenario().model_dump() | {field: value})


@pytest.mark.parametrize("tools", [[], [reference(), reference()]])
def test_invalid_capability(tools):
    with pytest.raises(ValidationError):
        CapabilitySkill.model_validate(capability().model_dump() | {"tools": tools})


def test_invalid_skill_discriminator():
    with pytest.raises(ValidationError):
        TypeAdapter(Skill).validate_python(capability().model_dump() | {"type": "agent"})


def test_reference_strictness_and_frozen_fields():
    with pytest.raises(ValidationError):
        VersionedReference(name="bad-name", version="1.0.0")
    with pytest.raises(ValidationError):
        VersionedReference(name="valid", version="latest")
    with pytest.raises(ValidationError):
        VersionedReference(name="valid", version="1.0.0", extra="no")
    with pytest.raises(ValidationError):
        reference().version = "2.0.0"
