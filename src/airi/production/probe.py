"""Phase 8 production runtime connection and probe.

The provider boundary is split in two so a partially configured platform cannot
be mistaken for a fully verified one:

* :class:`SparkRuntimeConnection` - connectivity, read-only queries, an optional
  cancel probe and (when the platform exposes a control channel) the metric
  activation ledger.
* :func:`run_production_probe` - turns that connection into the structured
  :class:`~airi.production.models.ProductionRuntimeProbe` evidence.

Every value a cluster cannot answer stays ``None``. Nothing here fabricates an
identity, a job id, an engine version or a read-only guarantee. ``NOSASL`` is
connectivity, not authentication, and is reported as such - which is why a
NOSASL-only cluster can never produce ``production_deployed = true``.
"""

import logging
import re
import time
from contextlib import suppress
from typing import Protocol

from airi.environments.probes import production_probe_sql
from airi.production.models import (
    ProbeCapability,
    ProductionRuntimeProbe,
    ProviderActivation,
    ProviderQueryResult,
    RuntimeAuthMode,
)

logger = logging.getLogger("airi.production")

# A runtime user is only evidence if the cluster named it. These are the
# allowlisted probes tried, in order; a cluster that exposes none of them leaves
# `runtime_identity` None and the identity therefore unverified.
IDENTITY_PROBES = ("runtime_user", "managed_runtime_user")

_LEDGER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_LITERAL_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class SparkRuntimeConnection(Protocol):
    """The narrow surface a production runtime may be asked for."""

    name: str
    synthetic: bool

    @property
    def supports_activation(self) -> bool: ...

    def run_query(
        self, sql: str, *, max_rows: int, timeout_seconds: int
    ) -> ProviderQueryResult: ...

    def activate_metric(
        self, *, deployment_id: str, metric_version_id: str
    ) -> ProviderActivation: ...

    def active_metric_versions(self) -> list[ProviderActivation]: ...

    def deactivate_metric(
        self, *, deployment_id: str, target_version_id: str
    ) -> ProviderActivation: ...

    def close(self) -> None: ...


def _rows(result: ProviderQueryResult) -> list[dict]:
    return result.rows if result.status == "success" else []


def _first_scalar(result: ProviderQueryResult, key: str) -> str | None:
    for row in _rows(result):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def run_production_probe(
    connection: SparkRuntimeConnection, environment_id: str
) -> ProductionRuntimeProbe:
    """Observe a runtime. A missing capability becomes ``not_verified``, never a guess.

    A probe that cannot reach the cluster returns ``reachable=False`` with a
    failure category; it never raises past this function, because "we could not
    verify" is itself the evidence an operator needs.
    """
    probe = ProductionRuntimeProbe(
        environment_id=environment_id,
        adapter=connection.name,
        auth_mode=_auth_mode(connection),
        synthetic=connection.synthetic,
    )
    try:
        return _observe(connection, probe)
    except Exception as exc:  # noqa: BLE001 - provider exceptions are opaque here
        logger.warning(
            "production_probe_failed",
            extra={"error_type": type(exc).__name__, "environment_id": environment_id},
        )
        return probe.model_copy(
            update={
                "reachable": False,
                "failure_category": type(exc).__name__,
                "detail": "production runtime probe did not complete",
            }
        )


def _observe(
    connection: SparkRuntimeConnection, probe: ProductionRuntimeProbe
) -> ProductionRuntimeProbe:
    connectivity = connection.run_query(
        production_probe_sql("connectivity"), max_rows=1, timeout_seconds=10
    )
    if connectivity.status != "success":
        return probe.model_copy(
            update={
                "reachable": False,
                "failure_category": connectivity.failure_category or "unknown",
                "detail": "production runtime refused the connectivity probe",
            }
        )

    capabilities: dict[str, ProbeCapability] = {"connectivity": "verified"}

    session = connection.run_query(production_probe_sql("session"), max_rows=1, timeout_seconds=10)
    capabilities["session"] = "verified" if session.status == "success" else "unsupported"

    database = connection.run_query(
        production_probe_sql("current_database"), max_rows=1, timeout_seconds=10
    )
    current_database = _first_scalar(database, "current_database")
    capabilities["current_database"] = "verified" if current_database else "unsupported"

    catalog = connection.run_query(production_probe_sql("catalog"), max_rows=1, timeout_seconds=10)
    catalog_identity = _first_scalar(catalog, "catalog_identity")
    capabilities["catalog"] = "verified" if catalog_identity else "unsupported"

    runtime_identity: str | None = None
    identity_source = "none"
    for probe_name in IDENTITY_PROBES:
        attempt = connection.run_query(
            production_probe_sql(probe_name), max_rows=1, timeout_seconds=10
        )
        candidate = _first_scalar(attempt, "runtime_user")
        if candidate:
            runtime_identity, identity_source = candidate, "session"
            capabilities[probe_name] = "verified"
            break
        capabilities[probe_name] = "unsupported"

    provider_query_id = connectivity.provider_query_id
    capabilities["query_id"] = "verified" if provider_query_id else "unsupported"

    cancel_capability: ProbeCapability = "not_verified"
    canceller = getattr(connection, "cancel_probe", None)
    if callable(canceller):
        with suppress(Exception):
            cancel_capability = "verified" if canceller() else "unsupported"
    capabilities["cancel"] = cancel_capability

    return probe.model_copy(
        update={
            "reachable": True,
            "authenticated": _auth_mode(connection) in {"ldap", "kerberos", "gateway"},
            "engine_version": _first_scalar(session, "engine_version"),
            "session_timezone": _first_scalar(session, "session_timezone"),
            "current_database": current_database,
            "catalog_identity": catalog_identity,
            "cluster_identifier": _optional(connection, "cluster_identifier"),
            "runtime_identity": runtime_identity,
            "runtime_identity_source": identity_source,
            "read_only_capability": _read_only_capability(connection),
            "query_id_available": bool(provider_query_id),
            "cancel_capability": cancel_capability,
            "provider_application_id": _optional(connection, "provider_application_id"),
            "provider_query_id": provider_query_id,
            "capabilities": capabilities,
        }
    )


