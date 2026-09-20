import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

from airi.core.execution import ExecutionContext
from airi.environments.fixtures import fixture_rows
from airi.environments.metadata import MetricMetadataValidator, SchemaInspector
from airi.environments.models import (
    EnvironmentStatus,
    EnvironmentValidationReport,
    Evidence,
    SemanticParityReport,
)
from airi.environments.probes import CAPABILITIES
from airi.execution.models import ExecutionPlan
from airi.infrastructure.query_executor import MockQueryExecutor
from airi.testing.reconciliation import reconciles, reference_calculate


def integration_metrics():
    """Fixed definitions for acceptance only; never expose arbitrary SQL or IR over this API."""
    from airi.metric_ir.derived import DerivedMetricIR
    from airi.metric_ir.models import MetricIR

    atomic = MetricIR.model_validate(
        {
            "name": "invoice_amount_30d",
            "display_name": "近30天企业开票金额",
            "description": "统计企业近30天开票金额之和",
            "entity_type": "enterprise",
            "entity_key": "seller_tax_no",
            "partition_field": "dt",
            "source": {"database": "c_db", "table": "source_fp_jdc_view"},
            "aggregation": {"function": "sum", "field": "invoice_amt"},
            "window": {
                "size": 30,
                "unit": "day",
                "time_field": "invoice_date",
                "timezone": "Asia/Shanghai",
            },
        }
    )
    count = atomic.model_copy(
        update={
            "name": "invoice_count_30d",
            "display_name": "近30天企业开票次数",
            "aggregation": atomic.aggregation.model_copy(
                update={"function": "count", "field": None}
            ),
        }
    )
    growth = DerivedMetricIR.model_validate(
        {
            "metric_type": "derived",
            "name": "invoice_amount_growth_30d",
            "display_name": "近30天企业开票金额增长率",
            "description": "本期相对前期增长率",
            "dependencies": [
                {"role": role, "metric": atomic.model_dump(), "anchor_offset_days": offset}
                for role, offset in (("current", 0), ("previous", 30))
            ],
            "expression": {"operator": "growth_rate"},
            "zero_division": {"strategy": "null"},
        }
    )
    return [atomic, count, growth]


def metric_sql(metric):
    from airi.metric_ir.derived import DerivedMetricIR
    from airi.tools.growth_sql import GenerateGrowthRateSQL, GrowthSQLInput
    from airi.tools.spark_sql import GenerateSparkSQLMetric, metric_to_tool_input

    context = ExecutionContext.model_validate({"anchor_time": "2026-09-09T00:00:00+08:00"})
    if isinstance(metric, DerivedMetricIR):
        code = (
            GenerateGrowthRateSQL()
            .invoke(GrowthSQLInput(metric_ir=metric, execution_context=context))
            .code
        )
    else:
        code = GenerateSparkSQLMetric().invoke(metric_to_tool_input(metric, context)).code
    return code, context


