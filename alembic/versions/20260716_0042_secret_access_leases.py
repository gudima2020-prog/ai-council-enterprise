"""P1-020.2 secret access policies and ephemeral leases.

Revision ID: 20260716_0042
Revises: 20260716_0041
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0042"
down_revision = "20260716_0041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "secret_access_settings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("enforcement_mode", sa.String(16), nullable=False),
        sa.Column("default_effect", sa.String(16), nullable=False),
        sa.Column("default_lease_seconds", sa.Integer(), nullable=False),
        sa.Column("max_lease_seconds", sa.Integer(), nullable=False),
        sa.Column("max_lease_uses", sa.Integer(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("updated_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("scope_key", name="uq_secret_access_settings_scope"),
        sa.CheckConstraint(
            "enforcement_mode IN ('legacy','audit','enforce')",
            name="ck_secret_access_settings_mode",
        ),
        sa.CheckConstraint(
            "default_effect IN ('allow','deny')",
            name="ck_secret_access_settings_default_effect",
        ),
        sa.CheckConstraint(
            "default_lease_seconds >= 1",
            name="ck_secret_access_settings_default_ttl",
        ),
        sa.CheckConstraint(
            "max_lease_seconds >= 1",
            name="ck_secret_access_settings_max_ttl",
        ),
        sa.CheckConstraint(
            "max_lease_uses >= 1",
            name="ck_secret_access_settings_max_uses",
        ),
    )
    op.create_index(
        "ix_secret_access_settings_workspace_id",
        "secret_access_settings",
        ["workspace_id"],
    )

    op.create_table(
        "secret_access_policies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("policy_key", sa.String(160), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("effect", sa.String(16), nullable=False),
        sa.Column("actions_json", sa.JSON(), nullable=False),
        sa.Column("secret_patterns_json", sa.JSON(), nullable=False),
        sa.Column("consumer_types_json", sa.JSON(), nullable=False),
        sa.Column("consumer_keys_json", sa.JSON(), nullable=False),
        sa.Column("actor_patterns_json", sa.JSON(), nullable=False),
        sa.Column("source_patterns_json", sa.JSON(), nullable=False),
        sa.Column("purpose_patterns_json", sa.JSON(), nullable=False),
        sa.Column("max_lease_seconds", sa.Integer(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("require_workspace_match", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("policy_key", name="uq_secret_access_policies_key"),
        sa.CheckConstraint(
            "effect IN ('allow','deny')",
            name="ck_secret_access_policies_effect",
        ),
    )
    op.create_index(
        "ix_secret_access_policies_effective",
        "secret_access_policies",
        ["workspace_id", "enabled", "priority"],
    )

    op.create_table(
        "secret_leases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("consumer_type", sa.String(64), nullable=False),
        sa.Column("consumer_key", sa.String(255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("token_hash", name="uq_secret_leases_token_hash"),
        sa.CheckConstraint(
            "status IN ('active','consumed','revoked','expired')",
            name="ck_secret_leases_status",
        ),
        sa.CheckConstraint("max_uses >= 1", name="ck_secret_leases_max_uses"),
        sa.CheckConstraint("use_count >= 0", name="ck_secret_leases_use_count"),
    )
    op.create_index(
        "ix_secret_leases_active",
        "secret_leases",
        ["workspace_id", "secret_id", "status", "expires_at"],
    )
    op.create_index("ix_secret_leases_provider_id", "secret_leases", ["provider_id"])
    op.create_index("ix_secret_leases_actor_id", "secret_leases", ["actor_id"])
    op.create_index("ix_secret_leases_consumer_type", "secret_leases", ["consumer_type"])
    op.create_index("ix_secret_leases_consumer_key", "secret_leases", ["consumer_key"])
    op.create_index("ix_secret_leases_status", "secret_leases", ["status"])
    op.create_index("ix_secret_leases_issued_at", "secret_leases", ["issued_at"])


def downgrade():
    op.drop_index("ix_secret_leases_issued_at", table_name="secret_leases")
    op.drop_index("ix_secret_leases_status", table_name="secret_leases")
    op.drop_index("ix_secret_leases_consumer_key", table_name="secret_leases")
    op.drop_index("ix_secret_leases_consumer_type", table_name="secret_leases")
    op.drop_index("ix_secret_leases_actor_id", table_name="secret_leases")
    op.drop_index("ix_secret_leases_provider_id", table_name="secret_leases")
    op.drop_index("ix_secret_leases_active", table_name="secret_leases")
    op.drop_table("secret_leases")

    op.drop_index("ix_secret_access_policies_effective", table_name="secret_access_policies")
    op.drop_table("secret_access_policies")

    op.drop_index("ix_secret_access_settings_workspace_id", table_name="secret_access_settings")
    op.drop_table("secret_access_settings")