def _auth_mode(connection) -> RuntimeAuthMode:
    return getattr(connection, "auth_mode", "unknown")


def _read_only_capability(connection) -> ProbeCapability:
    """Read-only is an operator attestation, not something a cluster can prove."""
    return "verified" if getattr(connection, "read_only_attested", False) else "not_verified"


def _optional(connection, attribute: str) -> str | None:
    value = getattr(connection, attribute, None)
    return str(value) if value else None


class ThriftSparkConnection:
    """Real Spark Thrift Server connection used only for read-only production work.

    Authentication is delegated to the platform: ``LDAP`` uses credentials this
    process receives from configuration, ``NOSASL`` performs no authentication at
    all, and ``KERBEROS``/``GATEWAY`` are reported as unsupported rather than
    half-implemented. AIRI never manages a keytab, a KDC or a ticket cache.

    Deployment control requires an explicit activation-ledger table. Without one,
    ``supports_activation`` is ``False`` and the deployment path stays NOT
    VERIFIED instead of pretending a read-only SQL query deployed something.
    """

    name = "spark_production"
    synthetic = False

    def __init__(self, settings):
        self.settings = settings
        self.host = settings.spark_production_host
        self.port = settings.spark_production_port
        self.username = settings.spark_production_username
        self.database = settings.spark_production_database
        self.auth_mode: RuntimeAuthMode = _normalise_auth_mode(settings.spark_production_auth_mode)
        self._password = settings.spark_production_password.get_secret_value()
        self.connect_timeout = settings.spark_production_connect_timeout_seconds
        self.query_timeout = settings.spark_production_query_timeout_seconds
        self.read_only_attested = settings.spark_production_read_only_attested
        self.cluster_identifier = settings.spark_production_cluster_identifier or None
        self.catalog = settings.spark_production_catalog or None
        self.provider_application_id: str | None = None
        self.ledger = settings.spark_production_activation_ledger
        if self.ledger and not _LEDGER_PATTERN.match(self.ledger):
            raise ValueError("activation ledger must be a plain table identifier")
        self._connection = None

    # ------------------------------------------------------------- lifecycle

    @property
    def supports_activation(self) -> bool:
        return bool(self.ledger) and self.configured

    @property
    def configured(self) -> bool:
        return bool(self.host) and bool(self.username)

    def _connect(self):
        if self._connection is not None:
            return self._connection
        from pyhive import hive
        from thrift.transport import TSocket, TTransport

        if self.auth_mode == "kerberos":
            raise RuntimeError("kerberos requires platform-side infrastructure")
        if self.auth_mode == "gateway":
            raise RuntimeError("gateway auth is provided by the platform proxy")
        if self.auth_mode == "unknown":
            raise RuntimeError("production auth mode is not supported by this adapter")
        socket = TSocket.TSocket(self.host, self.port)
        socket.setTimeout(self.connect_timeout * 1000)
        transport = TTransport.TBufferedTransport(socket)
        options = (
            {"auth": "LDAP", "password": self._password}
            if self.auth_mode == "ldap"
            else {"auth": "NOSASL"}
        )
        self._connection = hive.Connection(
            thrift_transport=transport,
            username=self.username,
            database=self.database,
            **options,
        )
        socket.setTimeout(self.query_timeout * 1000)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            with suppress(Exception):
                self._connection.close()
            self._connection = None

    # ---------------------------------------------------------------- queries

    def run_query(self, sql: str, *, max_rows: int, timeout_seconds: int) -> ProviderQueryResult:
        from airi.infrastructure.query_executor import QueryFailure, classify_provider_error

        started = _millis()
        cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(sql, async_=True)
            deadline = time.monotonic() + min(timeout_seconds, self.query_timeout)
            while True:
                state = cursor.poll().operationState
                if state == 2:  # HiveServer2 FINISHED_STATE
                    break
                if state not in (0, 1, 7):  # INITIALIZED, RUNNING, PENDING
                    raise QueryFailure("execution_error")
                if time.monotonic() >= deadline:
                    raise QueryFailure("timeout")
            columns = [item[0] for item in cursor.description or []]
            rows = [
                dict(
                    zip(
                        columns,
                        (None if value is None else str(value) for value in row),
                        strict=True,
                    )
                )
                for row in cursor.fetchmany(max_rows)
            ]
            return ProviderQueryResult(
                status="success",
                columns=columns,
                rows=rows,
                truncated=len(rows) >= max_rows,
                provider_query_id=_operation_guid(cursor),
                duration_ms=_millis() - started,
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderQueryResult(
                status="failed",
                failure_category=classify_provider_error(exc),
                duration_ms=_millis() - started,
            )
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()

    def cancel_probe(self) -> bool:
        """Ask the cluster whether a running statement can be cancelled."""
        from airi.environments.probes import production_probe_sql

        cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(production_probe_sql("cancel"), async_=True)
            cursor.cancel()
            return cursor.poll().operationState in (2, 3)  # FINISHED or CANCELED
        except Exception:  # noqa: BLE001
            return False
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()

    # ------------------------------------------------- activation control path

    def active_metric_versions(self) -> list[ProviderActivation]:
        if not self.supports_activation:
            return []
        result = self.run_query(
            f"SELECT deployment_id, metric_version_id, state FROM {self.ledger}",
            max_rows=1000,
            timeout_seconds=self.query_timeout,
        )
        return [
            ProviderActivation(
                deployment_id=str(row.get("deployment_id") or ""),
                metric_version_id=row.get("metric_version_id"),
                runtime_state=_ledger_state(row.get("state")),
            )
            for row in _rows(result)
        ]

    def activate_metric(self, *, deployment_id: str, metric_version_id: str) -> ProviderActivation:
        return self._write_activation(
            deployment_id=deployment_id,
            metric_version_id=metric_version_id,
            runtime_state="active",
        )

    def deactivate_metric(
        self, *, deployment_id: str, target_version_id: str
    ) -> ProviderActivation:
        """Roll the runtime back to ``target_version_id``.

        After a rollback the metric is still active in the runtime - on the target
        version. Recording it as anything else would make a post-rollback
        convergence check unable to see that the rollback landed.
        """
        return self._write_activation(
            deployment_id=deployment_id,
            metric_version_id=target_version_id,
            runtime_state="active",
        )

    def _write_activation(
        self, *, deployment_id: str, metric_version_id: str, runtime_state: str
    ) -> ProviderActivation:
        if not self.supports_activation:
            return ProviderActivation(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="no production activation control channel is configured",
            )
        # The statement shape is the documented platform assumption, not a
        # verified fact: it is reconciled with the real platform during Phase 9
        # acceptance. Both values are validated so neither can carry SQL.
        if not _LITERAL_PATTERN.match(deployment_id) or not _LITERAL_PATTERN.match(
            metric_version_id
        ):
            return ProviderActivation(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="activation identifiers are not ledger-safe",
            )
        statement = (
            f"INSERT INTO {self.ledger} "
            f"SELECT '{deployment_id}' AS deployment_id, "
            f"'{metric_version_id}' AS metric_version_id, '{runtime_state}' AS state"
        )
        result = self.run_query(statement, max_rows=1, timeout_seconds=self.query_timeout)
        if result.status != "success":
            return ProviderActivation(
                deployment_id=deployment_id,
                runtime_state="unknown",
                detail="activation ledger write was refused",
            )
        return ProviderActivation(
            deployment_id=deployment_id,
            metric_version_id=metric_version_id,
            runtime_state=runtime_state,
            provider_query_id=result.provider_query_id,
        )


def _normalise_auth_mode(value: str) -> RuntimeAuthMode:
    return {
        "NOSASL": "nosasl",
        "LDAP": "ldap",
        "KERBEROS": "kerberos",
        "GATEWAY": "gateway",
    }.get(value, "unknown")


def _ledger_state(value) -> str:
    text = (value or "").strip().lower()
    return text if text in {"active", "rolled_back", "failed", "shadow"} else "unknown"


def _operation_guid(cursor) -> str | None:
    handle = getattr(cursor, "_operationHandle", None)
    guid = getattr(getattr(handle, "operationId", None), "guid", None)
    return guid.hex() if isinstance(guid, bytes) else None


def _millis() -> int:
    return int(time.monotonic() * 1000)
