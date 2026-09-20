import json

from airi.infrastructure.llm import LLMClient


class MockReflectionLLM(LLMClient):
    """Explicit test double using supplied references, without numerical reasoning."""

    model_identifier = "mock-reflection@1.0.0"

    def complete_structured(self, *, system_prompt, user_prompt, schema):
        context = json.loads(user_prompt)
        proposals = []
        for e in context["evidence"]:
            finding = next(
                f
                for f in context["diagnostics"]
                if e["experiment_run_id"] in f["experiment_run_ids"]
                and f["finding_type"] == "insufficient_evidence"
            )
            proposals.append(
                {
                    "proposal_type": "additional_time_snapshot_validation",
                    "target": e["evaluation"]["metric_name"],
                    "action": "validate_additional_snapshot",
                    "parameters": {},
                    "reason": "Single snapshot evidence leaves temporal generalization unverified.",
                    "validation_question": "Does separation persist on a later labeled snapshot?",
                    "evidence_refs": [finding["finding_id"]],
                    "priority": "medium",
                }
            )
        return json.dumps(
            {
                "summary": "Mock interpretation only; independent evidence is needed.",
                "hypotheses": [
                    {
                        "hypothesis_id": "missingness_hypothesis",
                        "statement": "Missingness may reflect activity or matching gaps.",
                        "evidence_refs": [context["diagnostics"][0]["finding_id"]],
                        "confidence": "low",
                        "status": "unverified",
                    }
                ],
                "proposals": proposals[: context["policy"]["max_proposals"]],
                "missing_evidence": ["Independent later snapshot evidence is needed."],
            }
        )
