from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from airi.core.schemas import StrictSchema


class EvaluationPolicy(StrictSchema):
    metrics: list[Literal["coverage", "bad_rate", "lift", "ks", "iv"]] = [
        "coverage",
        "bad_rate",
        "lift",
        "ks",
        "iv",
    ]
    threshold_percentiles: list[Literal[50, 75, 90, 95, 99]] = [50, 75, 90, 95, 99]
    version: Literal["1.0.0"] = "1.0.0"
    bins: int = Field(default=10, ge=2, le=20)
    epsilon: float = Field(default=1e-6, gt=0, le=0.01)
    min_labeled_samples: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def supported(self):
        if len(self.metrics) != 5 or len(set(self.metrics)) != 5:
            raise ValueError("Version 1 evaluates exactly the five supported metrics")
        if not self.threshold_percentiles or len(set(self.threshold_percentiles)) != len(
            self.threshold_percentiles
        ):
            raise ValueError("Threshold percentiles must be nonempty and unique")
        return self


class MetricDistribution(StrictSchema):
    count: int
    null_count: int
    min: Decimal | None
    max: Decimal | None
    mean: Decimal | None
    p25: Decimal | None
    p50: Decimal | None
    p75: Decimal | None
    p90: Decimal | None
    p95: Decimal | None
    p99: Decimal | None


class EvaluationBin(StrictSchema):
    bucket: int
    is_null: bool
    lower: Decimal | None
    upper: Decimal | None
    count: int
    good: int
    bad: int
    bad_rate: float | None
    lift: float | None
    good_pct: float | None = None
    bad_pct: float | None = None
    woe: float | None = None
    iv_component: float | None = None


class KSResult(StrictSchema):
    status: Literal["available", "not_available"]
    value: float | None
    ascending: float | None
    descending: float | None
    bucket: int | None
    direction: Literal["higher_is_riskier", "lower_is_riskier", "undetermined"]
    split: Decimal | None


class ThresholdCandidate(StrictSchema):
    threshold: Decimal
    operator: Literal[">=", "<="]
    sources: list[str]
    hit_count: int
    hit_rate: float | None
    bad_count: int
    good_count: int
    bad_rate: float | None
    precision: float | None
    recall: float | None
    lift: float | None


class MetricEvaluation(StrictSchema):
    metric_name: str
    total_sample: int
    labeled_sample: int
    good_count: int
    bad_count: int
    non_null_metric: int
    null_metric: int
    coverage: float | None
    bad_rate: float | None
    distribution: MetricDistribution
    requested_bins: int
    actual_bins: int
    bins: list[EvaluationBin]
    ks: KSResult
    iv: float | None
    iv_status: Literal["available", "not_available"]
    epsilon: float
    threshold_candidates: list[ThresholdCandidate]
    warnings: list[str]
