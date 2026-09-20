from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIRI_", env_file=".env", env_file_encoding="utf-8", extra="forbid"
    )

    app_name: str = Field(default="AIRI", min_length=1)
    environment: Literal["local", "test", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    database_url: SecretStr = SecretStr(
        "mysql+pymysql://airi:change-me@127.0.0.1:3306/airi?charset=utf8mb4"
    )
    llm_base_url: HttpUrl = HttpUrl("https://api.openai.com/v1")
    llm_model: str = Field(default="configure-your-model", min_length=1)
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = Field(default=30, gt=0, le=300)
    query_executor: Literal["disabled", "spark_test"] = "disabled"
    spark_test_host: str = ""
    spark_test_port: int = Field(default=10000, ge=1, le=65535)
    spark_test_username: str = ""
    spark_test_database: Literal["c_db", "tmp_db"] = "c_db"
    execution_mode: Literal["disabled", "mock", "spark_test"] = "disabled"
    spark_host: str = ""
    spark_port: int = Field(default=10000, ge=1, le=65535)
    spark_username: str = ""
    spark_password: SecretStr = SecretStr("")
    spark_auth_mode: Literal["NOSASL", "LDAP", "KERBEROS"] = "NOSASL"
    spark_database: Literal["c_db", "tmp_db"] = "c_db"
    spark_connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    spark_query_timeout_seconds: int = Field(default=60, ge=1, le=60)
    spark_session_timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    spark_max_rows: int = Field(default=1000, ge=1, le=1000)
    spark_fixture_mapping: bool = False
    spark_read_only_attested: bool = False

    # Phase 7 production integration. Defaults are deliberately inert: nothing
    # reaches a production runtime and every production action fails closed.
    production_adapter: Literal["disabled", "mock", "spark_production"] = "disabled"
    production_identity_provider: Literal["disabled", "trusted_header"] = "disabled"
    production_identity_header: str = "x-airi-actor"
    spark_production_host: str = ""
    spark_production_port: int = Field(default=10000, ge=1, le=65535)
    spark_production_username: str = ""
    spark_production_database: str = "c_db"
    spark_production_read_only_attested: bool = False

    # Phase 8 real-runtime verification. Nothing here has a usable default: an
    # unconfigured production path reports NOT VERIFIED instead of guessing.
    spark_production_auth_mode: Literal["NOSASL", "LDAP", "KERBEROS", "GATEWAY"] = "NOSASL"
    spark_production_password: SecretStr = SecretStr("")
    spark_production_connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    spark_production_query_timeout_seconds: int = Field(default=60, ge=1, le=300)
    spark_production_session_timezone: str = ""
    spark_production_catalog: str = ""
    # Optional control channel. Without it, connectivity/shadow can be verified
    # while deployment control stays NOT VERIFIED.
    spark_production_activation_ledger: str = ""
    spark_production_cluster_identifier: str = ""

    # Phase 8 trusted identity. A header is only trusted behind a real boundary.
    production_identity_trust_boundary: Literal["none", "trusted_gateway", "signed_jwt"] = "none"
    production_identity_gateway_header: str = "x-airi-gateway-attestation"
    production_identity_gateway_secret: SecretStr = SecretStr("")
    production_identity_jwt_secret: SecretStr = SecretStr("")
    production_identity_issuer: str = ""

    # Phase 9. `auto` lets the environment decide: a synthetic or non-production
    # environment only records the verification result, a real production
    # environment is gated by it. Pinning either value overrides that per deployment.
    production_verification_enforcement: Literal["auto", "report_only", "enforced"] = "auto"
    # Where an alert is delivered. A mock sink exists for tests and the synthetic
    # demo and is deliberately not selectable here: a demo must never look like a
    # configured production notification path.
    production_notification_sink: Literal["disabled", "dingtalk"] = "disabled"
    production_notification_webhook: str = ""

    # Phase 10 local Web demo plumbing. `demo_mock` wires explicit deterministic
    # LLM doubles so the portfolio demo runs without any LLM credentials. It is
    # an explicit opt-in -- never a silent fallback, and never legitimate outside
    # a synthetic local demo.
    llm_mode: Literal["openai_compatible", "demo_mock"] = "openai_compatible"
    # Local Web development origins (e.g. the Vite dev server). Empty by default:
    # no CORS middleware is added unless explicitly configured, and a wildcard
    # origin is deliberately not expressible here.
    cors_origins: list[str] = Field(default_factory=list)
    # When True, the default mock executor loads the refinement fixtures — the
    # exact synthetic rows `scripts/seed_demo.py` registered — so a Web-guided
    # experiment reproduces the seeded demo numbers. Explicit opt-in only.
    demo_fixtures: bool = False

    @property
    def production_auth_configured(self) -> bool:
        """True only when the configured auth mode can actually authenticate."""
        return self.spark_production_auth_mode == "LDAP" and bool(
            self.spark_production_password.get_secret_value()
        )

    @property
    def effective_execution_mode(self) -> str:
        if "execution_mode" in self.model_fields_set:
            return self.execution_mode
        return self.query_executor

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
            if url.drivername not in {"mysql+pymysql", "sqlite+pysqlite"}:
                raise ValueError
            if not url.database:
                raise ValueError
        except Exception:
            raise ValueError("Expected mysql+pymysql URL or sqlite+pysqlite test URL") from None
        return value

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        if any(origin.strip() == "*" for origin in value):
            raise ValueError("Wildcard CORS origins are not allowed")
        return value
