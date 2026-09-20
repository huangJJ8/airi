"""Phase 11 — the enterprise_relation scenario proves the architecture generalizes.

Two families of tests live here:

* mechanism tests — the Join IR, the join SQL tool, the closed join grammar,
  the sandbox and the canonical hash treat a joined metric structurally and
  never mention a business path;
* scenario tests — the enterprise_relation declaration, the synthetic fixture
  with hand-derived expectations (no production code computes them), planner
  isolation between the two scenarios, and the full
  generation -> approval -> testing -> experiment API chain.
"""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import create_engine

from airi.approvals.artifacts import canonical_hash
from airi.core.config import Settings
from airi.core.exceptions import (
    DuplicateRegistrationError,
    PlanningError,
    UnsupportedMetricError,
)
from airi.execution.models import ExecutionProfile
from airi.execution.sandbox import SandboxGuard, SandboxViolation
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.infrastructure.relation_fixture import (
    ENTERPRISES,
    ISOLATED_ENTERPRISE,
    enterprise_id,
    relation_fixture,
)
from airi.main import create_app
from airi.metric_ir.enterprise_relation import (
    RELATION_METRIC_NAME,
    validate_enterprise_relation_metric,
)
from airi.metric_ir.joins import validate_join_metric
from airi.metric_ir.models import MetricIR
from airi.metric_ir.semantics import scenario_for_metric
from airi.skills.capabilities import metric_count
from airi.skills.enterprise_relation import enterprise_relation_skills
from airi.skills.invoice import invoice_skills
from airi.skills.models import ScenarioSkill
from airi.skills.registry import SkillRegistry
from airi.testing.models import DEFAULT_TEST_TYPES
from airi.testing.reconciliation import reconciles, reference_join_count
from airi.testing.relation import NAIVE_DIRECT_JOIN
from airi.tools.join_sql import GenerateJoinMetricSQL, metric_to_join_input
from airi.workflows.development.join_validation import JoinSQLValidator
from airi.workflows.development.planning import SkillPlanner, ToolPlanner
from airi.workflows.testing import workflow as testing_workflow

ANCHOR = {"anchor_time": "2026-09-09T00:00:00+08:00"}
RELATION_REQUIREMENT = "统计企业通过关联自然人间接关联的其他企业数量"
INVOICE_REQUIREMENT = "统计企业近30天开票金额"

# The declared relationship IR, spelled out once. Everything else derives from it.
RELATION_IR = {
    "schema_version": "1.0.0",
    "name": RELATION_METRIC_NAME,
    "display_name": "企业关联企业数量",
    "description": "统计企业通过关联自然人间接关联的其他企业数量（按企业去重，排除本企业）",
    "entity_type": "enterprise",
    "entity_key": "enterprise_id",
    "partition_field": None,
    "source": {
        "catalog": None,
        "database": "demo",
        "table": "enterprise_person_relation",
    },
    "aggregation": {"function": "count_distinct", "field": "related_enterprise_id"},
    "dimensions": [],
    "filters": [],
    "window": None,
    "source_alias": "ep",
    "aggregation_alias": "pe",
    "joins": [
        {
            "alias": "pe",
            "join_type": "inner",
            "source": {
                "catalog": None,
                "database": "demo",
                "table": "person_enterprise_relation",
            },
            "conditions": [
                {
                    "left": {"alias": "ep", "field": "person_id"},
                    "operator": "=",
                    "right": {"alias": "pe", "field": "person_id"},
                }
            ],
        }
    ],
    "column_filters": [
        {
            "left": {"alias": "pe", "field": "related_enterprise_id"},
            "operator": "<>",
            "right": {"alias": "ep", "field": "enterprise_id"},
        }
    ],
}

INVOICE_IR = {
    "schema_version": "1.0.0",
    "name": "invoice_amount_30d",
    "display_name": "近30天企业开票金额",
    "description": "统计企业近30天开票金额之和",
    "entity_type": "enterprise",
    "entity_key": "seller_tax_no",
    "partition_field": "dt",
    "source": {"catalog": None, "database": "c_db", "table": "source_fp_jdc_view"},
    "aggregation": {"function": "sum", "field": "invoice_amt"},
    "dimensions": [],
    "filters": [],
    "window": {
        "size": 30,
        "unit": "day",
        "time_field": "invoice_date",
        "timezone": "Asia/Shanghai",
    },
}


