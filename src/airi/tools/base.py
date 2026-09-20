from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError

from airi.core.exceptions import ToolInputError, ToolOutputError
from airi.core.schemas import NonEmptyText, StrictSchema, VersionedReference


class ToolSpec(VersionedReference):
    description: NonEmptyText


class Tool[InputT: StrictSchema, OutputT: StrictSchema](ABC):
    """Implement execute; callers use invoke so both boundaries are validated."""

    spec: ToolSpec
    input_model: type[InputT]
    output_model: type[OutputT]

    def invoke(self, payload: dict[str, Any]) -> OutputT:
        try:
            validated = self.input_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolInputError("Tool input does not match its declared schema") from exc
        result = self.execute(validated)
        try:
            # Revalidate even model instances: model_construct/mutation can bypass validation.
            raw = result.model_dump(warnings=False) if isinstance(result, StrictSchema) else result
            return self.output_model.model_validate(raw)
        except ValidationError as exc:
            raise ToolOutputError("Tool output does not match its declared schema") from exc

    @abstractmethod
    def execute(self, payload: InputT) -> OutputT:
        """Perform deterministic work. No LLM-generated production SQL."""

    def schemas(self) -> dict[str, Any]:
        return {
            "input": self.input_model.model_json_schema(),
            "output": self.output_model.model_json_schema(),
        }
