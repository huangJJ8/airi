"""real_production_verification

Phase 8 evidence layer. Everything here is additive: no Phase 7 column changes
meaning, and no metric definition, version, release, deployment or alert is
rewritten. Newly-required columns on historical tables carry a server default so
an already-populated deployment upgrades in place - the default names the honest
value ("unverified" telemetry, an unknown receipt time), never a claim.
"""

from alembic import op
import sqlalchemy as sa

# Alembic's default version_num is VARCHAR(32), including on MySQL.
revision = "0010_real_production_verification"
down_revision = "0009_production_monitoring_feedback"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------ environment fingerprint
    op.create_table(
        "production_environment_fingerprints",
        sa.Column("environment_fingerprint_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=True),
        sa.Column("cluster_identifier", sa.String(length=256), nullable=True),
        sa.Column("auth_mode", sa.String(length=16), nullable=False),
        sa.Column("runtime_user", sa.String(length=256), nullable=True),
        sa.Column("read_only_attested", sa.Boolean(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("fingerprint_hash", sa.String(length=64), nullable=False),
        sa.Column("fingerprint_json", sa.JSON(), nullable=False),
        sa.Column("observed_by", sa.String(length=128), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("environment_fingerprint_id"),
    )
    # ------------------------------------------- double-confirmed deployment
    op.create_table(
        "production_deployment_evidence",
        sa.Column("deployment_evidence_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("metric_version_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("deploy_status", sa.String(length=32), nullable=False),
        sa.Column("status_confirmed", sa.Boolean(), nullable=False),
        sa.Column("production_deployed", sa.Boolean(), nullable=False),
        sa.Column("provider_job_id", sa.String(length=128), nullable=True),
        sa.Column("provider_application_id", sa.String(length=128), nullable=True),
        sa.Column("environment_fingerprint_hash", sa.String(length=64), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("deployment_evidence_id"),
    )
    # ------------------------------------------------------ trusted telemetry
    op.create_table(
        "trusted_telemetry_sources",
        sa.Column("telemetry_source_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=2000), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("auth_identity", sa.String(length=256), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("synthetic", sa.Boolean(), nullable=False),
        sa.Column("registered_by", sa.String(length=128), nullable=False),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("telemetry_source_id"),
    )
    op.create_table(
        "monitoring_expectations",
        sa.Column("monitoring_expectation_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column("metric_version_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("telemetry_source_id", sa.String(length=64), nullable=True),
        sa.Column("expected_interval_hours", sa.Integer(), nullable=False),
        sa.Column("grace_hours", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("expectation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["production_deployments.deployment_id"]),
        sa.PrimaryKeyConstraint("monitoring_expectation_id"),
        sa.UniqueConstraint("deployment_id"),
    )
    # --------------------------------------------------------- reconciliation
    op.create_table(
        "reconciliation_plans",
        sa.Column("reconciliation_plan_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column("metric_version_id", sa.String(length=36), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("recommended_action", sa.String(length=32), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("reconciliation_plan_id"),
    )
    op.create_table(
        "reconciliation_reviews",
        sa.Column("reconciliation_review_id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_plan_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("reviewer", sa.String(length=2000), nullable=True),
        sa.Column("reviewer_actor_id", sa.String(length=128), nullable=True),
        sa.Column("reviewer_roles", sa.JSON(), nullable=True),
        sa.Column("reviewer_auth_source", sa.String(length=32), nullable=True),
        sa.Column("reviewer_issuer", sa.String(length=256), nullable=True),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column("review_json", sa.JSON(), nullable=False),
        sa.Column("review_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["reconciliation_plan_id"], ["reconciliation_plans.reconciliation_plan_id"]
        ),
        sa.PrimaryKeyConstraint("reconciliation_review_id"),
    )
    op.create_table(
        "reconciliation_results",
        sa.Column("reconciliation_result_id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_plan_id", sa.String(length=36), nullable=False),
        sa.Column("reconciliation_review_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("execution_status", sa.String(length=32), nullable=False),
        sa.Column("convergence_status", sa.String(length=32), nullable=False),
        sa.Column("final_status", sa.String(length=48), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("reconciliation_result_id"),
    )
    # -------------------------------------------------------- verification
    op.create_table(
        "production_verification_reports",
        sa.Column("production_verification_run_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column("metric_version_id", sa.String(length=36), nullable=False),
        sa.Column("overall", sa.String(length=24), nullable=False),
        sa.Column("production_runtime_verified", sa.Boolean(), nullable=False),
        sa.Column("production_deployed", sa.Boolean(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("report_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("production_verification_run_id"),
    )
    # ------------------------------------------- additions to existing tables
    #
    # A historical row predates telemetry attribution and the identity evidence
    # fields, so the default states the absence ("unverified", an unknown
    # receipt time) rather than inventing a verified value.
    op.add_column(
        "metric_alerts",
        sa.Column(
            "monitoring_policy_version",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'1.0.0'"),
        ),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column(
            "received_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("'1970-01-01 00:00:00'"),
        ),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column("telemetry_source_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column(
            "telemetry_trust",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'unverified'"),
        ),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column(
            "telemetry_freshness",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'late'"),
        ),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column("observation_lag_hours", sa.Float(), nullable=True),
    )
    op.add_column(
        "metric_monitoring_snapshots",
        sa.Column(
            "monitoring_policy_version",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'1.0.0'"),
        ),
    )
    op.add_column(
        "production_deployment_reviews",
        sa.Column("reviewer_roles", sa.JSON(), nullable=True),
    )
    op.add_column(
        "production_deployment_reviews",
        sa.Column("reviewer_auth_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "production_deployment_reviews",
        sa.Column("reviewer_issuer", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("runtime_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("provider_application_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("reviewer_roles", sa.JSON(), nullable=True),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("reviewer_auth_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("reviewer_issuer", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "production_rollback_reviews",
        sa.Column("preflight_json", sa.JSON(), nullable=True),
    )
    # §27 idempotency: one producer event may be ingested exactly once. SQLite
    # cannot ALTER TABLE ... ADD CONSTRAINT, so this goes through batch mode;
    # MySQL emits a plain ALTER.
    with op.batch_alter_table("metric_monitoring_snapshots") as batch:
        batch.create_unique_constraint(
            "uq_metric_monitoring_snapshots_source_event",
            ["telemetry_source_id", "source_event_id"],
        )


def downgrade():
    with op.batch_alter_table("metric_monitoring_snapshots") as batch:
        batch.drop_constraint("uq_metric_monitoring_snapshots_source_event", type_="unique")
    op.drop_column("production_rollback_reviews", "preflight_json")
    op.drop_column("production_rollback_reviews", "reviewer_issuer")
    op.drop_column("production_rollback_reviews", "reviewer_auth_source")
    op.drop_column("production_rollback_reviews", "reviewer_roles")
    op.drop_column("production_rollback_reviews", "provider_application_id")
    op.drop_column("production_rollback_reviews", "runtime_verified")
    op.drop_column("production_deployment_reviews", "reviewer_issuer")
    op.drop_column("production_deployment_reviews", "reviewer_auth_source")
    op.drop_column("production_deployment_reviews", "reviewer_roles")
    op.drop_column("metric_monitoring_snapshots", "monitoring_policy_version")
    op.drop_column("metric_monitoring_snapshots", "observation_lag_hours")
    op.drop_column("metric_monitoring_snapshots", "telemetry_freshness")
    op.drop_column("metric_monitoring_snapshots", "telemetry_trust")
    op.drop_column("metric_monitoring_snapshots", "telemetry_source_id")
    op.drop_column("metric_monitoring_snapshots", "received_at")
    op.drop_column("metric_alerts", "monitoring_policy_version")
    op.drop_table("production_verification_reports")
    op.drop_table("reconciliation_results")
    op.drop_table("reconciliation_reviews")
    op.drop_table("reconciliation_plans")
    op.drop_table("monitoring_expectations")
    op.drop_table("trusted_telemetry_sources")
    op.drop_table("production_deployment_evidence")
    op.drop_table("production_environment_fingerprints")