def relation_metric() -> MetricIR:
    return TypeAdapter(MetricIR).validate_python(RELATION_IR)


def invoice_metric() -> MetricIR:
    return TypeAdapter(MetricIR).validate_python(INVOICE_IR)


def rendered_join_sql() -> str:
    tool = GenerateJoinMetricSQL()
    output = tool.invoke(metric_to_join_input(relation_metric()).model_dump())
    return output.code


def expected_counts() -> dict[str, int]:
    """Hand-derived from the fixture's documented construction rules alone.

    present(i) iff t_i = i % 5 >= 1 or i % 4 == 0; count = t_i, or 1 when only
    the hub person provides a target. Self-loops (i % 7 == 0) never count and
    duplicate rows (i % 3 == 0) never inflate the DISTINCT.
    """
    counts = {}
    for index in range(1, ENTERPRISES + 1):
        targets = index % 5
        if targets == 0 and index % 4 != 0:
            continue
        counts[enterprise_id(index)] = max(targets, 1)
    return counts


@pytest.fixture
def demo_settings(tmp_path):
    config = Settings(
        _env_file=None,
        environment="test",
        execution_mode="mock",
        llm_mode="demo_mock",
        demo_fixtures=True,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'airi.db').as_posix()}",
    )
    engine = create_engine(config.database_url.get_secret_value())
    with engine.begin() as connection:
        migration = Config("alembic.ini")
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    engine.dispose()
    return config


@pytest.fixture
def demo_client(demo_settings):
    with TestClient(create_app(demo_settings)) as client:
        yield client


def generate(client, requirement, scenario):
    return client.post(
        "/api/v1/development/generate",
        json={"requirement": requirement, "scenario": scenario, "execution_context": ANCHOR},
    )


# ----------------------------------------------------------------- IR mechanism


def test_declared_relation_ir_validates():
    metric = validate_enterprise_relation_metric(relation_metric())
    assert metric.name == RELATION_METRIC_NAME
    assert metric.joins[0].conditions[0].left.field == "person_id"
    assert metric.column_filters[0].operator == "<>"


def test_direct_enterprise_join_key_is_rejected():
    """The naive enterprise_id = related_enterprise_id path is not the declaration."""
    data = {**RELATION_IR, "joins": [{**RELATION_IR["joins"][0]}]}
    data["joins"][0] = {
        **data["joins"][0],
        "conditions": [
            {
                "left": {"alias": "ep", "field": "enterprise_id"},
                "operator": "=",
                "right": {"alias": "pe", "field": "related_enterprise_id"},
            }
        ],
    }
    with pytest.raises(UnsupportedMetricError):
        validate_enterprise_relation_metric(TypeAdapter(MetricIR).validate_python(data))


def test_missing_self_exclusion_is_rejected():
    data = {**RELATION_IR, "column_filters": []}
    with pytest.raises(UnsupportedMetricError):
        validate_enterprise_relation_metric(TypeAdapter(MetricIR).validate_python(data))


def test_join_mechanism_gates():
    """The generic mechanism gate rejects shapes, not business names."""
    # A window makes no sense on a state-of-record relationship count.
    windowed = {**RELATION_IR, "window": INVOICE_IR["window"]}
    with pytest.raises(UnsupportedMetricError):
        validate_join_metric(TypeAdapter(MetricIR).validate_python(windowed))
    # An aggregate taken from the base source is not a join metric.
    base_aggregate = {
        **RELATION_IR,
        "aggregation_alias": "ep",
        "aggregation": {"function": "count_distinct", "field": "enterprise_id"},
        "column_filters": [],
    }
    with pytest.raises(UnsupportedMetricError):
        validate_join_metric(TypeAdapter(MetricIR).validate_python(base_aggregate))
    # A join not keyed to the base source cannot be executed safely.
    unanchored = {**RELATION_IR, "joins": [{**RELATION_IR["joins"][0]}]}
    unanchored["joins"][0] = {
        **unanchored["joins"][0],
        "conditions": [
            {
                "left": {"alias": "pe", "field": "person_id"},
                "operator": "=",
                "right": {"alias": "pe", "field": "related_enterprise_id"},
            }
        ],
    }
    with pytest.raises(UnsupportedMetricError):
        validate_join_metric(TypeAdapter(MetricIR).validate_python(unanchored))


