"""P1-019.3 authenticated identities, sessions, API tokens and break-glass.

Revision ID: 20260716_0033
Revises: 20260716_0032
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "20260716_0033"
down_revision = "20260716_0032"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column], unique=False)


def upgrade() -> None:
    op.create_table(
        "human_control_auth_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(320), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enforcement_mode", sa.String(16), nullable=False),
        sa.Column("session_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("session_idle_seconds", sa.Integer(), nullable=False),
        sa.Column("max_failed_attempts", sa.Integer(), nullable=False),
        sa.Column("lockout_seconds", sa.Integer(), nullable=False),
        sa.Column("api_token_max_ttl_days", sa.Integer(), nullable=False),
        sa.Column("break_glass_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("break_glass_requires_approval", sa.Boolean(), nullable=False),
        sa.Column("break_glass_distinct_approver", sa.Boolean(), nullable=False),
        sa.Column("allowed_api_scopes_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_human_control_auth_policies_scope"),
        sa.CheckConstraint("enforcement_mode IN ('legacy', 'audit', 'enforce')", name="ck_human_control_auth_policies_mode"),
    )
    _indexes("human_control_auth_policies", ("workspace_id", "enforcement_mode", "created_at"))
    op.create_index("ix_human_control_auth_policies_effective", "human_control_auth_policies", ["workspace_id", "enabled"], unique=False)

    op.create_table(
        "human_control_identities",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("username_normalized", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("identity_type", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_salt", sa.String(255), nullable=True),
        sa.Column("password_iterations", sa.Integer(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_ip", sa.String(128), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor_id", name="uq_human_control_identities_actor"),
        sa.UniqueConstraint("username_normalized", name="uq_human_control_identities_username"),
        sa.CheckConstraint("identity_type IN ('human', 'service')", name="ck_human_control_identities_type"),
        sa.CheckConstraint("status IN ('active', 'disabled', 'locked')", name="ck_human_control_identities_status"),
    )
    _indexes("human_control_identities", ("actor_id", "username_normalized", "identity_type", "status", "locked_until", "created_at"))
    op.create_index("ix_human_control_identities_lookup", "human_control_identities", ["username_normalized", "status"], unique=False)

    op.create_table(
        "human_control_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("identity_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("auth_method", sa.String(16), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("client_ip", sa.String(128), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["identity_id"], ["human_control_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_human_control_sessions_token"),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_human_control_sessions_status"),
        sa.CheckConstraint("auth_method IN ('password', 'api_token', 'break_glass')", name="ck_human_control_sessions_method"),
    )
    _indexes("human_control_sessions", ("identity_id", "workspace_id", "token_hash", "status", "created_at", "expires_at", "idle_expires_at"))
    op.create_index("ix_human_control_sessions_active", "human_control_sessions", ["identity_id", "status", "expires_at"], unique=False)

    op.create_table(
        "human_control_api_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("identity_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_prefix", sa.String(32), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["identity_id"], ["human_control_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token_hash", name="uq_human_control_api_tokens_hash"),
        sa.CheckConstraint("status IN ('active', 'revoked', 'expired')", name="ck_human_control_api_tokens_status"),
    )
    _indexes("human_control_api_tokens", ("identity_id", "workspace_id", "token_hash", "token_prefix", "status", "expires_at", "created_at"))
    op.create_index("ix_human_control_api_tokens_active", "human_control_api_tokens", ["identity_id", "status", "expires_at"], unique=False)

    op.create_table(
        "human_control_break_glass",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("requested_by_identity_id", sa.String(64), nullable=False),
        sa.Column("approved_by_identity_id", sa.String(64), nullable=True),
        sa.Column("request_idempotency_key", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("activation_token_hash", sa.String(64), nullable=True),
        sa.Column("activation_token_prefix", sa.String(32), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_identity_id"], ["human_control_identities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by_identity_id"], ["human_control_identities.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("request_idempotency_key", name="uq_human_control_break_glass_idempotency"),
        sa.UniqueConstraint("activation_token_hash", name="uq_human_control_break_glass_token"),
        sa.CheckConstraint("status IN ('requested', 'approved', 'active', 'used', 'rejected', 'revoked', 'expired')", name="ck_human_control_break_glass_status"),
    )
    _indexes("human_control_break_glass", ("workspace_id", "requested_by_identity_id", "approved_by_identity_id", "request_idempotency_key", "status", "activation_token_hash", "requested_at", "expires_at"))
    op.create_index("ix_human_control_break_glass_queue", "human_control_break_glass", ["workspace_id", "status", "expires_at"], unique=False)

    op.create_table(
        "human_control_security_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("identity_id", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("client_ip", sa.String(128), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["identity_id"], ["human_control_identities.id"], ondelete="SET NULL"),
    )
    _indexes("human_control_security_events", ("workspace_id", "identity_id", "actor_id", "event_type", "created_at"))
    op.create_index("ix_human_control_security_events_history", "human_control_security_events", ["workspace_id", "actor_id", "event_type", "created_at"], unique=False)


def downgrade() -> None:
    for table in (
        "human_control_security_events",
        "human_control_break_glass",
        "human_control_api_tokens",
        "human_control_sessions",
        "human_control_identities",
        "human_control_auth_policies",
    ):
        op.drop_table(table)
