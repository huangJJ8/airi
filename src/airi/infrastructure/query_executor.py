"""Provider boundary. No provider exception text crosses this module."""

import hashlib
import logging
import multiprocessing
import re
import sqlite3
import time
from abc import ABC, abstractmethod
from contextlib import suppress

from airi.execution.models import ExecutionPlan, FailureCategory, QueryOutput, ResultColumn
from airi.infrastructure.result_normalizer import ResultNormalizer


class QueryFailure(Exception):
    def __init__(self, category: FailureCategory, *, cancel_requested=None):
        self.category = category
        self.cancel_requested = cancel_requested
        super().__init__(category)


def classify_provider_error(exc: Exception) -> FailureCategory:
    if isinstance(exc, QueryFailure):
        return exc.category
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    if getattr(exc, "category", None) in {
        "metadata_error",
        "unsupported_source_schema",
        "timezone_mismatch",
        "fixture_mismatch",
    }:
        return exc.category
    # Inspect internally; never include provider text in public messages or logs.
    message = str(exc).lower()
    for tokens, category in (
        (("could not connect", "connection refused", "name resolution"), "connection_error"),
        (("parseexception", "syntax error"), "syntax_error"),
        (("table not found", "unresolved_column", "no such column"), "schema_error"),
        (("datatype_mismatch", "type mismatch"), "data_type_error"),
        (("permission denied", "access denied", "authorization"), "permission_error"),
        (("timed out", "timeout"), "timeout"),
        (("out of memory", "resource limit"), "resource_limit"),
    ):
        if any(token in message for token in tokens):
            return category
    return "unknown"


class QueryExecutor(ABC):
    fixture_rows: list[dict] | None = None

    def load_dataset(self, snapshot, plan):
        raise QueryFailure("execution_error")

    @abstractmethod
    def execute(self, code: str, plan: ExecutionPlan) -> QueryOutput:
        """Execute guarded SQL; return at most max_rows + 1 rows."""


class DisabledQueryExecutor(QueryExecutor):
    def execute(self, code, plan):
        raise QueryFailure("permission_error")


class MockQueryExecutor(QueryExecutor):
    """Explicit test injection. SQLite semantics are not Spark certification."""

    def __init__(
        self,
        fixture_rows=None,
        *,
        output=None,
        failure=None,
        dataset_rows=None,
        relation_tables=None,
        named_datasets=None,
    ):
        self.fixture_rows = fixture_rows
        self.output = output
        self.failure = failure
        self.dataset_rows = dataset_rows
        # Synthetic relationship sources, keyed by table name, exposed as `demo`.
        self.relation_tables = relation_tables
        # Optional per-table datasets: several scenario families share one executor.
        self.named_datasets = named_datasets

    def load_dataset(self, snapshot, plan):
        rows = None
        if self.named_datasets is not None:
            rows = self.named_datasets.get(snapshot.source.table)
        if rows is None:
            rows = self.dataset_rows
        if rows is None:
            raise QueryFailure("execution_error")
        rows = [dict(row) for row in rows if row.get("dt") == snapshot.partition.value]
        return QueryOutput(
            columns=[], rows=rows[: plan.max_rows], truncated=len(rows) > plan.max_rows
        )

    def execute(self, code, plan):
        if self.failure:
            raise QueryFailure(self.failure)
        if self.output is not None:
            return self.output
        if self.fixture_rows is None:
            raise QueryFailure("execution_error")
        return self.evaluate(code, self.fixture_rows, plan.max_rows, self.relation_tables)

    @staticmethod
    def evaluate(code, rows, max_rows=1000, relation_tables=None):
        with sqlite3.connect(":memory:") as db:
            db.execute("ATTACH DATABASE ':memory:' AS c_db")
            db.execute(
                "CREATE TABLE c_db.source_fp_jdc_view "
                "(seller_tax_no TEXT, invoice_date TEXT, invoice_amt NUMERIC, dt TEXT)"
            )
            db.executemany(
                "INSERT INTO c_db.source_fp_jdc_view VALUES (?, ?, ?, ?)",
                [
                    (
                        r["seller_tax_no"],
                        r["invoice_date"],
                        None if r["invoice_amt"] is None else str(r["invoice_amt"]),
                        r["dt"],
                    )
                    for r in rows
                ],
            )
            if relation_tables:
                db.execute("ATTACH DATABASE ':memory:' AS demo")
                for table, table_rows in relation_tables.items():
                    _load_relation_table(db, table, table_rows)
            sql = re.sub(r"\bDATE\s+('[0-9-]+')", r"\1", code).replace("<=>", "IS")
            cursor = db.execute(sql)
            names = [item[0] for item in cursor.description]
            columns = [
                ResultColumn(
                    name=n,
                    type="string"
                    if n == "entity_id"
                    else "bigint"
                    if n in {"invoice_count_30d", "related_enterprise_count"}
                    else "double",
                )
                for n in names
            ]
            result = [dict(zip(names, row, strict=True)) for row in cursor.fetchmany(max_rows + 1)]
            return QueryOutput(columns=columns, rows=result, truncated=len(result) > max_rows)


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _load_relation_table(db, table: str, rows: list[dict]) -> None:
    """Create one synthetic relationship table. Names are validated, never interpolated raw."""
    if not IDENTIFIER.fullmatch(table) or not rows:
        return
    columns = sorted({key for row in rows for key in row})
    if not all(IDENTIFIER.fullmatch(column) for column in columns):
        return
    db.execute(f"CREATE TABLE demo.{table} ({', '.join(f'{c} TEXT' for c in columns)})")
    db.executemany(
        f"INSERT INTO demo.{table} VALUES ({', '.join('?' * len(columns))})",
        [[row.get(column) for column in columns] for row in rows],
    )


