import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from airi.agents.requirement_parser.parser import RequirementParser
from airi.agents.requirement_parser.prompts import PROMPT_VERSION
from airi.agents.requirement_parser.schemas import RequirementParseOutput
from airi.core.exceptions import (
    LLMServiceError,
    PlanningError,
    RequirementParseError,
    SkillDependencyError,
    SQLValidationError,
    ToolInputError,
    ToolOutputError,
    UnsupportedMetricError,
)
from airi.core.execution import ExecutionContext
from airi.core.schemas import VersionedReference
from airi.infrastructure.llm import MockLLMClient
from airi.main import create_app
from airi.metric_ir.invoice import validate_invoice_metric
from airi.metric_ir.models import Aggregation, MetricIR
from airi.skills.dependencies import SkillDependencyValidator
from airi.skills.invoice import invoice_skills, reference
from airi.skills.models import CapabilitySkill, ScenarioSkill
from airi.skills.registry import SkillRegistry
from airi.tools.registry import ToolRegistry
from airi.tools.spark_sql import GenerateSparkSQLMetric, SQLDraft, metric_to_tool_input
from airi.workflows.development.planning import SkillPlan, SkillPlanner, ToolPlan, ToolPlanner
from airi.workflows.development.schemas import DevelopmentRequest, DevelopmentResult
from airi.workflows.development.validation import SQLValidationResult, SQLValidator
from airi.workflows.development.workflow import DevelopmentWorkflow

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def invoice_payload():
    return json.loads((EXAMPLES / "invoice_metric_ir.json").read_text(encoding="utf-8"))


@pytest.fixture
def invoice(invoice_payload):
    return MetricIR.model_validate(invoice_payload)


@pytest.fixture
def request_payload():
    return json.loads((EXAMPLES / "invoice_request.json").read_text(encoding="utf-8"))


@pytest.fixture
def request_model(request_payload):
    return DevelopmentRequest.model_validate(request_payload)


@pytest.fixture
def mock_llm(invoice_payload):
    return MockLLMClient(json.dumps({"metric_ir": invoice_payload, "unsupported_reason": None}))


@pytest.fixture
def registries():
    skills, tools = SkillRegistry(), ToolRegistry()
    for skill in invoice_skills():
        skills.register(skill)
    tools.register(GenerateSparkSQLMetric())
    return skills, tools


@pytest.fixture
def workflow(mock_llm, registries):
    return DevelopmentWorkflow(RequirementParser(mock_llm), *registries)


@pytest.fixture
def draft(invoice, request_model):
    payload = metric_to_tool_input(invoice, request_model.execution_context)
    return GenerateSparkSQLMetric().invoke(payload.model_dump())


def test_parser_validates_output_and_uses_versioned_prompt(mock_llm, invoice):
    parser = RequirementParser(mock_llm)
    assert parser.parse("近30天企业开票金额") == invoice
    assert parser.prompt_version == PROMPT_VERSION == "1.1.0"
    assert len(mock_llm.calls) == 1
    assert PROMPT_VERSION in mock_llm.calls[0]["system_prompt"]
    assert mock_llm.calls[0]["schema"]["additionalProperties"] is False


@pytest.mark.parametrize("raw", ["not JSON", "```json\n{}\n```", "", "{broken"])
def test_parser_illegal_json(raw):
    with pytest.raises(RequirementParseError, match="invalid_json"):
        RequirementParser(MockLLMClient(raw)).parse("近30天企业开票金额")


@pytest.mark.parametrize("case", ["unknown", "missing", "wrong_type", "invalid", "sql"])
def test_parser_invalid_metric_ir(invoice_payload, case):
    if case == "unknown":
        invoice_payload["unknown"] = 1
    elif case == "missing":
        del invoice_payload["entity_key"]
    elif case == "wrong_type":
        invoice_payload["window"]["size"] = "30"
    elif case == "invalid":
        invoice_payload["aggregation"] = {"function": "sum"}
    else:
        invoice_payload["sql"] = "DROP TABLE secret"
    llm = MockLLMClient(json.dumps({"metric_ir": invoice_payload, "unsupported_reason": None}))
    with pytest.raises(RequirementParseError, match="invalid_metric_ir"):
        RequirementParser(llm).parse("需求")
    assert len(llm.calls) == 1  # no retries, repair or silent fallback