def test_canonical_hash_includes_joins():
    """Two IRs differing only in a join key must hash differently."""
    baseline = canonical_hash(RELATION_IR)
    assert baseline == canonical_hash(relation_metric().model_dump())
    direct = {**RELATION_IR, "joins": [{**RELATION_IR["joins"][0]}]}
    direct["joins"][0] = {
        **direct["joins"][0],
        "conditions": [
            {
                "left": {"alias": "ep", "field": "enterprise_id"},
                "operator": "=",
                "right": {"alias": "pe", "field": "related_enterprise_id"},
            }
        ],
    }
    assert canonical_hash(direct) != baseline
    without_exclusion = {**RELATION_IR, "column_filters": []}
    assert canonical_hash(without_exclusion) != baseline


def test_scenario_resolution_is_unique():
    assert scenario_for_metric(relation_metric()) == "enterprise_relation"
    assert scenario_for_metric(invoice_metric()) == "invoice_risk"
    # Satisfying no declaration is an error, not a default scenario.
    orphan = {**RELATION_IR, "name": "undocumented_count"}
    with pytest.raises(UnsupportedMetricError):
        scenario_for_metric(TypeAdapter(MetricIR).validate_python(orphan))


# ------------------------------------------------------- SQL tool and sandbox


def test_join_tool_renders_the_declared_path():
    sql = rendered_join_sql()
    assert "INNER JOIN `demo`.`person_enterprise_relation` AS pe" in sql
    assert "ON ep.`person_id` = pe.`person_id`" in sql
    assert "COUNT(DISTINCT pe.`related_enterprise_id`)" in sql
    assert "WHERE pe.`related_enterprise_id` <> ep.`enterprise_id`" in sql
    assert "GROUP BY ep.`enterprise_id`" in sql


def test_join_validator_accepts_the_rendered_draft():
    from airi.core.execution import ExecutionContext

    context = ExecutionContext.model_validate(ANCHOR)
    result = JoinSQLValidator().validate(
        rendered_join_sql(), "spark_sql", relation_metric(), context
    )
    assert result.valid, result.errors


def test_join_validator_rejects_the_naive_direct_join():
    """The closed grammar compares the join key, not just the shape."""
    from airi.core.execution import ExecutionContext

    context = ExecutionContext.model_validate(ANCHOR)
    # Grammatically well-formed, but keyed on the enterprise id instead of the
    # person: the validator must reject it on the key comparison.
    direct = NAIVE_DIRECT_JOIN.replace(
        "GROUP BY ep.`enterprise_id`",
        "WHERE pe.`related_enterprise_id` <> ep.`enterprise_id`\nGROUP BY ep.`enterprise_id`",
    )
    result = JoinSQLValidator().validate(direct, "spark_sql", relation_metric(), context)
    assert not result.valid
    assert any("keys differ" in error for error in result.errors)


def test_sandbox_accepts_the_join_draft():
    from airi.core.execution import ExecutionContext

    SandboxGuard().validate(
        rendered_join_sql(),
        relation_metric(),
        ExecutionContext.model_validate(ANCHOR),
        ExecutionProfile(),
    )


def test_sandbox_rejects_a_foreign_source_database():
    from airi.core.execution import ExecutionContext

    foreign = {**RELATION_IR}
    foreign["joins"] = [
        {
            **RELATION_IR["joins"][0],
            "source": {"database": "ods", "table": "person_enterprise_relation"},
        }
    ]
    with pytest.raises(SandboxViolation):
        SandboxGuard().validate(
            rendered_join_sql(),
            TypeAdapter(MetricIR).validate_python(foreign),
            ExecutionContext.model_validate(ANCHOR),
            ExecutionProfile(),
        )


