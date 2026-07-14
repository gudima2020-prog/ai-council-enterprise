from __future__ import annotations

from sqlalchemy import inspect, text

from backend.core.logging import LoggerManager
from backend.database.session import engine


logger = LoggerManager.get_logger("database")


def _column_names(table_name: str) -> set[str]:
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def apply_workspace_link_migration() -> None:
    """
    Lightweight SQLite migration for the current development stage.

    Adds workspace_id columns to existing tables when they are absent.
    Alembic will replace this helper in a later phase.
    """
    migrations: list[tuple[str, str]] = [
        ("projects", "ALTER TABLE projects ADD COLUMN workspace_id VARCHAR(64)"),
        ("chats", "ALTER TABLE chats ADD COLUMN workspace_id VARCHAR(64)"),
        ("memory_items", "ALTER TABLE memory_items ADD COLUMN workspace_id VARCHAR(64)"),
    ]

    with engine.begin() as connection:
        for table_name, statement in migrations:
            if "workspace_id" in _column_names(table_name):
                continue

            connection.execute(text(statement))
            logger.info(
                "Database migration applied table=%s column=workspace_id",
                table_name,
            )
