"""Production identity boundary.

Phase 6 accepted `reviewer` as a caller-supplied string, which is adequate for a
research registry but not for a production gate. From here on every production
action resolves an :class:`ActorIdentity` from an external trusted identity and
is authorised against a fixed role list.

AIRI never implements credentials. It either trusts an upstream gateway
(:class:`TrustedHeaderIdentityProvider`) or it fails closed
(:class:`DisabledIdentityProvider`). The mock provider exists for tests and the
synthetic demo only and is never selectable through configuration.

Phase 8 adds the missing half of "trusted header": a *boundary*. A header is only
trusted when the deployment proves one - either the gateway overwrites it behind a
shared attestation, or the identity arrives as a signed token. Without either,
the provider returns ``ANONYMOUS`` and the request fails closed as
``actor_identity_required``. A plain header reachable by any client is not
identity, and AIRI refuses to pretend otherwise.
"""

import base64
import hashlib
import hmac
import json
from typing import Literal, Protocol

from pydantic import Field

from airi.core.exceptions import AIRIError
from airi.core.schemas import NonEmptyText, StrictSchema

Role = Literal[
    "metric_reviewer",
    "release_reviewer",
    "production_deployer",
    "rollback_approver",
    "admin",
]

AuthSource = Literal["trusted_header", "external_jwt", "mock", "anonymous"]
TrustBoundary = Literal["none", "trusted_gateway", "signed_jwt"]

ROLES: tuple[Role, ...] = (
    "metric_reviewer",
    "release_reviewer",
    "production_deployer",
    "rollback_approver",
    "admin",
)

# Action -> roles that may perform it. `admin` satisfies every entry.
ACTION_ROLES: dict[str, tuple[Role, ...]] = {
    "register_production_environment": ("admin",),
    "verify_production_environment": ("admin",),
    "register_trusted_telemetry_source": ("admin",),
    "review_production_deployment": ("production_deployer", "admin"),
    "request_production_deployment": ("production_deployer", "admin"),
    "approve_production_rollback": ("rollback_approver", "admin"),
    "plan_production_reconciliation": ("production_deployer", "admin"),
    "execute_production_reconciliation": ("production_deployer", "admin"),
    "align_runtime_to_registry": ("production_deployer", "admin"),
    "align_registry_to_runtime": ("production_deployer", "admin"),
    "keep_mismatch_for_investigation": (
        "production_deployer",
        "rollback_approver",
        "metric_reviewer",
        "release_reviewer",
        "admin",
    ),
    "publish_monitoring_snapshot": (
        "production_deployer",
        "release_reviewer",
        "metric_reviewer",
        "rollback_approver",
        "admin",
    ),
    "record_feedback": (
        "production_deployer",
        "release_reviewer",
        "metric_reviewer",
        "rollback_approver",
        "admin",
    ),
    "decide_feedback_research": ("metric_reviewer", "admin"),
}

# Auth sources that came from a verified upstream boundary rather than a test.
TRUSTED_AUTH_SOURCES: frozenset[str] = frozenset({"trusted_header", "external_jwt"})


class ActorIdentity(StrictSchema):
    """Who is acting, and why we believe it. Never carries a credential."""

    actor_id: NonEmptyText
    display_name: NonEmptyText
    auth_source: AuthSource
    roles: tuple[Role, ...] = ()
    issuer: str | None = Field(default=None, max_length=256)

    @property
    def authenticated(self) -> bool:
        return self.auth_source != "anonymous"

    @property
    def trusted(self) -> bool:
        """A verified upstream boundary asserted this identity."""
        return self.auth_source in TRUSTED_AUTH_SOURCES

    def holds(self, *roles: Role) -> bool:
        return "admin" in self.roles or any(role in self.roles for role in roles)


ANONYMOUS = ActorIdentity(
    actor_id="anonymous",
    display_name="Anonymous",
    auth_source="anonymous",
    roles=(),
)


class IdentityRequired(AIRIError):
    code = "actor_identity_required"
    status_code = 401


class IdentityNotTrusted(AIRIError):
    """Identity material arrived, but no trusted boundary vouches for it."""

    code = "identity_not_trusted"
    status_code = 401


class ActorNotAuthorized(AIRIError):
    code = "actor_not_authorized"
    status_code = 403


def authorize(identity: ActorIdentity, action: str) -> ActorIdentity:
    """Fail closed: an unauthenticated caller never reaches a production action."""
    if action not in ACTION_ROLES:
        raise KeyError(action)
    if not identity.authenticated:
        raise IdentityRequired("authenticated actor required")
    if not identity.holds(*ACTION_ROLES[action]):
        raise ActorNotAuthorized(f"role required for {action}")
    return identity


def require_authenticated(identity: ActorIdentity) -> ActorIdentity:
    if not identity.authenticated:
        raise IdentityRequired("authenticated actor required")
    return identity


def require_trusted(identity: ActorIdentity) -> ActorIdentity:
    """Phase 8: an action that must come from a verified boundary, not a test mock."""
    require_authenticated(identity)
    if not identity.trusted:
        raise IdentityNotTrusted("trusted identity boundary required")
    return identity


class TrustedIdentityProvider(Protocol):
    """The only place a caller identity may be resolved."""

    def identify(self, request) -> ActorIdentity: ...


