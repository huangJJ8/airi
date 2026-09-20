"""Explicit Phase 10/11 demo-mode LLM doubles.

These exist so the local Web portfolio demo runs with zero LLM credentials.
They are selected ONLY through the explicit ``AIRI_LLM_MODE=demo_mock``
setting; nothing here is a silent fallback for a misconfigured provider, and
none of this is legitimate outside a synthetic local demo.

The requirement double is a deterministic rule-based parser over the two demo
scenario families. It understands the small vocabulary the demo teaches (near
N-day windows, invoice amount / invoice count for ``invoice_risk``; the declared
two-hop relationship count for ``enterprise_relation``) and refuses everything
else with ``unsupported_reason`` instead of guessing. The scenario the caller
asked for selects the family; a requirement belonging to another family is
refused rather than translated.
"""

import json
import re
from typing import Any

from airi.infrastructure.llm import LLMClient
from airi.metric_ir.enterprise_relation import (
    RELATION_BASE_ALIAS,
    RELATION_BASE_TABLE,
    RELATION_JOINED_ALIAS,
    RELATION_JOINED_TABLE,
    RELATION_METRIC_NAME,
    RELATION_SOURCE_DATABASE,
    enterprise_relation_path,
    self_exclusion,
)

INVOICE_UNSUPPORTED = (
    "Demo mode only supports the invoice_risk family: "
    "invoice amount or invoice count over the last N days."
)
RELATION_UNSUPPORTED = (
    "Demo mode only supports the declared enterprise_relation metric: the number of other "
    "enterprises reachable through an associated natural person (enterprise -> person -> "
    "enterprise), counted distinctly and excluding the enterprise itself."
)


class DemoRequirementLLM(LLMClient):
    """Deterministic stand-in for the requirement parser. Never auto-selected."""

    model_identifier = "demo-requirement-parser@1.1.0"

    def complete_structured(
        self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> str:
        requirement, scenario = self._prompt(user_prompt)
        parser = self._parse_relation if scenario == "enterprise_relation" else self._parse
        metric_ir = parser(requirement)
        unsupported = (
            RELATION_UNSUPPORTED if scenario == "enterprise_relation" else INVOICE_UNSUPPORTED
        )
        if metric_ir is None:
            return json.dumps(
                {"metric_ir": None, "unsupported_reason": unsupported}, ensure_ascii=False
            )
        return json.dumps({"metric_ir": metric_ir, "unsupported_reason": None}, ensure_ascii=False)

    @staticmethod
    def _prompt(user_prompt: str) -> tuple[str, str]:
        try:
            payload = json.loads(user_prompt)
        except ValueError:
            return "", "invoice_risk"
        if not isinstance(payload, dict):
            return "", "invoice_risk"
        requirement = payload.get("requirement")
        scenario = payload.get("scenario")
        return (
            requirement if isinstance(requirement, str) else "",
            scenario if isinstance(scenario, str) else "invoice_risk",
        )

    def _parse_relation(self, requirement: str) -> dict[str, Any] | None:
        """Only the declared two-hop path; anything vaguer is undefined, not guessed."""
        text = requirement.strip()
        if not text:
            return None
        indirect = any(
            token in text for token in ("关联自然人", "自然人", "间接", "两跳", "中转", "经由")
        )
        target = any(
            token in text for token in ("关联企业", "关联方", "关联的其他企业", "其他企业")
        )
        counted = any(token in text for token in ("数量", "家数", "多少家", "多少个", "count"))
        forbidden = any(
            token in text
            for token in ("穿透", "最终受益人", "多跳", "三跳", "持股比例", "隐性关系", "对外投资")
        )
        if forbidden or not (indirect and target and counted):
            return None
        left, right = enterprise_relation_path()
        exclusion = self_exclusion()
        return {
            "schema_version": "1.0.0",
            "name": RELATION_METRIC_NAME,
            "display_name": "企业关联企业数量",
            "description": "统计企业通过关联自然人间接关联的其他企业数量（按企业去重，排除本企业）",
            "entity_type": "enterprise",
            "entity_key": "enterprise_id",
            "partition_field": None,
            "source": {
                "catalog": None,
                "database": RELATION_SOURCE_DATABASE,
                "table": RELATION_BASE_TABLE,
            },
            "aggregation": {"function": "count_distinct", "field": exclusion.left.field},
            "dimensions": [],
            "filters": [],
            "window": None,
            "source_alias": RELATION_BASE_ALIAS,
            "aggregation_alias": RELATION_JOINED_ALIAS,
            "joins": [
                {
                    "alias": RELATION_JOINED_ALIAS,
                    "join_type": "inner",
                    "source": {
                        "catalog": None,
                        "database": RELATION_SOURCE_DATABASE,
                        "table": RELATION_JOINED_TABLE,
                    },
                    "conditions": [
                        {
                            "left": {"alias": left.alias, "field": left.field},
                            "operator": "=",
                            "right": {"alias": right.alias, "field": right.field},
                        }
                    ],
                }
            ],
            "column_filters": [
                {
                    "left": {
                        "alias": exclusion.left.alias,
                        "field": exclusion.left.field,
                    },
                    "operator": exclusion.operator,
                    "right": {
                        "alias": exclusion.right.alias,
                        "field": exclusion.right.field,
                    },
                }
            ],
        }

    def _parse(self, requirement: str) -> dict[str, Any] | None:
        text = requirement.strip()
        if not text:
            return None
        window = 30
        match = re.search(r"近\s*(\d{1,3})\s*天", text)
        if match:
            window = int(match.group(1))
        elif not any(token in text for token in ("开票", "发票", "invoice")):
            return None
        if not any(token in text for token in ("开票", "发票", "invoice")):
            return None
        is_count = any(token in text for token in ("次数", "笔数", "数量", "count"))
        if is_count:
            name = f"invoice_count_{window}d"
            display_name = f"近{window}天企业开票次数"
            description = f"企业近{window}日开票原始记录行数"
            aggregation: dict[str, Any] = {"function": "count", "field": None}
        else:
            name = f"invoice_amount_{window}d"
            display_name = f"近{window}天企业开票金额"
            description = f"统计企业近{window}天开票金额之和"
            aggregation = {"function": "sum", "field": "invoice_amt"}
        return {
            "schema_version": "1.0.0",
            "name": name,
            "display_name": display_name,
            "description": description,
            "entity_type": "enterprise",
            "entity_key": "seller_tax_no",
            "partition_field": "dt",
            "source": {"catalog": None, "database": "c_db", "table": "source_fp_jdc_view"},
            "aggregation": aggregation,
            "dimensions": [],
            "filters": [],
            "window": {
                "size": window,
                "unit": "day",
                "time_field": "invoice_date",
                "timezone": "Asia/Shanghai",
            },
        }


__all__ = ["DemoRequirementLLM", "demo_reflection_llm"]


def demo_reflection_llm() -> LLMClient:
    """Reuse the governed synthetic reflection double; window-proposal aware."""
    from airi.refinement.fixtures import MockWindowReflectionLLM

    return MockWindowReflectionLLM()