@pytest.mark.parametrize(
    "payload",
    [
        {"metric_ir": None, "unsupported_reason": "不支持增长率"},
        {"metric_ir": None, "unsupported_reason": None},
        {"unsupported_reason": None},
    ],
)
def test_parser_unsupported_or_incomplete_envelope(payload):
    with pytest.raises(RequirementParseError):
        RequirementParser(MockLLMClient(json.dumps(payload))).parse("增长率")


@pytest.mark.parametrize(
    "update",
    [
        {"entity_type": None},
        {"display_name": None},
        {"partition_field": "other"},
        {"source": {"database": "other", "table": "source_fp_jdc_view"}},
        {"aggregation": {"function": "count"}},
        {"dimensions": ["buyer_tax_no"]},
        {"filters": [{"field": "invoice_amt", "operator": "gt", "value": 0}]},
        {"window": {"size": 7, "unit": "day", "time_field": "invoice_date"}},
    ],
)
def test_ir_business_allowlist_rejects_semantic_drift(invoice_payload, update):
    with pytest.raises(UnsupportedMetricError):
        validate_invoice_metric(MetricIR.model_validate(invoice_payload | update))


def test_invoice_ir_roundtrip_and_knowledge(invoice):
    assert MetricIR.model_validate_json(invoice.model_dump_json()) == invoice
    scenario = invoice_skills()[-1]
    assert scenario.requires_human_review is True
    assert "红冲票处理属于待确认业务规则" in scenario.knowledge
    assert "发票重复属于潜在风险" in scenario.knowledge
    assert all("SELECT" not in text for text in scenario.knowledge)


def test_skill_planner_rules_and_tool_coalescing(invoice, registries):
    skills, tools = registries
    plan = SkillPlanner().plan(skills.get("invoice_risk", "1.0.0"), invoice)
    assert [item.name for item in plan.capabilities] == [
        "metric_window",
        "metric_sum",
        "spark_sql_generator",
    ]
    capabilities = SkillDependencyValidator().validate(
        plan.scenario_skill, plan.capabilities, skills, tools
    )
    tool_plan = ToolPlanner().plan(capabilities)
    assert tool_plan.tools == [reference("generate_spark_sql_metric")]
    assert SkillPlan.model_validate_json(plan.model_dump_json()) == plan
    assert ToolPlan.model_validate_json(tool_plan.model_dump_json()) == tool_plan


@pytest.mark.parametrize(
    "update", [{"window": None}, {"aggregation": Aggregation(function="avg", field="invoice_amt")}]
)
def test_skill_planner_rejects_unsupported_semantics(invoice, update):
    with pytest.raises(PlanningError):
        SkillPlanner().plan(invoice_skills()[-1], invoice.model_copy(update=update))


def test_skill_planner_missing_declaration(invoice):
    scenario = invoice_skills()[-1]
    scenario.capabilities[:] = [ref for ref in scenario.capabilities if ref.name != "metric_sum"]
    with pytest.raises(PlanningError, match="lacks required"):
        SkillPlanner().plan(scenario, invoice)


