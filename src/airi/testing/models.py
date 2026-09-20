from collections import Counter
from datetime import datetime
from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator

from airi.core.schemas import StrictSchema
from airi.execution.models import FailureCategory

TestType = Literal[
    "schema",
    "null",
    "duplicate",
    "window",
    "boundary",
    "join",
    "distinct",
    "self_exclusion",
    "missing_relation",
    "reconciliation",
]
Severity = Literal["error", "warning"]

DEFAULT_TEST_TYPES: list[TestType] = [
    "schema",
    "null",
    "duplicate",
    "window",
    "boundary",
    "reconciliation",
]

# Every test type the runner can execute, with the severity used unless a
# scenario overrides it in ScenarioTestRules.
TEST_TYPE_SEVERITY: dict[str, Severity] = {
    "schema": "error",
    "null": "warning",
    "duplicate": "error",
    "window": "error",
    "boundary": "error",
    "join": "error",
    "distinct": "error",
    "self_exclusion": "error",
    "missing_relation": "warning",
    "reconciliation": "error",
}


class TestRule(StrictSchema):
    severity: Severity


class ScenarioTestRules(StrictSchema):
    """Per-scenario test declaration. The scenario decides *which* checks apply.

    A window metric asks for window and boundary probes; a relationship metric
    asks for join, distinct, self-exclusion and missing-relation probes instead.
    The UI renders whatever the plan contains — it never decides this itself.
    """

    entity_null: TestRule = TestRule(severity="warning")
    result_duplicate: TestRule = TestRule(severity="error")
    empty_result: TestRule = TestRule(severity="warning")
    test_types: list[TestType] = Field(
        default_factory=lambda: list(DEFAULT_TEST_TYPES), min_length=1, max_length=10
    )

    @model_validator(mode="after")
    def unique_test_types(self) -> Self:
        if len(self.test_types) != len(set(self.test_types)):
            raise ValueError("A test type must appear at most once in a scenario plan")
        return self

    def severity_for(self, test_type: str) -> Severity:
        if test_type == "null":
            return self.entity_null.severity
        if test_type == "duplicate":
            return self.result_duplicate.severity
        return TEST_TYPE_SEVERITY.get(test_type, "error")


class MetricTestCase(StrictSchema):
    test_case_id: str = Field(default_factory=lambda: str(uuid4()))
    type: TestType
    severity: Severity


class MetricTestPlan(StrictSchema):
    test_plan_id: str = Field(default_factory=lambda: str(uuid4()))
    artifact_id: str
    execution_run_id: str
    tests: list[MetricTestCase] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.type for case in self.tests}) != len(self.tests):
            raise ValueError("Each test type may appear at most once in a plan")
        return self


class FailureClassification(StrictSchema):
    category: FailureCategory
    possible_causes: list[str]
    recommended_actions: list[str]


class MetricTestResult(MetricTestCase):
    status: Literal["passed", "warning", "failed", "skipped"]
    failure_category: FailureCategory | None = None
    expected_summary: str = Field(max_length=1000)
    actual_summary: str = Field(max_length=1000)
    details: dict[str, int | bool | str] = Field(default_factory=dict)


class MetricTestReport(StrictSchema):
    test_run_id: str = Field(default_factory=lambda: str(uuid4()))
    execution_run_id: str
    artifact_id: str
    status: Literal["passed", "passed_with_warnings", "failed"]
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    warnings: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    created_at: datetime
    finished_at: datetime
    plan: MetricTestPlan
    results: list[MetricTestResult]
    classifications: list[FailureClassification]
    requires_human_review: Literal[True] = True

    @model_validator(mode="after")
    def consistent_counts(self) -> Self:
        if self.total != len(self.results) or self.total != (
            self.passed + self.warnings + self.failed + self.skipped
        ):
            raise ValueError("Report counts must agree with results")
        counts = Counter(result.status for result in self.results)
        if (self.passed, self.warnings, self.failed, self.skipped) != (
            counts["passed"],
            counts["warning"],
            counts["failed"],
            counts["skipped"],
        ):
            raise ValueError("Individual result statuses must agree with counts")
        if self.status == "passed" and (self.warnings or self.failed or self.skipped):
            raise ValueError("Only a complete passing report may have passed status")
        return self