class DisabledIdentityProvider:
    """No trusted identity infrastructure configured: everybody is anonymous.

    Used in production mode when no gateway header is configured, so every
    production action fails closed instead of trusting a client-supplied name.
    """

    name = "disabled"

    def identify(self, request) -> ActorIdentity:  # noqa: ARG002
        return ANONYMOUS


class TrustedHeaderIdentityProvider:
    """Read an identity asserted by an upstream gateway, behind a proven boundary.

    The header is trusted *only* because the deployment proves one of:

    * ``trusted_gateway`` - a second header carries a shared attestation value
      that the gateway overwrites, so a client cannot forge either header; or
    * ``signed_jwt`` - the identity header carries an HS256 token this process can
      verify, so a client cannot mint one.

    With boundary ``none`` the provider refuses to trust anything. AIRI performs
    no credential verification of its own beyond that boundary check; it never
    manages sessions, MFA or token lifecycles.
    """

    name = "trusted_header"

    def __init__(
        self,
        header: str,
        *,
        trust_boundary: TrustBoundary = "none",
        gateway_header: str = "x-airi-gateway-attestation",
        gateway_secret: str = "",
        jwt_secret: str = "",
        issuer: str = "",
    ):
        self.header = header.lower()
        self.trust_boundary: TrustBoundary = trust_boundary
        self.gateway_header = gateway_header.lower()
        self.gateway_secret = gateway_secret
        self.jwt_secret = jwt_secret
        self.issuer = issuer

    def identify(self, request) -> ActorIdentity:
        raw = request.headers.get(self.header)
        if not raw:
            # Nothing was asserted. Anonymous, and every production action fails
            # closed on `actor_identity_required`.
            return ANONYMOUS
        if self.trust_boundary == "trusted_gateway":
            return self._from_gateway(request, raw)
        if self.trust_boundary == "signed_jwt":
            return self._from_token(raw)
        # Identity material with no boundary: explicitly untrusted, not anonymous.
        raise IdentityNotTrusted("no trusted identity boundary is configured")

    # ---------------------------------------------------------------- boundary

    def _from_gateway(self, request, raw: str) -> ActorIdentity:
        if not self.gateway_secret:
            raise IdentityNotTrusted("gateway attestation is not configured")
        presented = request.headers.get(self.gateway_header, "")
        if not hmac.compare_digest(presented, self.gateway_secret):
            raise IdentityNotTrusted("gateway attestation did not match")
        return self._decode_identity(raw)

    def _from_token(self, raw: str) -> ActorIdentity:
        if not self.jwt_secret:
            raise IdentityNotTrusted("signed identity token verification is not configured")
        claims = _verify_hs256(raw, self.jwt_secret, self.issuer)
        roles = tuple(role for role in _as_sequence(claims.get("roles")) if role in ROLES)
        return ActorIdentity(
            actor_id=_required_claim(claims, "sub"),
            display_name=_required_claim(claims, "name", fallback="sub"),
            auth_source="external_jwt",
            roles=roles,
            issuer=claims.get("iss") or None,
        )

    def _decode_identity(self, raw: str) -> ActorIdentity:
        try:
            return ActorIdentity.model_validate_json(raw)
        except Exception as exc:
            raise IdentityNotTrusted("gateway identity payload was not readable") from exc


def _as_sequence(value) -> list:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _required_claim(claims: dict, key: str, *, fallback: str | None = None) -> str:
    value = claims.get(key)
    if value is None and fallback:
        value = claims.get(fallback)
    if not isinstance(value, str) or not value.strip():
        raise IdentityNotTrusted(f"identity token is missing the {key} claim")
    return value.strip()


def _verify_hs256(token: str, secret: str, issuer: str) -> dict:
    """Minimal HS256 verification. No key management, no token lifecycle."""
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityNotTrusted("identity token is not a compact JWS")
    header_segment, payload_segment, signature_segment = parts
    signed = f"{header_segment}.{payload_segment}".encode()
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).digest()
    try:
        presented = _b64url_decode(signature_segment)
    except Exception as exc:
        raise IdentityNotTrusted("identity token signature is not decodable") from exc
    if not hmac.compare_digest(expected, presented):
        raise IdentityNotTrusted("identity token signature did not verify")
    try:
        header = json.loads(_b64url_decode(header_segment))
        claims = json.loads(_b64url_decode(payload_segment))
    except Exception as exc:
        raise IdentityNotTrusted("identity token payload was not readable") from exc
    if header.get("alg") != "HS256":
        raise IdentityNotTrusted("identity token algorithm is not accepted")
    if issuer and claims.get("iss") != issuer:
        raise IdentityNotTrusted("identity token issuer did not match")

    return claims


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class MockIdentityProvider:
    """Explicit test injection. Never reachable from configuration.

    Actors are resolved from a request header against a fixed table, so a test
    can exercise several roles without any authentication infrastructure.
    """

    name = "mock"
    header = "x-airi-actor"

    def __init__(self, actors: dict[str, ActorIdentity]):
        self.actors = actors

    def identify(self, request) -> ActorIdentity:
        key = request.headers.get(self.header)
        return self.actors.get(key, ANONYMOUS) if key else ANONYMOUS


def actor(
    actor_id: str,
    roles: tuple[Role, ...],
    *,
    display_name: str | None = None,
    auth_source: AuthSource = "mock",
    issuer: str | None = None,
) -> ActorIdentity:
    return ActorIdentity(
        actor_id=actor_id,
        display_name=display_name or actor_id.replace("-", " ").title(),
        auth_source=auth_source,
        roles=roles,
        issuer=issuer,
    )
