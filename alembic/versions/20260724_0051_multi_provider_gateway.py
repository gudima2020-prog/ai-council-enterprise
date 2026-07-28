"""P2-009 Multi-Provider Model Gateway runtime health.

Revision ID: 20260724_0051
Revises: 20260724_0050
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260724_0051"
down_revision = "20260724_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_provider_stats",
        sa.Column("provider", sa.String(length=64), primary_key=True),
        sa.Column("total_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ema_latency_ms", sa.Float(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("gateway_provider_stats")
