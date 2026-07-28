"""P1-019.5 browser sessions, CSRF and trusted clients.

Revision ID: 20260716_0035
Revises: 20260716_0034
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0035"
down_revision = "20260716_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_control_browser_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("csrf_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("require_trusted_client", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_missing_origin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cookie_secure", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cookie_samesite", sa.String(16), nullable=False, server_default="strict"),
        sa.Column("cookie_domain", sa.String(255), nullable=True),
        sa.Column("session_cookie_name", sa.String(128), nullable=False, server_default="hc_browser_session"),
        sa.Column("csrf_cookie_name", sa.String(128), nullable=False, server_default="hc_csrf"),
        sa.Column("csrf_header_name", sa.String(128), nullable=False, server_default="X-CSRF-Token"),
        sa.Column("session_ttl_seconds", sa.Integer(), nullable=False, server_default="28800"),
        sa.Column("session_idle_seconds", sa.Integer(), nullable=False, server_default="1800"),
        sa.Column("rotate_csrf_on_login", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bind_user_agent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bind_client_ip", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(255), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_browser_policies_scope"),
        sa.CheckConstraint("cookie_samesite IN ('strict', 'lax', 'none')", name="ck_human_control_browser_policies_samesite"),
    )
    op.create_index("ix_human_control_browser_policies_workspace_id", "human_control_browser_policies", ["workspace_id"], unique=False)
    op.create_index("ix_human_control_browser_policies_created_at", "human_control_browser_policies", ["created_at"], unique=False)
    op.create_index("ix_human_control_browser_policies_effective", "human_control_browser_policies", ["workspace_id", "enabled"], unique=False)

    op.create_table(
        "human_control_trusted_clients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("client_key", sa.String(160), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("allowed_origins_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "client_key", name="uq_human_control_trusted_clients_key"),
    )
    op.create_index("ix_human_control_trusted_clients_workspace_id", "human_control_trusted_clients", ["workspace_id"], unique=False)
    op.create_index("ix_human_control_trusted_clients_client_key", "human_control_trusted_clients", ["client_key"], unique=False)
    op.create_index("ix_human_control_trusted_clients_enabled", "human_control_trusted_clients", ["enabled"], unique=False)
    op.create_index("ix_human_control_trusted_clients_created_at", "human_control_trusted_clients", ["created_at"], unique=False)
    op.create_index("ix_human_control_trusted_clients_lookup", "human_control_trusted_clients", ["workspace_id", "enabled", "client_key"], unique=False)



def downgrade() -> None:
    op.drop_table("human_control_trusted_clients")
    op.drop_table("human_control_browser_policies")
