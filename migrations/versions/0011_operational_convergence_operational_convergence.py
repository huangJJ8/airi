"""operational_convergence

Phase 9 operational layer. Additive: four new governance tables plus one for the
break-glass capability, and a single nullability change on `reconciliation_results`
so a converged plan can be recorded without inventing a review or an action.

Nothing from 0001-0010 is rewritten by this revision.
"""

from alembic import op
import sqlalchemy as sa

# Alembic's default version_num is VARCHAR(32), including on MySQL.
revision = "0011_operational_convergence"
down_revision = "0010_real_production_verification"
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------- desired state
    op.create_table(
        "desired_runtime_states",
        sa.Column("desired_state_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("desired_metric_version_id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_action_id", sa.String(length=64), nullable=False),
        sa.Column("source_deployment_id", sa.String(length=36), nullable=True),
        sa.Column("established_by", sa.String(length=128), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("established_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("desired_state_id"),
    )
    op.create_index(
        "ix_desired_runtime_states_scope",
        "desired_runtime_states",
        ["metric_definition_id", "environment_id", "established_at"],
    )

    # -------------------------------------------------- expectation runs
    op.create_table(
        "expectation_evaluation_runs",
        sa.Column("evaluation_run_id", sa.String(length=36), nullable=False),
        sa.Column("evaluated_by", sa.String(length=128), nullable=False),
        sa.Column("expectation_count", sa.Integer(), nullable=False),
        sa.Column("overdue_count", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=16), nullable=False),
        sa.Column("run_json", sa.JSON(), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("evaluation_run_id"),
    )

    # ------------------------------------------------- notification delivery
    op.create_table(
        "notification_deliveries",
        sa.Column("notification_delivery_id", sa.String(length=36), nullable=False),
        sa.Column("alert_id", sa.String(length=36), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=True),
        sa.Column("sink", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider_message_id", sa.String(length=256), nullable=True),
        sa.Column("failure_category", sa.String(length=64), nullable=True),
        sa.Column("delivery_json", sa.JSON(), nullable=False),
        sa.Column("delivery_hash", sa.String(length=64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("notification_delivery_id"),
    )
    op.create_index(
        "ix_notification_deliveries_alert",
        "notification_deliveries",
        ["alert_id", "sink"],
    )

    # ------------------------------------------------------- gate decisions
    op.create_table(
        "verification_gate_decisions",
        sa.Column("gate_decision_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("override_id", sa.String(length=36), nullable=True),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("gate_decision_id"),
    )

    # ----------------------------------------------------- emergency override
    op.create_table(
        "emergency_overrides",
        sa.Column("emergency_override_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("deployment_id", sa.String(length=36), nullable=True),
        sa.Column("environment_id", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("override_json", sa.JSON(), nullable=False),
        sa.Column("override_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("emergency_override_id"),
    )

    # `batch_alter_table` so the same revision works on SQLite, which has no
    # ALTER COLUMN of its own. A converged plan has no review and no action, and
    # the honest encoding of that is NULL rather than a sentinel string.
    with op.batch_alter_table("reconciliation_results") as batch:
        batch.alter_column("reconciliation_review_id", existing_type=sa.String(36), nullable=True)
        batch.alter_column("action", existing_type=sa.String(40), nullable=True)


def downgrade():
    with op.batch_alter_table("reconciliation_results") as batch:
        batch.alter_column("reconciliation_review_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("action", existing_type=sa.String(40), nullable=False)
    op.drop_table("emergency_overrides")
    op.drop_table("verification_gate_decisions")
    op.drop_index("ix_notification_deliveries_alert", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_table("expectation_evaluation_runs")
    op.drop_index("ix_desired_runtime_states_scope", table_name="desired_runtime_states")
    op.drop_table("desired_runtime_states")
