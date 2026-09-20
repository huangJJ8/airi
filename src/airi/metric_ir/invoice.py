from airi.core.exceptions import UnsupportedMetricError
from airi.metric_ir.models import MetricIR


def validate_invoice_metric(metric: MetricIR) -> MetricIR:
    """Business allowlist after Schema validation; never repairs LLM-generated semantics."""
    metric = MetricIR.model_validate(metric.model_dump())
    # The invoice family is single-source by definition: a metric carrying joins
    # belongs to a relationship scenario and is rejected here, not silently
    # accepted with its joins ignored.
    if metric.joins or metric.column_filters:
        raise UnsupportedMetricError(
            "The invoice metric family is single-source and declares no joins"
        )
    if not (
        (metric.name, metric.display_name, metric.aggregation.function, metric.aggregation.field)
        in {
            ("invoice_amount_30d", "近30天企业开票金额", "sum", "invoice_amt"),
            ("invoice_count_30d", "近30天企业开票次数", "count", None),
        }
        and metric.entity_type == "enterprise"
        and metric.entity_key == "seller_tax_no"
        and metric.partition_field == "dt"
        and metric.source.catalog is None
        and metric.source.database == "c_db"
        and metric.source.table == "source_fp_jdc_view"
        and metric.window is not None
        and metric.window.size == 30
        and metric.window.unit == "day"
        and metric.window.time_field == "invoice_date"
        and metric.window.timezone == "Asia/Shanghai"
        and not metric.filters
        and not metric.dimensions
    ):
        raise UnsupportedMetricError(
            "Only the declared enterprise invoice SUM or row COUNT over 30 days is supported"
        )
    return metric
