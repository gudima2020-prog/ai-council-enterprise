"""AI Studio Enterprise baseline schema

Revision ID: 20260714_0001
Revises:
Create Date: 2026-07-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BASELINE_TABLES = frozenset(
    {
        "workspaces",
        "projects",
        "chats",
        "chat_messages",
        "settings",
        "model_configs",
        "memory_items",
    }
)


def upgrade() -> None:
    """
    Create the immutable P1-013 baseline schema.

    The table definitions live in this revision instead of being generated
    from current ORM metadata. Later model imports therefore cannot make a
    fresh installation skip or pre-create tables owned by newer revisions.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names()) - {"alembic_version"}

    if existing_tables:
        missing_tables = BASELINE_TABLES - existing_tables
        if missing_tables:
            names = ", ".join(sorted(missing_tables))
            raise RuntimeError(
                "Existing unversioned database does not match the P1-013 "
                f"baseline; missing tables: {names}. Use the safe bootstrap "
                "command instead of applying Alembic directly."
            )
        return

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("workspace_type", sa.String(32), nullable=False),
        sa.Column("icon", sa.String(64), nullable=True),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_workspaces_name"),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_projects_workspace_id",
        "projects",
        ["workspace_id"],
    )

    op.create_table(
        "chats",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_chats_workspace_id", "chats", ["workspace_id"])
    op.create_index("ix_chats_project_id", "chats", ["project_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["chats.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_chat_messages_chat_id",
        "chat_messages",
        ["chat_id"],
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "scope",
            "project_id",
            "workspace_id",
            "key",
            name="uq_settings_scope_target_key",
        ),
    )
    op.create_index(
        "ix_settings_project_id",
        "settings",
        ["project_id"],
    )
    op.create_index(
        "ix_settings_workspace_id",
        "settings",
        ["workspace_id"],
    )

    op.create_table(
        "model_configs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=False),
        sa.Column("supports_vision", sa.Boolean(), nullable=False),
        sa.Column("supports_json", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_model_configs_slug"),
    )

    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("workspace_id", sa.String(64), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_memory_items_project_id",
        "memory_items",
        ["project_id"],
    )
    op.create_index(
        "ix_memory_items_workspace_id",
        "memory_items",
        ["workspace_id"],
    )


def downgrade() -> None:
    """
    Baseline downgrade intentionally keeps application tables.

    Dropping all production data automatically is unsafe. Destructive teardown
    must be an explicit administrative operation, not an Alembic downgrade.
    """
    pass