@pytest.mark.parametrize(
    "case",
    [
        "missing_capability",
        "capability_version",
        "wrong_type",
        "missing_tool",
        "tool_version",
        "plan_version",
    ],
)
def test_dependency_validation_exact_resolution(invoice, case):
    declarations = invoice_skills()
    skills, tools = SkillRegistry(), ToolRegistry()
    for skill in declarations:
        if skill.name == "metric_sum":
            if case == "missing_capability":
                continue
            if case == "capability_version":
                skill = skill.model_copy(update={"version": "2.0.0"})
            if case == "wrong_type":
                skill = ScenarioSkill(
                    name="metric_sum",
                    version="1.0.0",
                    description="wrong",
                    intent="wrong",
                    capabilities=[reference("metric_window")],
                )
            if case == "tool_version":
                skill = skill.model_copy(
                    update={
                        "tools": [
                            VersionedReference(name="generate_spark_sql_metric", version="2.0.0")
                        ]
                    }
                )
        skills.register(skill)
    if case != "missing_tool":
        tools.register(GenerateSparkSQLMetric())
    plan = SkillPlanner().plan(declarations[-1], invoice)
    if case == "plan_version":
        plan.capabilities[0] = plan.capabilities[0].model_copy(update={"version": "2.0.0"})
    with pytest.raises(SkillDependencyError):
        SkillDependencyValidator().validate(plan.scenario_skill, plan.capabilities, skills, tools)


def test_tool_planner_rejects_ambiguous_tools():
    skill = CapabilitySkill(
        name="metric_sum", version="1.0.0", description="test", tools=[reference("other_tool")]
    )
    with pytest.raises(PlanningError):
        ToolPlanner().plan([skill])


def test_generate_fixed_anchor_and_template(draft):
    assert draft.status == "draft"
    assert draft.language == "spark_sql"
    assert draft.template == reference("aggregation_metric")
    assert "SUM(`invoice_amt`) AS `invoice_amount_30d`" in draft.code
    assert "FROM `c_db`.`source_fp_jdc_view`" in draft.code
    assert "`invoice_date` >= DATE '2026-08-10'" in draft.code
    assert "`invoice_date` < DATE '2026-09-09'" in draft.code
    assert "GROUP BY `seller_tax_no`" in draft.code
    assert "current_date" not in draft.code.lower()
    assert "`dt`" not in draft.code
    assert any("分区" in warning for warning in draft.warnings)


@pytest.mark.parametrize(
    "update",
    [
        {"unknown": 1},
        {"entity_field": "seller_tax_no; DROP TABLE x"},
        {"source_table": "other.table"},
        {"value_field": "SUM(invoice_amt)"},
        {"window_size": 0},
        {"window_size": 7},
        {"window_size": "30"},
        {"window_size": True},
        {"window_unit": "month"},
        {"aggregation": "count"},
    ],
)
def test_tool_strict_input(invoice, request_model, update):
    payload = metric_to_tool_input(invoice, request_model.execution_context).model_dump()
    with pytest.raises(ToolInputError):
        GenerateSparkSQLMetric().invoke(payload | update)


@pytest.mark.parametrize(
    "update", [{"language": "hive_sql"}, {"status": "approved"}, {"code": ""}, {"unexpected": True}]
)
def test_sql_tool_output_schema(invoice, request_model, draft, update, monkeypatch):
    tool = GenerateSparkSQLMetric()
    monkeypatch.setattr(tool, "execute", lambda payload: draft.model_dump() | update)
    with pytest.raises(ToolOutputError):
        tool.invoke(metric_to_tool_input(invoice, request_model.execution_context).model_dump())


@pytest.mark.parametrize(
    "anchor",
    [
        "2026-09-09",
        "2026-09-09T00:00:00",
        "2026-09-09T12:00:00+08:00",
        "not-a-date",
        123,
        "1999-09-09T00:00:00+08:00",
    ],
)
def test_execution_context_rejects_ambiguous_anchors(anchor):
    with pytest.raises(ValidationError):
        ExecutionContext(anchor_time=anchor)


def test_equivalent_instant_and_leap_window(invoice, request_model, draft):
    context = ExecutionContext(anchor_time="2026-09-08T16:00:00Z")
    assert (
        GenerateSparkSQLMetric().invoke(metric_to_tool_input(invoice, context).model_dump()).code
        == draft.code
    )
    leap_context = ExecutionContext(anchor_time="2024-03-01T00:00:00+08:00")
    output = GenerateSparkSQLMetric().invoke(
        metric_to_tool_input(invoice, leap_context).model_dump()
    )
    assert "DATE '2024-01-31'" in output.code
    assert SQLValidator().validate(output.code, output.language, invoice, leap_context).valid


