from backend.core.logging import LoggerManager
from backend.database.base import Base
from backend.database.session import engine
from backend.database import models  # noqa: F401
from backend.database.migrations import apply_workspace_link_migration


def initialize_database() -> None:
    logger = LoggerManager.get_logger("database")

    Base.metadata.create_all(bind=engine)
    apply_workspace_link_migration()

    logger.info("Database initialized")