def test_sandbox_rejects_the_naive_direct_join():
    from airi.core.execution import ExecutionContext

    with pytest.raises(SandboxViolation):
        SandboxGuard().validate(
            NAIVE_DIRECT_JOIN,
            relation_metric(),
            ExecutionContext.model_validate(ANCHOR),
            ExecutionProfile(),
        )


# ------------------------------------------- fixture expectations, hand-derived


def test_fixture_matches_hand_derived_counts():
    """The rendered SQL and the independent oracle must both equal the closed form."""
    fixture = relation_fixture()
    expected = expected_counts()
    assert len(expected) == 102  # 120 - 18 window-less, hub-less enterprises
    output = MockQueryExecutor.evaluate(rendered_join_sql(), [], 1000, fixture.tables)
    observed = {row["entity_id"]: row[RELATION_METRIC_NAME] for row in output.rows}
    assert observed == expected
    oracle = {
        row["entity_id"]: row[RELATION_METRIC_NAME]
        for row in reference_join_count(relation_metric(), fixture.tables)
    }
    assert oracle == expected


def test_fixture_spot_checks():
    counts = expected_counts()
    assert counts[enterprise_id(1)] == 1  # t=1: one forward target
    assert counts[enterprise_id(2)] == 2  # t=2
    assert counts[enterprise_id(4)] == 4  # t=4; hub target already included
    assert counts[enterprise_id(7)] == 2  # self-loop excluded, forward targets kept
    assert counts[enterprise_id(20)] == 1  # t=0, hub person only
    assert counts[enterprise_id(120)] == 1  # hub wraps to E1
    assert enterprise_id(5) not in counts  # t=0, no hub
    assert enterprise_id(35) not in counts  # only a self-loop: excluded entirely
    assert ISOLATED_ENTERPRISE not in counts


def test_sql_result_reconciles_with_oracle():
    fixture = relation_fixture()
    output = MockQueryExecutor.evaluate(rendered_join_sql(), [], 1000, fixture.tables)
    assert reconciles(output.rows, reference_join_count(relation_metric(), fixture.tables))


# ------------------------------------------------------- planner / skill wiring


def relation_scenario():
    return next(s for s in enterprise_relation_skills() if isinstance(s, ScenarioSkill))


def invoice_scenario():
    return next(s for s in invoice_skills() if isinstance(s, ScenarioSkill))


def test_relation_plan_has_no_invoice_capabilities():
    plan = SkillPlanner().plan(relation_scenario(), relation_metric())
    assert [reference.name for reference in plan.capabilities] == [
        "metric_count",
        "metric_join",
        "spark_sql_generator",
    ]
    tools = ToolPlanner().plan(
        [skill for skill in enterprise_relation_skills() if not isinstance(skill, ScenarioSkill)]
    )
    assert [(t.name, t.version) for t in tools.tools] == [("generate_spark_sql_metric", "1.3.0")]


def test_invoice_plan_has_no_join_capability():
    plan = SkillPlanner().plan(invoice_scenario(), invoice_metric())
    names = [reference.name for reference in plan.capabilities]
    assert names == ["metric_window", "metric_sum", "spark_sql_generator"]
    assert "metric_join" not in names


def test_cross_scenario_planning_is_refused():
    with pytest.raises(PlanningError):
        SkillPlanner().plan(invoice_scenario(), relation_metric())
    with pytest.raises(PlanningError):
        SkillPlanner().plan(relation_scenario(), invoice_metric())


def test_shared_capability_is_registered_once():
    registry = SkillRegistry()
    for skill in [*invoice_skills(), *enterprise_relation_skills()]:
        registry.register_shared(skill)
    assert registry.get("metric_count", "1.0.0").name == "metric_count"
    # The same declaration twice is the legitimate shared case...
    registry.register_shared(metric_count())
    # ...a conflicting redeclaration is not.
    conflicting = metric_count().model_copy(update={"description": "a different mechanism"})
    with pytest.raises(DuplicateRegistrationError):
        registry.register_shared(conflicting)


