"""The ``enterprise_relation`` scenario skill.

Second, deliberately different scenario. ``invoice_risk`` is a single fact table
with a time window and SUM/COUNT; this one is a two-hop relationship path with
COUNT DISTINCT and a self-exclusion rule. Only business semantics live here:
the declared path, entity/relationship field meanings, data-source meaning,
business rules, known pitfalls and the test expectations the scenario asks for.

Nothing here knows how a join is rendered into SQL. That is the capability
(``metric_join``) and the pinned tool's job — the whole point of the split.
"""

from airi.core.schemas import VersionedReference
from airi.metric_ir.enterprise_relation import (
    RELATION_BASE_TABLE,
    RELATION_JOINED_TABLE,
    RELATION_METRIC_NAME,
    RELATION_SOURCE_DATABASE,
)
from airi.skills.capabilities import metric_count, metric_join, spark_sql_generator
from airi.skills.models import CapabilitySkill, ScenarioSkill
from airi.testing.models import ScenarioTestRules, TestRule

VERSION = "1.0.0"

KNOWLEDGE = [
    # domain / business_description
    "业务域：企业关联关系风险（enterprise relationship risk）。",
    "业务问题：目标企业通过其关联自然人，间接控制或参股了多少家其他企业。",
    # primary_entity
    "主体：enterprise（企业），主体字段 enterprise_id。",
    # relationship_path
    "关系路径：enterprise -> person -> enterprise（两跳，不做多跳与股权穿透）。",
    # data_sources
    f"数据源：{RELATION_SOURCE_DATABASE}.{RELATION_BASE_TABLE}（企业与自然人关系）"
    f"、{RELATION_SOURCE_DATABASE}.{RELATION_JOINED_TABLE}（自然人与企业关系）。",
    # entity_fields
    f"实体字段：{RELATION_BASE_TABLE}.enterprise_id（主体企业）、"
    f"{RELATION_BASE_TABLE}.person_id（关联自然人）、"
    f"{RELATION_JOINED_TABLE}.related_enterprise_id（自然人关联的其他企业）。",
    # relationship_fields
    "关系字段：两表均有 relation_type（股东/法定代表人/实际控制人）。第一期不做关系类型筛选。",
    # business_rules
    "业务规则：同一自然人对同一企业的重复关系记录只计一次（COUNT DISTINCT）。",
    "业务规则：同一企业由多个自然人共同指向时仍只计一次（按企业去重，不按关系记录去重）。",
    "业务规则：自身排除——关联企业等于主体企业时不计入。",
    "业务规则：完全没有任何关联自然人的企业不会出现在结果中（结果只包含关系路径上出现过的企业）。",
    # known_pitfalls
    "注意事项：直接按 enterprise_id 关联 related_enterprise_id 会跳过自然人中转，语义错误。",
    "注意事项：关系表数据缺失会造成关联企业数偏低，属于数据覆盖问题而非指标口径问题。",
    "注意事项：自然人重名或主体标识不统一会引入虚假关联；权威身份映射属于待确认业务规则。",
    "注意事项：不做 N 跳穿透、最终受益人、隐性关系或持股比例链——这些超出本期声明范围。",
]

TEST_RULES = ScenarioTestRules(
    entity_null=TestRule(severity="warning"),
    result_duplicate=TestRule(severity="error"),
    empty_result=TestRule(severity="warning"),
    test_types=[
        "schema",
        "null",
        "duplicate",
        "join",
        "distinct",
        "self_exclusion",
        "missing_relation",
        "reconciliation",
    ],
)


def enterprise_relation_skills() -> list[ScenarioSkill | CapabilitySkill]:
    capabilities = [metric_join(), metric_count(), spark_sql_generator()]
    scenario = ScenarioSkill(
        name="enterprise_relation",
        version=VERSION,
        description="企业关联关系风险指标研发",
        intent=(f"研发企业关联自然人控制或参股的其他企业数量（{RELATION_METRIC_NAME}）"),
        capabilities=[
            VersionedReference(name=skill.name, version=skill.version) for skill in capabilities
        ],
        knowledge=KNOWLEDGE,
        test_rules=TEST_RULES,
        requires_human_review=True,
    )
    return [*capabilities, scenario]