def run_cursor(connection, code, plan):
    cursor = connection.cursor()
    deadline = time.monotonic() + plan.timeout_seconds
    try:
        cursor.execute(code, async_=True)
        while True:
            operation = cursor.poll()
            state = operation.operationState
            if state == 2:  # HiveServer2 FINISHED_STATE
                break
            if state not in (0, 1, 7):  # INITIALIZED, RUNNING, PENDING
                category = classify_provider_error(
                    RuntimeError(getattr(operation, "errorMessage", "") or "")
                )
                raise QueryFailure(category if category != "unknown" else "execution_error")
            if time.monotonic() >= deadline:
                raise QueryFailure("timeout")
            time.sleep(0.05)
        columns = [
            ResultColumn(name=d[0], type=ResultNormalizer.type_name(d)) for d in cursor.description
        ]
        rows = [
            dict(zip([c.name for c in columns], map(ResultNormalizer.cell, row), strict=True))
            for row in cursor.fetchmany(plan.max_rows + 1)
        ]
        handle = getattr(cursor, "_operationHandle", None)
        guid = getattr(getattr(handle, "operationId", None), "guid", None)
        query_id = guid.hex() if isinstance(guid, bytes) else None
        return QueryOutput(
            columns=columns,
            rows=rows,
            truncated=len(rows) > plan.max_rows,
            provider_query_id=query_id,
        )
    except Exception as exc:
        requested = False
        with suppress(Exception):
            cursor.cancel()
            requested = True
        raise QueryFailure(classify_provider_error(exc), cancel_requested=requested) from None
    finally:
        with suppress(Exception):
            cursor.close()


def _spark_worker(pipe, config, code, plan_data):
    # Child-process wall deadline also bounds blocking connect/fetch/close calls.
    logging.disable(logging.CRITICAL)
    connection = None
    try:
        from pyhive import hive
        from thrift.transport import TSocket, TTransport

        plan = ExecutionPlan.model_validate_json(plan_data)
        socket = TSocket.TSocket(config["host"], config["port"])
        socket.setTimeout(min(plan.timeout_seconds, config.get("connect_timeout", 10)) * 1000)
        transport = TTransport.TBufferedTransport(socket)
        connection = hive.Connection(
            thrift_transport=transport,
            username=config["username"],
            database=config["database"],
        )
        socket.setTimeout(plan.timeout_seconds * 1000)
        from airi.environments.fixtures import FixtureSourceMapping
        from airi.environments.metadata import (
            MetricMetadataValidator,
            SchemaInspector,
            fixture_matches,
        )
        from airi.environments.probes import probe_sql

        if isinstance(code, dict) and "dataset" in code:
            from jinja2 import Environment, PackageLoader, StrictUndefined

            from airi.experiments.models import DatasetSnapshot

            snapshot = DatasetSnapshot.model_validate(code["dataset"])
            template = Environment(
                loader=PackageLoader("airi.tools", "templates"),
                undefined=StrictUndefined,
                autoescape=False,
            )
            sql = template.get_template("spark/evaluation_dataset.sql.j2").render(
                partition_value=snapshot.partition.value,
                entity_key=snapshot.entity_key,
                database=snapshot.source.database,
                table=snapshot.source.table,
            )
            output = run_cursor(connection, sql, plan)
        elif isinstance(code, dict):
            sql = probe_sql(code["probe"], config.get("mapping", False))
            if code["probe"] == "cancel":
                cursor = connection.cursor()
                try:
                    cursor.execute(sql, async_=True)
                    cursor.cancel()
                    state = cursor.poll().operationState
                    output = QueryOutput(
                        columns=[],
                        rows=[],
                        cancel_requested=True,
                        remote_cancel_status="cancelled"
                        if state == 3
                        else "finished"
                        if state == 2
                        else "not_verified",
                    )
                finally:
                    cursor.close()
            else:
                output = run_cursor(connection, sql, plan)
        else:
            from airi.environments.probes import CAPABILITIES

            for probe, (_, expected) in CAPABILITIES.items():
                checked = run_cursor(connection, probe_sql(probe), plan)
                if not checked.rows or list(checked.rows[0].values())[0] != expected:
                    raise QueryFailure("dialect_mismatch")
            session = run_cursor(connection, probe_sql("session"), plan)
            timezone = session.rows[0]["session_timezone"]
            metadata = run_cursor(
                connection,
                probe_sql(
                    "fixture_schema" if config.get("mapping") else "source_schema",
                    config.get("mapping", False),
                ),
                plan.model_copy(update={"max_rows": 1000}),
            )
            schema = SchemaInspector().inspect(metadata, fixture=config.get("mapping", False))
            MetricMetadataValidator().validate(schema, timezone)
            if config.get("mapping"):
                data = run_cursor(
                    connection,
                    probe_sql("fixture_rows", True),
                    plan.model_copy(update={"max_rows": 1000}),
                )
                if not fixture_matches(data):
                    raise QueryFailure("fixture_mismatch")
            sql = FixtureSourceMapping(enabled=config.get("mapping", False)).apply(code)
            output = run_cursor(connection, sql, plan).model_copy(
                update={
                    "executed_sql_hash": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                    "source_mapping": "tmp_db.airi_invoice_fixture"
                    if config.get("mapping")
                    else None,
                }
            )
        pipe.send(("success", output.model_dump_json()))
    except Exception as exc:
        pipe.send(
            (
                "failure",
                {
                    "category": classify_provider_error(exc),
                    "cancel_requested": getattr(exc, "cancel_requested", None),
                },
            )
        )
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()
        pipe.close()