def test_scenario_test_declarations_differ():
    relation_rules = relation_scenario().test_rules
    assert "join" in relation_rules.test_types
    assert set(relation_rules.test_types) == {
        "schema",
        "null",
        "duplicate",
        "join",
        "distinct",
        "self_exclusion",
        "missing_relation",
        "reconciliation",
    }
    # The invoice default is unchanged by Phase 11.
    assert set(DEFAULT_TEST_TYPES) == {
        "schema",
        "null",
        "duplicate",
        "window",
        "boundary",
        "reconciliation",
    }
    assert "join" not in invoice_scenario().test_rules.test_types


def test_testing_workflow_resolves_rules_from_the_artifact():
    registry = SkillRegistry()
    for skill in [*invoice_skills(), *enterprise_relation_skills()]:
        registry.register_shared(skill)
    workflow = testing_workflow.TestingWorkflow(None, None, registry)
    rules = workflow.rules_for(relation_metric())
    assert "self_exclusion" in rules.test_types
    invoice_rules = workflow.rules_for(invoice_metric())
    assert "self_exclusion" not in invoice_rules.test_types
    assert "window" in invoice_rules.test_types


# --------------------------------------------------------------- API end to end


def test_relation_generation_e2e(demo_client):
    response = generate(demo_client, RELATION_REQUIREMENT, "enterprise_relation")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metric_ir"]["name"] == RELATION_METRIC_NAME
    assert body["metric_ir"]["joins"][0]["alias"] == "pe"
    assert [c["name"] for c in body["skill_plan"]["capabilities"]] == [
        "metric_count",
        "metric_join",
        "spark_sql_generator",
    ]
    assert body["tool_plan"]["tools"] == [{"name": "generate_spark_sql_metric", "version": "1.3.0"}]
    assert body["validation"]["valid"], body["validation"]["errors"]
    assert "INNER JOIN `demo`.`person_enterprise_relation` AS pe" in body["artifact"]["code"]
    assert body["prompt_version"] == "1.2.0"
    assert any("关联自然人" in warning for warning in body["artifact"]["warnings"])


@pytest.mark.parametrize(
    "requirement,scenario",
    [
        ("统计企业关联了多少家企业", "enterprise_relation"),  # no person mediation
        ("统计企业通过关联自然人控股的最终受益人数量", "enterprise_relation"),  # out of scope
        (RELATION_REQUIREMENT, "invoice_risk"),  # relation text in invoice family
        (INVOICE_REQUIREMENT, "enterprise_relation"),  # invoice text in relation family
    ],
)
def test_ambiguous_or_cross_family_requirements_are_refused(demo_client, requirement, scenario):
    response = generate(demo_client, requirement, scenario)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "requirement_parse_failed"


def approved_relation(demo_client):
    generated = generate(demo_client, RELATION_REQUIREMENT, "enterprise_relation").json()
    artifact = generated["artifact"]
    approval = demo_client.post(
        "/api/v1/approvals",
        json={
            "artifact_id": artifact["artifact_id"],
            "metric_ir_hash": canonical_hash(generated["metric_ir"]),
            "artifact_hash": artifact["content_hash"],
        },
    ).json()
    decision = demo_client.post(
        f"/api/v1/approvals/{approval['approval_id']}/approve",
        json={"reviewer": "synthetic", "comment": "Synthetic Phase 11 demo"},
    )
    assert decision.status_code == 200, decision.text
    return artifact, approval


