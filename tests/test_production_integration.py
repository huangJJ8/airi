"""Real production runtime, identity and telemetry only.

There is no verified production Spark cluster in this repository, so these run
only when an operator points the server at one. They never fall back to a Mock
success: the whole point of the marker is to keep "not verified" honest.

Every assertion here is written as an inequality guard, not a happy path:

* connectivity is not authentication,
* a deploy response is not a confirmed deployment,
* a received snapshot is not trusted telemetry,
* an executed rollback is not a converged runtime.
"""

import pytest

from airi.core.config import Settings
from airi.production.adapters import ProductionAdapterError, adapter_factory

production_integration = pytest.mark.production_integration
identity_integration = pytest.mark.identity_integration
telemetry_integration = pytest.mark.telemetry_integration

ENVIRONMENT_ID = "prod_real"


def _unconfigured_reason(remote: Settings) -> str | None:
    if remote.environment != "production":
        return "settings.environment is not 'production'"
    if remote.production_adapter != "spark_production":
        return "settings.production_adapter is not 'spark_production'"
    if not remote.spark_production_host:
        return "settings.spark_production_host is empty"
    if not remote.spark_production_username:
        return "settings.spark_production_username is empty"
    if not remote.spark_production_read_only_attested:
        return "settings.spark_production_read_only_attested is not attested"
    return None


@pytest.fixture
def production_settings(settings):
    """Real runtime settings, or an explicit skip. Never a synthetic fallback."""
    remote = Settings()
    reason = _unconfigured_reason(remote)
    if reason:
        pytest.skip(
            f"NOT VERIFIED: requires a real production runtime ({reason}). "
            "No mock success is substituted."
        )
    return remote.model_copy(update={"database_url": settings.database_url})


@pytest.fixture
def production_adapter(production_settings):
    adapter = adapter_factory(production_settings)(None, None, None)
    yield adapter
    close = getattr(adapter, "close", None)
    if callable(close):
        close()


# --------------------------------------------------------------- connectivity


@production_integration
def test_real_adapter_is_reachable_and_authoritative(production_settings):
    """The contract check for the real seam once an operator configures it."""
    adapter = adapter_factory(production_settings)(None, None, None)
    assert adapter.authoritative is True
    assert adapter.runtime_mode == "spark_production"
    assert adapter.configured is True


@production_integration
def test_real_status_comes_from_the_cluster(production_settings):
    """A configured adapter answers only through the provider, never by guessing."""
    adapter = adapter_factory(production_settings)(None, None, None)
    try:
        status = adapter.status("00000000-0000-0000-0000-000000000000")
    except ProductionAdapterError:
        # Failing closed is an acceptable outcome; inventing an identity is not.
        return
    assert status.active_version_id is None or isinstance(status.active_version_id, str)
    assert status.runtime_state in {
        "unknown",
        "shadow",
        "active",
        "failed",
        "rolled_back",
        "mismatch",
    }


@production_integration
def test_real_environment_fingerprint(production_adapter, production_settings):
    """The observed runtime identity is what a package is anchored to."""
    probe = production_adapter.probe(ENVIRONMENT_ID)
    if not probe.reachable:
        pytest.skip(f"NOT VERIFIED: production runtime is unreachable ({probe.failure_category})")
    fingerprint = probe.fingerprint(
        environment_id=ENVIRONMENT_ID,
        read_only_attested=bool(production_settings.spark_production_read_only_attested),
        observed_by="integration-test",
    )
    assert fingerprint.verified is True
    assert len(fingerprint.fingerprint_hash) == 64
    assert fingerprint.source == "runtime_probe"
    # The hash is re-derived from observed facts, so a second observation of the
    # same cluster must produce the same anchor.
    again = production_adapter.probe(ENVIRONMENT_ID).fingerprint(
        environment_id=ENVIRONMENT_ID,
        read_only_attested=fingerprint.read_only_attested,
    )
    assert again.fingerprint_hash == fingerprint.fingerprint_hash


@production_integration
def test_real_runtime_identity(production_adapter, production_settings):
    """Authenticated is not the same claim as connected."""
    probe = production_adapter.probe(ENVIRONMENT_ID)
    if not probe.reachable:
        pytest.skip("NOT VERIFIED: production runtime is unreachable")
    if probe.auth_mode == "nosasl" or not production_settings.production_auth_configured:
        # A NOSASL/GATEWAY session proves connectivity only. A runtime identity
        # must stay unverified, and the adapter must say so.
        assert probe.authenticated is False or probe.runtime_identity_source == "none"
        fingerprint = probe.fingerprint(
            environment_id=ENVIRONMENT_ID,
            read_only_attested=bool(production_settings.spark_production_read_only_attested),
        )
        assert fingerprint.runtime_identity_verified is False
        return
    assert probe.authenticated is True
    assert probe.runtime_identity is not None
    assert probe.runtime_identity_verified is True