def test_validator_valid_select(draft, invoice, request_model):
    result = SQLValidator().validate(
        draft.code, draft.language, invoice, request_model.execution_context
    )
    assert result.valid is True
    assert result.errors == []
    assert result.warnings
    # SQL formatting and keyword case do not affect the grammar.
    assert (
        SQLValidator()
        .validate(
            " ".join(draft.code.lower().split()),
            draft.language,
            invoice,
            request_model.execution_context,
        )
        .valid
    )


@pytest.mark.parametrize(
    "statement", ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE", "CREATE", "MERGE"]
)
def test_validator_forbidden_statements(statement, draft, invoice, request_model):
    result = SQLValidator().validate(
        f"{draft.code}\n{statement} TABLE danger",
        "spark_sql",
        invoice,
        request_model.execution_context,
    )
    assert not result.valid
    assert "Forbidden SQL statement" in result.errors


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "group",
        "window",
        "lower",
        "upper",
        "inclusive",
        "source",
        "entity",
        "amount",
        "alias",
        "or",
        "union",
        "comment",
        "current_date",
        "language",
        "time_field",
    ],
)
def test_validator_rejects_incorrect_or_bypassed_sql(case, draft, invoice, request_model):
    code, language = draft.code, draft.language
    changes = {
        "group": ("GROUP BY `seller_tax_no`", ""),
        "window": ("WHERE", ""),
        "lower": ("2026-08-10", "2026-08-11"),
        "upper": ("2026-09-09", "2026-09-10"),
        "inclusive": (" < DATE", " <= DATE"),
        "source": ("`c_db`", "`wrong_db`"),
        "entity": ("`seller_tax_no` AS", "`buyer_tax_no` AS"),
        "amount": ("SUM(`invoice_amt`)", "SUM(`other_amount`)"),
        "alias": ("`invoice_amount_30d`", "`wrong_metric`"),
        "or": ("GROUP BY", "OR 1=1 GROUP BY"),
        "union": (";", " UNION SELECT 1, 2;"),
        "comment": ("GROUP BY", "-- GROUP BY"),
        "current_date": ("DATE '2026-09-09'", "current_date()"),
        "time_field": ("`invoice_date`", "`dt`"),
    }
    if case in changes:
        code = code.replace(*changes[case])
    elif case == "empty":
        code = " "
    else:
        language = "hive_sql"
    assert (
        not SQLValidator().validate(code, language, invoice, request_model.execution_context).valid
    )


def test_workflow_success(workflow, request_model, mock_llm):
    result = workflow.run(request_model)
    assert result.requires_human_review is True
    assert result.artifact.status == "draft"
    assert result.validation.valid
    assert len(result.tool_plan.tools) == 1
    assert len(mock_llm.calls) == 1
    assert "红冲票处理属于待确认业务规则" in result.artifact.warnings
    assert "发票重复属于潜在风险" in result.artifact.warnings
    assert DevelopmentResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("stage", ["parser", "planner", "tool", "validator"])