def test_relation_testing_runs_eight_checks(demo_client):
    artifact, approval = approved_relation(demo_client)
    response = demo_client.post(
        "/api/v1/tests/run",
        json={
            "artifact_id": artifact["artifact_id"],
            "approval_id": approval["approval_id"],
            "max_rows": 1000,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    report = body["report"]
    assert report["total"] == 8
    assert report["status"] == "passed"
    assert {result["type"] for result in report["results"]} == {
        "schema",
        "null",
        "duplicate",
        "join",
        "distinct",
        "self_exclusion",
        "missing_relation",
        "reconciliation",
    }
    assert all(result["status"] == "passed" for result in report["results"])
    # The mock executor ran the approved SQL over the synthetic relationship
    # tables: 102 rows, exactly the hand-derived population.
    assert body["execution"]["row_count"] == 102


def test_invoice_testing_still_runs_six_checks(demo_client):
    generated = generate(demo_client, INVOICE_REQUIREMENT, "invoice_risk").json()
    artifact = generated["artifact"]
    approval = demo_client.post(
        "/api/v1/approvals",
        json={
            "artifact_id": artifact["artifact_id"],
            "metric_ir_hash": canonical_hash(generated["metric_ir"]),
            "artifact_hash": artifact["content_hash"],
        },
    ).json()
    demo_client.post(
        f"/api/v1/approvals/{approval['approval_id']}/approve",
        json={"reviewer": "synthetic", "comment": "Synthetic Phase 11 demo"},
    )
    response = demo_client.post(
        "/api/v1/tests/run",
        json={
            "artifact_id": artifact["artifact_id"],
            "approval_id": approval["approval_id"],
            "max_rows": 1000,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()["report"]
    assert report["total"] == 6
    assert {result["type"] for result in report["results"]} == set(DEFAULT_TEST_TYPES)


def test_relation_experiment_e2e(demo_client):
    from airi.experiments.validation import dataset_checksum
    from airi.infrastructure.relation_fixture import RELATION_PARTITION

    artifact, approval = approved_relation(demo_client)
    tests = demo_client.post(
        "/api/v1/tests/run",
        json={
            "artifact_id": artifact["artifact_id"],
            "approval_id": approval["approval_id"],
            "max_rows": 1000,
        },
    ).json()
    fixture = relation_fixture()
    dataset = demo_client.post(
        "/api/v1/datasets",
        json={
            "name": "enterprise_relation_sample_20260911",
            "source": {"database": "tmp_db", "table": "enterprise_relation_sample"},
            "entity_key": "enterprise_id",
            "snapshot_time": "2026-09-09T00:00:00+08:00",
            "partition": {"field": "dt", "value": RELATION_PARTITION},
            "row_count": len(fixture.labeled_rows),
            "checksum": dataset_checksum(fixture.labeled_rows, "enterprise_id"),
        },
    )
    assert dataset.status_code == 201, dataset.text
    dataset = dataset.json()
    assert dataset["entity_key"] == "enterprise_id"
    label = demo_client.post(
        "/api/v1/labels", json={"name": "synthetic_relation_label", "entity_key": "enterprise_id"}
    ).json()
    spec = demo_client.post(
        "/api/v1/experiments",
        json={
            "experiment_name": "related_enterprise_count_evaluation",
            "metric": {"artifact_id": artifact["artifact_id"]},
            "approval_id": approval["approval_id"],
            "test_run_id": tests["report"]["test_run_id"],
            "dataset_snapshot_id": dataset["dataset_snapshot_id"],
            "label_definition_id": label["label_definition_id"],
            "anchor_time": ANCHOR["anchor_time"],
            "observation_time": ANCHOR["anchor_time"],
            "label_window": {
                "start": "2026-09-10T00:00:00+08:00",
                "end": "2026-10-09T00:00:00+08:00",
            },
        },
    )
    assert spec.status_code == 201, spec.text
    run = demo_client.post(f"/api/v1/experiments/{spec.json()['experiment_spec_id']}/run")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["run"]["status"] == "completed"
    evaluation = body["evaluation"]
    assert evaluation["metric_name"] == RELATION_METRIC_NAME
    # 102 metric rows join 121 labeled entities: 19 label-only (no relation path).
    assert evaluation["coverage"] == pytest.approx(102 / 121)
    assert body["join_summary"]["matched_count"] == 102
    assert body["join_summary"]["metric_only"] == 0
    assert body["join_summary"]["label_only"] == 19
    assert evaluation["ks"]["direction"] == "higher_is_riskier"
    assert 0 < evaluation["ks"]["value"] <= 1