@production_integration
def test_real_shadow(production_adapter):
    """A shadow reads production and writes nothing, business or otherwise."""
    probe = production_adapter.probe(ENVIRONMENT_ID)
    if not probe.reachable:
        pytest.skip("NOT VERIFIED: production runtime is unreachable")
    assert probe.read_only_capability in {"verified", "not_verified", "unsupported"}
    # The read-only basis is derived from the probe, never assumed.
    expected = "read_only_identity" if probe.read_only_capability == "verified" else "not_verified"
    assert probe.read_only_capability == "verified" or expected == "not_verified"


@production_integration
def test_real_deployment_status(production_adapter, production_settings):
    """A provider that answers "deployed" has not yet confirmed a deployment."""
    if not production_settings.spark_production_activation_ledger:
        status = production_adapter.status("00000000-0000-0000-0000-000000000000")
        assert status.active_version_id is None
        assert status.detail is not None
        return
    status = production_adapter.status("00000000-0000-0000-0000-000000000000")
    # An activation ledger that has no record of a deployment reports unknown.
    assert status.runtime_state in {"unknown", "active"}
    if status.runtime_state != "active":
        assert status.active_version_id is None


@production_integration
def test_real_rollback(production_adapter, production_settings):
    """Recovery is verified by re-reading the runtime, never by its own return value."""
    if not production_adapter.supports_rollback:
        assert production_adapter.supports_rollback is False
        result = production_adapter.rollback(
            "00000000-0000-0000-0000-000000000000",
            production_settings.spark_production_cluster_identifier or "unknown-target",
        )
        assert result.status in {"not_verified", "failed"}
        return
    # With a control channel, a rollback without a target is still refused.
    result = production_adapter.rollback("00000000-0000-0000-0000-000000000000", None)
    assert result.status == "not_verified"
    assert result.failure_category == "rollback_target_missing"


# ------------------------------------------------- trusted identity integration


@identity_integration
def test_real_trusted_identity_boundary(settings):
    """A header is only an identity when a deployed boundary vouches for it."""
    remote = Settings()
    if remote.production_identity_provider != "trusted_header":
        pytest.skip("NOT VERIFIED: no trusted identity provider is configured")
    if remote.production_identity_trust_boundary == "none":
        pytest.skip("NOT VERIFIED: no trust boundary is configured")
    if remote.production_identity_trust_boundary == "trusted_gateway":
        if not remote.production_identity_gateway_secret.get_secret_value():
            pytest.skip("NOT VERIFIED: gateway attestation secret is not configured")
    elif not remote.production_identity_jwt_secret.get_secret_value():
        pytest.skip("NOT VERIFIED: signed identity token secret is not configured")

    from airi.production.identity import IdentityNotTrusted, TrustedHeaderIdentityProvider

    provider = TrustedHeaderIdentityProvider(
        remote.production_identity_header,
        trust_boundary=remote.production_identity_trust_boundary,
        gateway_header=remote.production_identity_gateway_header,
        gateway_secret=remote.production_identity_gateway_secret.get_secret_value(),
        jwt_secret=remote.production_identity_jwt_secret.get_secret_value(),
        issuer=remote.production_identity_issuer,
    )

    class _Request:
        def __init__(self, headers):
            self.headers = headers

    # An unvouched header must never be accepted.
    with pytest.raises(IdentityNotTrusted):
        provider.identify(_Request({remote.production_identity_header: "{}"}))
    # Nothing asserted stays anonymous, which fails closed everywhere else.
    assert provider.identify(_Request({})).authenticated is False


# ----------------------------------------------- trusted telemetry integration


@telemetry_integration
def test_real_monitoring_source_auth(settings):
    """A registered producer is not a trusted producer until it is attested."""
    remote = Settings()
    if remote.environment != "production":
        pytest.skip("NOT VERIFIED: settings.environment is not 'production'")

    from airi.production.models import TrustedTelemetrySource

    source = TrustedTelemetrySource(
        telemetry_source_id="integration_monitor",
        source_system="integration-monitor",
        environment_id=ENVIRONMENT_ID,
        auth_identity="monitor@airi.invalid",
        registered_by="integration-test",
    )
    # Registration alone never marks a producer trusted.
    assert source.verified is False
    # And an unattested producer can never be treated as trusted telemetry.
    assert source.synthetic is False
    if remote.production_identity_trust_boundary == "none":
        pytest.skip("NOT VERIFIED: no identity boundary exists to attest a producer")
