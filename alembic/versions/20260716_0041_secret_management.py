"""P1-020.1 Secret Management foundation.

Revision ID: 20260716_0041
Revises: 20260716_0040
"""
from alembic import op
import sqlalchemy as sa

revision = "20260716_0041"
down_revision = "20260716_0040"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "secret_providers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider_key", sa.String(120), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("adapter_key", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("read_only", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("health_status", sa.String(16), nullable=False),
        sa.Column("health_message", sa.Text(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("provider_key", name="uq_secret_providers_key"),
        sa.CheckConstraint(
            "provider_type IN ('env','windows_dpapi','external')",
            name="ck_secret_providers_type",
        ),
        sa.CheckConstraint(
            "health_status IN ('unknown','healthy','unavailable','error')",
            name="ck_secret_providers_health",
        ),
    )
    op.create_index(
        "ix_secret_providers_effective",
        "secret_providers",
        ["workspace_id", "enabled", "provider_type"],
    )

    op.create_table(
        "secret_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("secret_key", sa.String(240), nullable=False),
        sa.Column("locator_key", sa.String(512), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("material_hash", sa.String(64), nullable=False),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["secret_providers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("locator_key", name="uq_secret_records_locator"),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_secret_records_status"),
        sa.CheckConstraint("current_version >= 0", name="ck_secret_records_version"),
    )
    op.create_index(
        "ix_secret_records_lookup",
        "secret_records",
        ["workspace_id", "provider_id", "status", "secret_key"],
    )

    op.create_table(
        "secret_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_version", sa.String(255), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
        sa.Column("material_hash", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["secret_id"], ["secret_records.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "secret_id", "version", name="uq_secret_versions_secret_version"
        ),
        sa.CheckConstraint(
            "status IN ('current','retired')", name="ck_secret_versions_status"
        ),
    )
    op.create_index(
        "ix_secret_versions_history",
        "secret_versions",
        ["secret_id", "status", "version"],
    )

    op.create_table(
        "secret_access_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("secret_id", sa.String(64), nullable=True),
        sa.Column("provider_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("auth_method", sa.String(32), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("material_hash", sa.String(64), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('allowed','denied','error')",
            name="ck_secret_access_events_outcome",
        ),
    )
    op.create_index(
        "ix_secret_access_events_history",
        "secret_access_events",
        ["workspace_id", "secret_id", "actor_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_secret_access_events_history", table_name="secret_access_events")
    op.drop_table("secret_access_events")
    op.drop_index("ix_secret_versions_history", table_name="secret_versions")
    op.drop_table("secret_versions")
    op.drop_index("ix_secret_records_lookup", table_name="secret_records")
    op.drop_table("secret_records")
    op.drop_index("ix_secret_providers_effective", table_name="secret_providers")
    op.drop_table("secret_providers")