def test_workflow_stops_on_failure(workflow, request_model, monkeypatch, stage):
    tool = workflow.tools.get("generate_spark_sql_metric", "1.1.0")
    if stage == "parser":
        workflow.parser.llm = MockLLMClient("broken JSON")
        downstream = Mock(side_effect=AssertionError("Planner must not run"))
        monkeypatch.setattr(workflow.skill_planner, "plan", downstream)
        expected = RequirementParseError
    elif stage == "planner":
        monkeypatch.setattr(
            workflow.skill_planner, "plan", Mock(side_effect=PlanningError("No capability"))
        )
        downstream = Mock(side_effect=AssertionError("Tool must not run"))
        monkeypatch.setattr(tool, "invoke", downstream)
        expected = PlanningError
    elif stage == "tool":
        monkeypatch.setattr(tool, "execute", lambda payload: {"language": "invalid"})
        downstream = Mock(side_effect=AssertionError("Validator must not run"))
        # The validator is chosen by the metric's shape, so the seam is the
        # dispatcher rather than a workflow attribute.
        monkeypatch.setattr(
            "airi.workflows.development.workflow.validator_for_metric",
            lambda metric: SimpleNamespace(validate=downstream),
        )
        expected = ToolOutputError
    else:
        original = tool.execute
        monkeypatch.setattr(
            tool,
            "execute",
            lambda payload: original(payload).model_copy(
                update={"code": "DELETE FROM c_db.source_fp_jdc_view"}
            ),
        )
        downstream = None
        expected = SQLValidationError
    with pytest.raises(expected):
        workflow.run(request_model)
    if downstream is not None:
        downstream.assert_not_called()


def test_workflow_rejects_llm_business_drift(workflow, request_model, invoice_payload):
    invoice_payload["source"]["database"] = "unapproved"
    workflow.parser.llm = MockLLMClient(
        json.dumps(
            {
                "metric_ir": invoice_payload,
                "unsupported_reason": None,
            }
        )
    )
    with pytest.raises(UnsupportedMetricError):
        workflow.run(request_model)


def test_api_success_request_id_and_no_execute(settings, mock_llm, request_payload):
    with TestClient(create_app(settings, llm_client=mock_llm)) as client:
        response = client.post("/api/v1/development/generate", json=request_payload)
        assert response.status_code == 200
        result = DevelopmentResult.model_validate_json(response.content)
        assert result.requires_human_review
        assert result.artifact.status == "draft"
        assert len(response.headers["X-Request-ID"]) == 32
        assert client.post("/api/v1/development/execute").status_code == 404


@pytest.mark.parametrize(
    "update",
    [
        {"requirement": " "},
        {"scenario": "unknown"},
        {"execution_context": {}},
        {"execution_context": {"anchor_time": "2026-09-09T00:00:00"}},
        {"execute": True},
    ],
)
def test_api_422_before_llm(settings, mock_llm, request_payload, update):
    with TestClient(create_app(settings, llm_client=mock_llm)) as client:
        response = client.post("/api/v1/development/generate", json=request_payload | update)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]
    assert mock_llm.calls == []


def test_api_parser_application_error(settings, request_payload):
    with TestClient(create_app(settings, llm_client=MockLLMClient("secret bad JSON"))) as client:
        response = client.post("/api/v1/development/generate", json=request_payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "requirement_parse_failed"
        assert "secret" not in response.text
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_api_missing_llm_configuration(settings, request_payload):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/development/generate", json=request_payload)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "llm_not_configured"
        assert "AIRI_LLM_MODEL" in response.json()["error"]["message"]


def test_api_llm_service_error_is_clear_and_redacted(settings, mock_llm, request_payload):
    mock_llm.complete_structured = Mock(side_effect=LLMServiceError("private provider payload"))
    with TestClient(create_app(settings, llm_client=mock_llm)) as client:
        response = client.post("/api/v1/development/generate", json=request_payload)
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "llm_service_failed"
        assert "private" not in response.text
        assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_result_schema_enforces_review_and_validation_consistency(workflow, request_model):
    result = workflow.run(request_model).model_dump()
    for value in (False, 1, "true"):
        with pytest.raises(ValidationError):
            DevelopmentResult.model_validate(result | {"requires_human_review": value})
    with pytest.raises(ValidationError):
        SQLValidationResult(valid=True, errors=["broken"])
    with pytest.raises(ValidationError):
        RequirementParseOutput(metric_ir=request_model, unsupported_reason=None)
    with pytest.raises(ValidationError):
        SQLDraft.model_validate(result["artifact"] | {"status": "approved"})
