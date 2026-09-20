"""Persist immutable development snapshots and single-cycle human reviews."""

import sqlalchemy as sa
from alembic import op

revision = "0001_reviews"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_artifacts",
        sa.Column("artifact_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False, unique=True),
        sa.Column("artifact_version", sa.String(32), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_ir_hash", sa.String(64), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_development_artifacts_metric_name", "development_artifacts", ["metric_name"]
    )
    op.create_table(
        "approval_records",
        sa.Column("approval_id", sa.String(36), primary_key=True),
        sa.Column("workflow_run_id", sa.String(36), nullable=False),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("development_artifacts.artifact_id"),
            nullable=False,
        ),
        sa.Column("artifact_version", sa.String(32), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_ir_hash", sa.String(64), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(128)),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.String(2000)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime()),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("artifact_id", name="uq_approval_artifact"),
    )
    op.create_index("ix_approval_records_workflow_run_id", "approval_records", ["workflow_run_id"])
    op.create_index("ix_approval_decision_created", "approval_records", ["decision", "created_at"])


def downgrade():
    op.drop_table("approval_records")
    op.drop_table("development_artifacts")
