"""Persist evidence-driven reflection metadata and reports."""

from alembic import op
import sqlalchemy as sa

revision = "0005_experiment_reflection"
down_revision = "0004_experiment_evaluation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reflection_runs",
        sa.Column("reflection_run_id", sa.String(36), primary_key=True),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reflection_policy_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(256), nullable=False),
        sa.Column("input_evidence_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "reflection_reports",
        sa.Column(
            "reflection_run_id",
            sa.String(36),
            sa.ForeignKey("reflection_runs.reflection_run_id"),
            primary_key=True,
        ),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("proposal_count", sa.Integer(), nullable=False),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("reflection_reports")
    op.drop_table("reflection_runs")
