"""Scenario-independent capability skills.

A capability skill declares *how* a metric mechanism is realised (and which
pinned tool implements it). It never declares business meaning: the
``enterprise -> person -> enterprise`` path lives in the scenario skill, not
here. ``metric_join`` is the deliberate proof of that split — it knows how to
compose two structured sources through constrained equality joins and nothing
about enterprises, persons or risk.
"""

from airi.core.schemas import VersionedReference
from airi.skills.models import CapabilitySkill

VERSION = "1.0.0"

# Tool versions are pinned here once; a scenario references a capability, and the
# capability pins the tool. Nothing resolves "latest".
ATOMIC_TOOL_VERSION = "1.1.0"
CANDIDATE_TOOL_VERSION = "1.2.0"
JOIN_TOOL_VERSION = "1.3.0"
GROWTH_TOOL_VERSION = "1.0.0"


def reference(name: str) -> VersionedReference:
    """Backwards-compatible helper: the atomic SQL tool and its template pair up."""
    version = (
        ATOMIC_TOOL_VERSION
        if name in {"generate_spark_sql_metric", "aggregation_metric"}
        else VERSION
    )
    return VersionedReference(name=name, version=version)


def metric_window() -> CapabilitySkill:
    return CapabilitySkill(
        name="metric_window",
        version=VERSION,
        description="显式 anchor 下的自然日左闭右开窗口",
        tools=[reference("generate_spark_sql_metric")],
    )


def metric_sum() -> CapabilitySkill:
    return CapabilitySkill(
        name="metric_sum",
        version=VERSION,
        description="对已声明金额字段求和",
        tools=[reference("generate_spark_sql_metric")],
    )


def metric_count() -> CapabilitySkill:
    return CapabilitySkill(
        name="metric_count",
        version=VERSION,
        description="统计行数或实体数量：COUNT(*) 与 COUNT(DISTINCT 已声明字段)",
        tools=[reference("generate_spark_sql_metric")],
    )


def spark_sql_generator() -> CapabilitySkill:
    return CapabilitySkill(
        name="spark_sql_generator",
        version=VERSION,
        description="使用受控模板生成待审核 Spark SQL 草稿",
        tools=[reference("generate_spark_sql_metric")],
    )


def metric_growth_rate() -> CapabilitySkill:
    return CapabilitySkill(
        name="metric_growth_rate",
        version=VERSION,
        description="相邻30日金额增长率，前期为零返回 NULL",
        tools=[VersionedReference(name="generate_growth_rate_sql", version=GROWTH_TOOL_VERSION)],
    )


def metric_join() -> CapabilitySkill:
    """Generic join composition. Declares no business path and no scenario."""
    return CapabilitySkill(
        name="metric_join",
        version=VERSION,
        description=(
            "Compose two or more structured metric data sources through constrained "
            "deterministic joins"
        ),
        tools=[VersionedReference(name="generate_spark_sql_metric", version=JOIN_TOOL_VERSION)],
    )


def shared_capabilities() -> list[CapabilitySkill]:
    """Capabilities that any scenario may reference, defined exactly once."""
    return [
        metric_window(),
        metric_sum(),
        metric_count(),
        spark_sql_generator(),
        metric_join(),
    ]
