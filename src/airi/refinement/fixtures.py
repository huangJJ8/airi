"""Explicit synthetic research fixtures; no production fallback."""

import json
import random

from airi.experiments.fixtures import experiment_fixture
from airi.reflection.llm import MockReflectionLLM


def refinement_fixture():
    source, labels = experiment_fixture()
    rng = random.Random(20260914)
    # An older, independently noisy month; also contains entities absent in the recent window.
    for row in labels:
        source.append(
            {
                "seller_tax_no": row["seller_tax_no"],
                "invoice_date": "2026-06-20",
                "invoice_amt": str(rng.randrange(10000)),
                "dt": "20260620",
            }
        )
    return source, labels


class MockWindowReflectionLLM(MockReflectionLLM):
    model_identifier = "mock-window-reflection@1.0.0"

    def complete_structured(self, **kwargs):
        output = json.loads(super().complete_structured(**kwargs))
        proposal = output["proposals"][0]
        proposal.update(
            proposal_type="window_review",
            action="evaluate_alternative_window",
            parameters={"candidate_windows": [60, 90]},
            reason="Recent-window missingness motivates a controlled window investigation.",
            validation_question="Does a longer window change coverage and separation here?",
        )
        return json.dumps(output)
