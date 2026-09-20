"""The ``invoice_risk`` scenario skill.

Business semantics only. The capability skills it references are declared once
in :mod:`airi.skills.capabilities`, so a second scenario can reuse the same
mechanisms without redefining them.
"""

from airi.core.schemas import VersionedReference
from airi.skills.capabilities import (
    metric_count,
    metric_growth_rate,
    metric_sum,
    metric_window,
    reference,
    spark_sql_generator,
)
from airi.skills.models import CapabilitySkill, ScenarioSkill

VERSION = "1.0.0"

__all__ = ["VERSION", "invoice_skills", "reference"]


def invoice_skills() -> list[ScenarioSkill | CapabilitySkill]:
    capabilities = [
        metric_window(),
        metric_sum(),
        metric_count(),
        spark_sql_generator(),
        metric_growth_rate(),
    ]
    scenario = ScenarioSkill(
        name="invoice_risk",
        version=VERSION,
        description="企业发票风险指标研发",
        intent="研发企业开票金额、次数及相邻30日金额增长率",
        capabilities=[
            VersionedReference(name=skill.name, version=skill.version) for skill in capabilities
        ],
        knowledge=[
            "企业发票风险：常用主体字段 seller_tax_no",
            "业务时间字段 invoice_date；分区字段 dt",
            "红冲票处理属于待确认业务规则",
            "发票重复属于潜在风险",
        ],
        requires_human_review=True,
    )
    return [*capabilities, scenario]


def invoice_capability(name: str) -> CapabilitySkill:
    for skill in invoice_skills():
        if isinstance(skill, CapabilitySkill) and skill.name == name:
            return skill
    raise KeyError(name)
