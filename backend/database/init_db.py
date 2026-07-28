from backend.core.logging import LoggerManager
from backend.database.migration_manager import bootstrap_or_upgrade


def initialize_database() -> None:
    logger = LoggerManager.get_logger("database")
    result = bootstrap_or_upgrade()

    logger.info(
        "Database migrations completed action=%s before=%s after=%s head=%s",
        result.get("action"),
        result.get("before"),
        result.get("after"),
        result.get("head"),
    )
