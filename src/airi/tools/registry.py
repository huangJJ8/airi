from airi.core.exceptions import DuplicateRegistrationError, RegistryNotFoundError
from airi.core.schemas import StrictSchema, VersionedReference
from airi.tools.base import Tool, ToolSpec


class ToolRegistry:
    """App-scoped registry. Register at startup; exact version resolution only."""

    def __init__(self) -> None:
        self._tools: dict[tuple[str, str], Tool] = {}

    def register(self, tool: Tool) -> None:
        if not isinstance(tool, Tool) or not isinstance(tool.spec, ToolSpec):
            raise TypeError("Expected a Tool with a ToolSpec")
        for model in (tool.input_model, tool.output_model):
            if not isinstance(model, type) or not issubclass(model, StrictSchema):
                raise TypeError("Tool input/output models must extend StrictSchema")
            if (
                model.model_config.get("strict") is not True
                or model.model_config.get("extra") != "forbid"
            ):
                raise TypeError("Tool schemas must remain strict and forbid extra fields")
        key = (tool.spec.name, tool.spec.version)
        if key in self._tools:
            raise DuplicateRegistrationError(f"Tool already registered: {key[0]}@{key[1]}")
        self._tools[key] = tool

    def get(self, name: str, version: str) -> Tool:
        reference = VersionedReference(name=name, version=version)
        try:
            return self._tools[(reference.name, reference.version)]
        except KeyError:
            raise RegistryNotFoundError(f"Tool not found: {name}@{version}") from None

    def list_specs(self) -> list[ToolSpec]:
        return [self._tools[key].spec for key in sorted(self._tools)]
