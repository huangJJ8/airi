import json

from pydantic import ValidationError

from airi.agents.requirement_parser.prompts import PROMPT_VERSION, PROMPTS, SYSTEM_PROMPT
from airi.agents.requirement_parser.schemas import RequirementParseOutput
from airi.core.exceptions import (
    DerivedDependencyError,
    DerivedMetricError,
    RequirementParseError,
    ZeroDivisionStrategyError,
)
from airi.infrastructure.llm import LLMClient
from airi.metric_ir.derived import DerivedMetricIR
from airi.metric_ir.models import MetricIR


class RequirementParser:
    prompt_version = PROMPT_VERSION

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def prompt_version_for(self, scenario: str = "invoice_risk") -> str:
        return PROMPTS.get(scenario, (SYSTEM_PROMPT, PROMPT_VERSION))[1]

    def parse(self, requirement: str, scenario: str = "invoice_risk") -> MetricIR | DerivedMetricIR:
        system_prompt = PROMPTS.get(scenario, (SYSTEM_PROMPT, PROMPT_VERSION))[0]
        raw = self.llm.complete_structured(
            system_prompt=system_prompt,
            user_prompt=json.dumps(
                {"requirement": requirement, "scenario": scenario}, ensure_ascii=False
            ),
            schema=RequirementParseOutput.model_json_schema(),
        )
        try:
            parsed = RequirementParseOutput.model_validate_json(raw)
        except ValidationError as exc:
            try:
                decoded = json.loads(raw)
                is_derived = (
                    isinstance(decoded, dict)
                    and isinstance(decoded.get("metric_ir"), dict)
                    and decoded["metric_ir"].get("metric_type") == "derived"
                )
            except (ValueError, TypeError):
                is_derived = False
            for error in exc.errors():
                location = tuple(str(part) for part in error["loc"])
                if is_derived and any("DerivedMetricIR" in part for part in location):
                    if "zero_division" in location:
                        raise ZeroDivisionStrategyError(
                            "Only null zero-division strategy is supported"
                        ) from exc
                    if "expression" in location:
                        raise DerivedMetricError(
                            "Only structured growth_rate is supported"
                        ) from exc
                    if "dependencies" in location:
                        raise DerivedDependencyError("Invalid derived metric dependencies") from exc
            reason = (
                "invalid_json"
                if any(error["type"] == "json_invalid" for error in exc.errors())
                else "invalid_metric_ir"
            )
            raise RequirementParseError(
                f"Requirement Parser rejected LLM output: {reason}"
            ) from exc
        if parsed.metric_ir is None:
            raise RequirementParseError(f"Requirement is unsupported or ambiguous for {scenario}")
        return parsed.metric_ir