class SparkSQLExecutor(QueryExecutor):
    """Spark Thrift Server NOSASL test adapter; never enabled implicitly."""

    def __init__(self, settings):
        self.config = {
            "host": settings.spark_host or settings.spark_test_host,
            "port": settings.spark_port
            if "spark_port" in settings.model_fields_set
            else settings.spark_test_port,
            "username": settings.spark_username or settings.spark_test_username,
            "database": settings.spark_database
            if "spark_database" in settings.model_fields_set
            else settings.spark_test_database,
            "connect_timeout": settings.spark_connect_timeout_seconds,
            "mapping": settings.spark_fixture_mapping and settings.environment != "production",
        }
        self.settings = settings
        if self.config["mapping"]:
            from airi.environments.fixtures import fixture_rows

            self.fixture_rows = fixture_rows()

    def execute(self, code, plan):
        return self._run(code, plan)

    def load_dataset(self, snapshot, plan):
        base = ExecutionPlan.model_validate(
            plan.model_dump(include=set(ExecutionPlan.model_fields))
        )
        return self._run({"dataset": snapshot.model_dump(mode="json")}, base)

    def probe(self, name):
        from airi.core.execution import ExecutionContext
        from airi.environments.probes import probe_sql

        probe_sql(name, self.config["mapping"])
        plan = ExecutionPlan(
            artifact_id="environment-probe",
            approval_id="environment-probe",
            workflow_run_id="environment-probe",
            request_id="environment-probe",
            sql_hash="0" * 64,
            max_rows=10 if name == "resource" else 1000,
            execution_context=ExecutionContext.model_validate(
                {"anchor_time": "2026-09-09T00:00:00+08:00"}
            ),
        )
        return self._run({"probe": name}, plan)

    def _run(self, code, plan):
        if not self.config["host"] or not self.config["username"]:
            raise QueryFailure("permission_error")
        if self.settings.environment == "production" or self.settings.spark_auth_mode != "NOSASL":
            raise QueryFailure("permission_error")
        plan = plan.model_copy(
            update={
                "max_rows": min(plan.max_rows, self.settings.spark_max_rows),
                "timeout_seconds": min(
                    plan.timeout_seconds, self.settings.spark_query_timeout_seconds
                ),
            }
        )
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_spark_worker, args=(sender, self.config, code, plan.model_dump_json())
        )
        process.start()
        sender.close()
        try:
            if not receiver.poll(plan.timeout_seconds):
                raise QueryFailure("timeout")
            status, payload = receiver.recv()
            if status != "success":
                if isinstance(payload, dict):
                    raise QueryFailure(
                        payload["category"], cancel_requested=payload["cancel_requested"]
                    )
                raise QueryFailure(payload)
            output = QueryOutput.model_validate_json(payload)
            return output.model_copy(
                update={
                    "rows": output.rows[: plan.max_rows],
                    "truncated": output.truncated or len(output.rows) > plan.max_rows,
                }
            )
        finally:
            receiver.close()
            # Let successful workers close their provider session before terminating.
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
