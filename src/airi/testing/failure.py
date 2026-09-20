from airi.testing.models import FailureClassification


def classify(category):
    actions = {
        "syntax_error": "Check generated grammar against the test engine version",
        "schema_error": "Verify source and result columns against the approved Metric IR",
        "data_type_error": "Confirm numeric and date types in the test source",
        "permission_error": "Confirm test adapter configuration and read-only credentials",
        "timeout": "Inspect test-cluster query duration and cancellation status",
        "resource_limit": "Inspect test-cluster resource limits and result size",
        "empty_result": "Confirm test source coverage for the approved window",
        "duplicate_entity": "Inspect final aggregation and join cardinality",
        "window_mismatch": "Compare both SQL boundaries with the approved anchor",
        "boundary_mismatch": "Inspect left-inclusive and right-exclusive predicates",
        "reconciliation_mismatch": "Compare metric arithmetic with independent fixture evidence",
        "execution_error": "Inspect restricted provider diagnostics with the operator",
        "unknown": "Request operator investigation; no cause has been established",
    }
    return FailureClassification(
        category=category,
        possible_causes=[
            "A configuration, data, or SQL mismatch may explain this category; "
            "the underlying cause is not established."
        ],
        recommended_actions=[
            actions.get(category, "Inspect environment acceptance evidence with the operator")
        ],
    )