class SparkEnvironmentProbe:
    def __init__(self, executor, settings):
        self.executor, self.settings = executor, settings

    def status(self):
        try:
            result = self.executor.probe("session").rows[0]
            return EnvironmentStatus(
                reachable=True,
                engine_version=str(result["engine_version"])[:256],
                session_timezone=str(result["session_timezone"])[:128],
                database=self.settings.spark_database,
            )
        except Exception:
            return EnvironmentStatus(reachable=False, database=self.settings.spark_database)

    def validate(self):
        started = datetime.now(UTC)
        evidence, capabilities, parity = [], {}, []
        status = self.status()
        from airi.infrastructure.query_executor import SparkSQLExecutor

        unconfigured = isinstance(self.executor, SparkSQLExecutor) and not (
            self.settings.spark_host or self.settings.spark_test_host
        )

        def add(kind, state, summary, source="spark_test"):
            evidence.append(
                Evidence(evidence_type=kind, source=source, status=state, summary=summary)
            )

        schema = None
        if status.reachable:
            add(
                "timezone",
                "passed" if status.session_timezone == "Asia/Shanghai" else "failed",
                "Expected Asia/Shanghai; actual=" + str(status.session_timezone),
            )
            for name, (_, expected) in CAPABILITIES.items():
                try:
                    output = self.executor.probe(name)
                    matches = bool(output.rows) and list(output.rows[0].values())[0] == expected
                    capabilities[name] = "passed" if matches else "failed"
                except Exception:
                    capabilities[name] = "failed"
                add(name, capabilities[name], "Bounded literal query expectation checked")
            try:
                schema = SchemaInspector().inspect(self.executor.probe("source_schema"))
                MetricMetadataValidator().validate(schema, status.session_timezone)
                add("source_schema", "passed", "Required field names and types verified")
            except Exception as exc:
                add("source_schema", "failed", getattr(exc, "category", "metadata_error"))
            try:
                decimal = self.executor.probe("decimal").rows[0]
                valid = Decimal(str(decimal["value"])) == Decimal("0.30") and bool(
                    re.fullmatch(r"decimal\(\d+,\s*2\)", str(decimal["result_type"]))
                )
                add(
                    "decimal_semantics",
                    "passed" if valid else "failed",
                    "SUM(DECIMAL(18,2)) observed result type=" + str(decimal["result_type"])[:100],
                )
            except Exception:
                add("decimal_semantics", "failed", "decimal_mismatch")
            try:
                limited = self.executor.probe("resource")
                add(
                    "max_rows",
                    "passed" if len(limited.rows) == 10 and limited.truncated else "failed",
                    "11-row literal source with max_rows=10",
                )
            except Exception:
                add("max_rows", "failed", "Resource probe failed")
            try:
                cancelled = self.executor.probe("cancel")
                add(
                    "cancel_requested",
                    "passed" if cancelled.cancel_requested else "not_verified",
                    "Small SELECT cancellation requested",
                )
                add(
                    "remote_cancel",
                    "passed" if cancelled.remote_cancel_status == "cancelled" else "not_verified",
                    "Observed operation state=" + cancelled.remote_cancel_status,
                )
            except Exception:
                add("remote_cancel", "not_verified", "remote_cancellation_not_guaranteed")
            if self.settings.spark_fixture_mapping:
                for metric in integration_metrics():
                    try:
                        code, context = metric_sql(metric)
                        plan = ExecutionPlan(
                            artifact_id="environment-fixture",
                            approval_id="environment-fixture",
                            workflow_run_id="environment-fixture",
                            request_id="environment-fixture",
                            sql_hash="0" * 64,
                            max_rows=1000,
                            execution_context=context,
                        )
                        output = self.executor.execute(code, plan)
                        expected = reference_calculate(metric.name, context, fixture_rows())
                        local = MockQueryExecutor.evaluate(code, fixture_rows()).rows
                        a, b, c = (
                            reconciles(local, expected),
                            reconciles(output.rows, expected),
                            reconciles(output.rows, local),
                        )
                        valid = a and b and c and not output.truncated
                        parity.append(
                            SemanticParityReport(
                                result_types={c.name: c.type for c in output.columns},
                                metric_name=metric.name,
                                status="passed" if valid else "failed",
                                sqlite_vs_python=a,
                                spark_vs_python=b,
                                spark_vs_sqlite=c,
                                differences=[]
                                if valid
                                else [
                                    f"sqlite_vs_python={a}; spark_vs_python={b}; "
                                    f"spark_vs_sqlite={c}",
                                    f"expected_rows={len(expected)}; "
                                    f"spark_rows={len(output.rows)}; "
                                    f"truncated={output.truncated}",
                                ],
                            )
                        )
                    except Exception:
                        parity.append(
                            SemanticParityReport(
                                metric_name=metric.name,
                                status="failed",
                                differences=["Fixture execution or metadata verification failed"],
                            )
                        )
                # Only the controlled fixture format is inspected, never sample business rows.
                try:
                    fixture_schema = SchemaInspector().inspect(
                        self.executor.probe("fixture_schema"), fixture=True
                    )
                    samples = self.executor.probe("fixture_rows")
                    fmt = (
                        "yyyyMMdd"
                        if samples.rows
                        and all(re.fullmatch(r"\d{8}", str(r["dt"])) for r in samples.rows)
                        else "unknown"
                    )
                    add(
                        "fixture_partition",
                        "passed_with_warnings",
                        f"dt type={fixture_schema.partition.type}; "
                        f"is_partition={fixture_schema.partition.is_partition}; "
                        f"format={fmt}; relation_to_business_time=unknown; manual_review_required",
                    )
                except Exception:
                    add("fixture_partition", "not_verified", "Partition format unavailable")
            else:
                add("fixture_parity", "not_verified", "Explicit fixture mapping is disabled")
        else:
            add(
                "connectivity",
                "not_verified" if unconfigured else "failed",
                "No configuration"
                if unconfigured
                else "connection_error; no real environment evidence available",
            )
            capabilities = dict.fromkeys(CAPABILITIES, "not_verified")
        add(
            "client_timeout",
            "not_verified",
            "Client deadline is unit tested; no deliberately slow remote query was run",
        )
        add(
            "read_only_identity",
            "passed" if self.settings.spark_read_only_attested else "not_verified",
            "Operator attestation; no destructive permission test",
            "operator_attestation",
        )
        for kind in (
            "timezone",
            "source_schema",
            "decimal_semantics",
            "max_rows",
            "cancel_requested",
            "remote_cancel",
            "fixture_parity",
            "source_partition_format",
        ):
            if kind == "fixture_parity" and parity:
                continue
            if not any(e.evidence_type == kind for e in evidence):
                add(kind, "not_verified", "No direct observation; manual verification required")
        states = [e.status for e in evidence] + [p.status for p in parity]
        overall = (
            "failed"
            if "failed" in states
            else "passed_with_warnings"
            if any(s != "passed" for s in states)
            else "passed"
        )
        return EnvironmentValidationReport(
            started_at=started,
            finished_at=datetime.now(UTC),
            overall="not_verified" if unconfigured else overall,
            connectivity="passed"
            if status.reachable
            else "not_verified"
            if unconfigured
            else "failed",
            engine_version=status.engine_version,
            session_timezone=status.session_timezone,
            capabilities=capabilities,
            source_schema=schema,
            fixture_parity=parity,
            evidence=evidence,
        )


def save_report(session, report, request_id):
    from airi.environments.persistence import EnvironmentValidationRow

    session.add(
        EnvironmentValidationRow(
            validation_run_id=report.validation_run_id,
            environment=report.environment,
            profile_version=report.profile_version,
            status=report.overall,
            engine_version=report.engine_version,
            session_timezone=report.session_timezone,
            started_at=report.started_at.replace(tzinfo=None),
            finished_at=report.finished_at.replace(tzinfo=None),
            created_at=datetime.now(UTC).replace(tzinfo=None),
            report_json=report.model_dump(mode="json"),
        )
    )
    session.commit()
    logging.getLogger("airi.environment").info(
        "environment_validated",
        extra={
            "request_id": request_id,
            "validation_run_id": report.validation_run_id,
            "status": report.overall,
        },
    )
