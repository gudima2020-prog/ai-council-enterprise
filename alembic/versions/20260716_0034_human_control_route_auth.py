"""P1-019.4 mandatory route authentication and principal binding.

Revision ID: 20260716_0034
Revises: 20260716_0033
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260716_0034"
down_revision = "20260716_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("human_control_auth_policies", sa.Column("protected_path_prefixes_json", sa.JSON(), nullable=False, server_default='["/api/human-control"]'))
    op.add_column("human_control_auth_policies", sa.Column("public_paths_json", sa.JSON(), nullable=False, server_default='["/", "/docs*", "/redoc*", "/openapi.json", "/api/health*", "/api/human-control/auth/status", "/api/human-control/auth/login", "/api/human-control/auth/bootstrap-identity", "/api/human-control/auth/break-glass/activate", "/api/human-control/governance/bootstrap-owner"]'))
    op.add_column("human_control_auth_policies", sa.Column("require_actor_binding", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("human_control_auth_policies", sa.Column("require_workspace_binding", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("human_control_auth_policies", sa.Column("reject_unscoped_api_tokens", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("human_control_auth_policies", "reject_unscoped_api_tokens")
    op.drop_column("human_control_auth_policies", "require_workspace_binding")
    op.drop_column("human_control_auth_policies", "require_actor_binding")
    op.drop_column("human_control_auth_policies", "public_paths_json")
    op.drop_column("human_control_auth_policies", "protected_path_prefixes_json")
