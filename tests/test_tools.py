import pytest
from pydantic import Field, ValidationError

from airi.core.exceptions import (
    DuplicateRegistrationError,
    RegistryNotFoundError,
    ToolInputError,
    ToolOutputError,
)
from airi.core.schemas import StrictSchema
from airi.tools.base import Tool, ToolSpec
from airi.tools.registry import ToolRegistry


class AddInput(StrictSchema):
    value: int = Field(ge=0)


class AddOutput(StrictSchema):
    result: int


class AddTool(Tool[AddInput, AddOutput]):
    spec = ToolSpec(name="add_one", version="1.0.0", description="Test-only deterministic tool")
    input_model = AddInput
    output_model = AddOutput

    def execute(self, payload: AddInput) -> AddOutput:
        return AddOutput(result=payload.value + 1)


def test_tool_registration_and_invocation():
    registry = ToolRegistry()
    registry.register(AddTool())
    tool = registry.get("add_one", "1.0.0")
    assert tool.invoke({"value": 2}) == AddOutput(result=3)
    assert tool.schemas()["input"]["additionalProperties"] is False
    assert tool.schemas()["output"]["properties"]["result"]["type"] == "integer"
    assert registry.list_specs() == [AddTool.spec]


def test_explicit_version_resolution_and_duplicate():
    registry = ToolRegistry()
    registry.register(AddTool())
    with pytest.raises(DuplicateRegistrationError):
        registry.register(AddTool())
    newer = AddTool()
    newer.spec = ToolSpec(name="add_one", version="1.1.0", description="New test version")
    registry.register(newer)
    assert registry.get("add_one", "1.1.0") is newer
    with pytest.raises(RegistryNotFoundError):
        registry.get("add_one", "2.0.0")
    assert ToolRegistry().list_specs() == []


@pytest.mark.parametrize(
    "payload", [{"value": "1"}, {"value": True}, {"value": -1}, {"value": 1, "extra": 1}, {}]
)
def test_tool_rejects_input_before_execution(payload):
    class Unreachable(AddTool):
        def execute(self, payload):
            pytest.fail("Invalid input must never reach execute")

    with pytest.raises(ToolInputError):
        Unreachable().invoke(payload)


@pytest.mark.parametrize(
    "bad_result", [{"result": "1"}, {"wrong": 1}, AddOutput.model_construct(result="1")]
)
def test_tool_revalidates_outputs(bad_result):
    class BrokenTool(AddTool):
        def execute(self, payload):
            return bad_result

    with pytest.raises(ToolOutputError):
        BrokenTool().invoke({"value": 1})


def test_tool_is_abstract_and_schema_registration_checked():
    with pytest.raises(TypeError):
        Tool()
    invalid = AddTool()
    invalid.input_model = dict
    with pytest.raises(TypeError):
        ToolRegistry().register(invalid)


@pytest.mark.parametrize("version", ["latest", "1", "1.0", "01.0.0", "1.0.0-beta"])
def test_tool_spec_requires_numeric_triplet(version):
    with pytest.raises(ValidationError):
        ToolSpec(name="tool", version=version, description="test")
