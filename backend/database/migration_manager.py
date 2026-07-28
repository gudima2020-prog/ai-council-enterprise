from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect

from backend.database.base import Base
from backend.database.session import engine
from backend.database import models as database_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.autonomy import models as autonomy_models  # noqa: F401
from backend.control_center import models as control_center_models  # noqa: F401
from backend.secrets import models as secret_models  # noqa: F401
from backend.council import models as council_models  # noqa: F401
from backend.code_sandbox import models as code_sandbox_models  # noqa: F401


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
BASELINE_REVISION = "20260714_0001"
BASELINE_TABLE_NAMES = frozenset(
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


class MigrationBootstrapError(RuntimeError):
    """Raised when an unversioned database cannot be classified safely."""


def _target_engine(db_engine: Engine | None) -> Engine:
    return db_engine or engine


def alembic_config(db_engine: Engine | None = None) -> Config:
    target = _target_engine(db_engine)
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        target.url.render_as_string(hide_password=False),
    )
    return config


def current_revision(db_engine: Engine | None = None) -> str | None:
    target = _target_engine(db_engine)
    with target.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(db_engine: Engine | None = None) -> str:
    return ScriptDirectory.from_config(alembic_config(db_engine)).get_current_head()


def _actual_table_names(db_engine: Engine | None = None) -> set[str]:
    target = _target_engine(db_engine)
    return set(inspect(target).get_table_names()) - {"alembic_version"}


def database_has_application_tables(db_engine: Engine | None = None) -> bool:
    return bool(set(Base.metadata.tables) & _actual_table_names(db_engine))


def _schema_diff_for(
    expected_table_names: Iterable[str],
    db_engine: Engine | None = None,
) -> dict[str, list[str]]:
    target = _target_engine(db_engine)
    inspector = inspect(target)
    expected_tables = set(expected_table_names)
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    missing_columns: list[str] = []

    for table_name in expected_tables & actual_tables:
        expected_columns = set(Base.metadata.tables[table_name].columns.keys())
        actual_columns = {
            str(column["name"])
            for column in inspector.get_columns(table_name)
        }
        missing_columns.extend(
            f"{table_name}.{column_name}"
            for column_name in sorted(expected_columns - actual_columns)
        )

    return {
        "missing_tables": sorted(expected_tables - actual_tables),
        "missing_columns": sorted(missing_columns),
        "unexpected_tables": sorted(actual_tables - expected_tables),
    }


def schema_diff(db_engine: Engine | None = None) -> dict[str, list[str]]:
    return _schema_diff_for(Base.metadata.tables, db_engine)


def baseline_schema_diff(
    db_engine: Engine | None = None,
) -> dict[str, list[str]]:
    return _schema_diff_for(BASELINE_TABLE_NAMES, db_engine)


def _has_missing_schema(diff: dict[str, list[str]]) -> bool:
    return bool(diff["missing_tables"] or diff["missing_columns"])


def _bootstrap_error(
    *,
    current_diff: dict[str, list[str]],
    baseline_diff: dict[str, list[str]],
) -> MigrationBootstrapError:
    details = {
        "current_missing_tables": current_diff["missing_tables"],
        "current_missing_columns": current_diff["missing_columns"],
        "baseline_missing_tables": baseline_diff["missing_tables"],
        "baseline_missing_columns": baseline_diff["missing_columns"],
    }
    return MigrationBootstrapError(
        "Existing unversioned database is neither a complete current schema "
        "nor the P1-013 baseline. Migration was stopped before stamping. "
        f"Details: {json.dumps(details, ensure_ascii=False, sort_keys=True)}"
    )


def bootstrap_or_upgrade(
    db_engine: Engine | None = None,
) -> dict[str, Any]:
    """
    Safely classify an unversioned database, then upgrade it to Alembic head.

    Supported unversioned states:

    * no application tables: build the schema from the full migration chain;
    * complete P1-013 baseline: stamp the baseline and apply later revisions;
    * complete current schema: stamp the current head without recreating data.

    A partial or ambiguous known schema is rejected before Alembic stamping.
    Unknown plugin-owned tables do not block a compatible application schema.
    """
    target = _target_engine(db_engine)
    config = alembic_config(target)
    before = current_revision(target)
    head = head_revision(target)
    action = "upgraded"

    if before is None and database_has_application_tables(target):
        actual_tables = _actual_table_names(target)
        known_tables = actual_tables & set(Base.metadata.tables)
        current_diff = schema_diff(target)
        baseline_diff = baseline_schema_diff(target)

        if not _has_missing_schema(current_diff):
            command.stamp(config, head)
            action = "stamped_current"
        elif (
            known_tables <= BASELINE_TABLE_NAMES
            and not _has_missing_schema(baseline_diff)
        ):
            command.stamp(config, BASELINE_REVISION)
            action = "stamped_baseline"
        else:
            raise _bootstrap_error(
                current_diff=current_diff,
                baseline_diff=baseline_diff,
            )

    command.upgrade(config, "head")
    after = current_revision(target)
    final_diff = schema_diff(target)
    if _has_missing_schema(final_diff):
        raise MigrationBootstrapError(
            "Database reached an Alembic revision but its schema is "
            "incomplete: "
            f"{json.dumps(final_diff, ensure_ascii=False, sort_keys=True)}"
        )

    return {
        "action": action,
        "before": before,
        "after": after,
        "head": head,
        "schema_diff": final_diff,
    }


def downgrade_one(db_engine: Engine | None = None) -> dict[str, Any]:
    target = _target_engine(db_engine)
    config = alembic_config(target)
    before = current_revision(target)
    command.downgrade(config, "-1")
    return {
        "action": "downgraded",
        "before": before,
        "after": current_revision(target),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI Studio Enterprise database migration manager."
    )
    parser.add_argument(
        "action",
        choices=("upgrade", "current"),
        nargs="?",
        default="upgrade",
    )
    args = parser.parse_args(argv)

    if args.action == "current":
        print(current_revision() or "<base>")
        return 0

    result = bootstrap_or_upgrade()
    print(
        "DATABASE_MIGRATION "
        f"action={result['action']} "
        f"before={result['before']} "
        f"after={result['after']} "
        f"head={result['head']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
