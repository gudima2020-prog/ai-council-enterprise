"""P1-019.10 Human Control backup and restore.
Revision ID: 20260716_0040
Revises: 20260716_0039
"""
from alembic import op
import sqlalchemy as sa
revision="20260716_0040"
down_revision="20260716_0039"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("human_control_backups",sa.Column("id",sa.String(64),primary_key=True),sa.Column("backup_key",sa.String(160),nullable=False),sa.Column("workspace_id",sa.String(64),nullable=True),sa.Column("status",sa.String(16),nullable=False),sa.Column("backup_type",sa.String(32),nullable=False),sa.Column("storage_path",sa.Text(),nullable=False),sa.Column("manifest_json",sa.JSON(),nullable=False),sa.Column("manifest_hash",sa.String(64),nullable=False),sa.Column("file_hash",sa.String(64),nullable=False),sa.Column("size_bytes",sa.Integer(),nullable=False),sa.Column("created_by",sa.String(255),nullable=False),sa.Column("verified_at",sa.DateTime(timezone=True)),sa.Column("restored_at",sa.DateTime(timezone=True)),sa.Column("revoked_at",sa.DateTime(timezone=True)),sa.Column("error",sa.Text(),nullable=False),sa.Column("metadata_json",sa.JSON(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.ForeignKeyConstraint(["workspace_id"],["workspaces.id"],ondelete="SET NULL"),sa.UniqueConstraint("backup_key",name="uq_human_control_backups_key"),sa.CheckConstraint("status IN ('creating','ready','verified','failed','restored','revoked')",name="ck_human_control_backups_status"))
    op.create_index("ix_human_control_backups_history","human_control_backups",["workspace_id","status","created_at"])
    op.create_table("human_control_restore_runs",sa.Column("id",sa.String(64),primary_key=True),sa.Column("backup_id",sa.String(64),nullable=False),sa.Column("idempotency_key",sa.String(255),nullable=False),sa.Column("status",sa.String(16),nullable=False),sa.Column("dry_run",sa.Boolean(),nullable=False),sa.Column("requested_by",sa.String(255),nullable=False),sa.Column("reason",sa.Text(),nullable=False),sa.Column("validation_json",sa.JSON(),nullable=False),sa.Column("result_json",sa.JSON(),nullable=False),sa.Column("error",sa.Text(),nullable=False),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("completed_at",sa.DateTime(timezone=True)),sa.ForeignKeyConstraint(["backup_id"],["human_control_backups.id"],ondelete="CASCADE"),sa.UniqueConstraint("idempotency_key",name="uq_human_control_restore_runs_idempotency"),sa.CheckConstraint("status IN ('planned','validated','completed','failed','cancelled')",name="ck_human_control_restore_runs_status"))
def downgrade():
    op.drop_table("human_control_restore_runs")
    op.drop_index("ix_human_control_backups_history",table_name="human_control_backups")
    op.drop_table("human_control_backups")
