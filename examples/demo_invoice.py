"""Offline example: explicitly inject Mock LLM and write reproducible draft artifacts."""

import json
from pathlib import Path

from airi.agents.requirement_parser.parser import RequirementParser
from airi.infrastructure.llm import MockLLMClient
from airi.skills.invoice import invoice_skills
from airi.skills.registry import SkillRegistry
from airi.tools.registry import ToolRegistry
from airi.tools.spark_sql import GenerateSparkSQLMetric
from airi.workflows.development.schemas import DevelopmentRequest
from airi.workflows.development.workflow import DevelopmentWorkflow


def main() -> None:
    directory = Path(__file__).resolve().parent
    metric = json.loads((directory / "invoice_metric_ir.json").read_text(encoding="utf-8"))
    request = DevelopmentRequest.model_validate_json(
        (directory / "invoice_request.json").read_text(encoding="utf-8")
    )
    llm = MockLLMClient(json.dumps({"metric_ir": metric, "unsupported_reason": None}))
    skills, tools = SkillRegistry(), ToolRegistry()
    for skill in invoice_skills():
        skills.register(skill)
    tools.register(GenerateSparkSQLMetric())
    result = DevelopmentWorkflow(RequirementParser(llm), skills, tools).run(request)
    (directory / "invoice_result.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )
    (directory / "invoice_amount_30d.sql").write_text(result.artifact.code, encoding="utf-8")
    print("Mock LLM demo: draft generated; static validation passed; human review required.")
    print(result.artifact.code)


if __name__ == "__main__":
    main()
