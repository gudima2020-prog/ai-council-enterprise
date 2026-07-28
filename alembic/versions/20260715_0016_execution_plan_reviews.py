"""Execution Plan Critic reviews

Revision ID: 20260715_0016
Revises: 20260715_0015
Create Date: 2026-07-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260715_0016"
down_revision: Union[str, Sequence[str], None] = "20260715_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "execution_plan_reviews",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("plan_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=True),
        sa.Column("reviewer_ref", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("review_round", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("dimension_scores_json", sa.JSON(), nullable=False),
        sa.Column("issues_json", sa.JSON(), nullable=False),
        sa.Column("suggested_fixes_json", sa.JSON(), nullable=False),
        sa.Column("applied_fixes_json", sa.JSON(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_execution_plan_reviews_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('pass', 'revise', 'reject')",
            name="ck_execution_plan_reviews_decision",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["execution_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_plan_reviews_plan_id",
        "execution_plan_reviews",
        ["plan_id"],
    )
    op.create_index(
        "ix_execution_plan_reviews_workspace_id",
        "execution_plan_reviews",
        ["workspace_id"],
    )
    op.create_index(
        "ix_execution_plan_reviews_reviewer_ref",
        "execution_plan_reviews",
        ["reviewer_ref"],
    )
    op.create_index(
        "ix_execution_plan_reviews_status",
        "execution_plan_reviews",
        ["status"],
    )
    op.create_index(
        "ix_execution_plan_reviews_decision",
        "execution_plan_reviews",
        ["decision"],
    )
    op.create_index(
        "ix_execution_plan_reviews_created_at",
        "execution_plan_reviews",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_plan_reviews_created_at",
        table_name="execution_plan_reviews",
    )
    op.drop_index(
        "ix_execution_plan_reviews_decision",
        table_name="execution_plan_reviews",
    )
    op.drop_index(
        "ix_execution_plan_reviews_status",
        table_name="execution_plan_reviews",
    )
    op.drop_index(
        "ix_execution_plan_reviews_reviewer_ref",
        table_name="execution_plan_reviews",
    )
    op.drop_index(
        "ix_execution_plan_reviews_workspace_id",
        table_name="execution_plan_reviews",
    )
    op.drop_index(
        "ix_execution_plan_reviews_plan_id",
        table_name="execution_plan_reviews",
    )
    op.drop_table("execution_plan_reviews")
