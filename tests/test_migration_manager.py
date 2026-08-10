from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import subprocess
import sys

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from backend.database.base import Base
from backend.database.migration_manager import (
    ALEMBIC_INI,
    BASELINE_TABLE_NAMES,
    MigrationBootstrapError,
    alembic_config,
    bootstrap_or_upgrade,
    current_revision,
    head_revision,
    schema_diff,
)


def test_alembic_configuration_exists() -> None:
    assert Path(ALEMBIC_INI).is_file()


def test_alembic_has_exactly_one_head() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    heads = script.get_heads()

    assert len(heads) == 1
    assert head_revision() == heads[0]


def test_agent_policy_profiles_are_current_head() -> None:
    assert head_revision() == "20260807_0058"




def test_alembic_cli_respects_ai_studio_database_path(tmp_path: Path) -> None:
    database_path = tmp_path / "alembic-cli-override.db"
    env = os.environ.copy()
    env["AI_STUDIO_DATABASE_PATH"] = str(database_path)
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert revision == ("20260807_0058",)
    assert "code_sandbox_sessions" in tables
    assert "code_sandbox_approvals" in tables
    assert "code_sandbox_agent_runs" in tables
    assert "code_sandbox_runtime_runs" in tables
    assert "policy_approvals" in tables
    assert "policy_approval_evidence" in tables
    assert "documents" in tables
    assert "document_events" in tables
    assert "document_extraction_runs" in tables
    assert "document_extraction_units" in tables
    assert "document_extraction_chunks" in tables
    assert "document_ocr_runs" in tables
    assert "document_ocr_pages" in tables
    assert "document_ai_analysis_runs" in tables
    assert "document_ai_analysis_citations" in tables
    assert "agent_policy_profile_versions" in tables
    assert "agent_policy_profile_selections" in tables


def _sqlite_engine(tmp_path: Path, name: str) -> Engine:
    database_path = tmp_path / name
    return create_engine(f"sqlite:///{database_path.as_posix()}", future=True)


def test_fresh_database_round_trip_preserves_complete_schema(
    tmp_path: Path,
) -> None:
    db_engine = _sqlite_engine(tmp_path, "fresh-round-trip.db")

    result = bootstrap_or_upgrade(db_engine)

    assert result["action"] == "upgraded"
    assert result["before"] is None
    assert result["after"] == head_revision(db_engine)
    assert result["schema_diff"]["missing_tables"] == []
    assert result["schema_diff"]["missing_columns"] == []

    config = alembic_config(db_engine)
    command.downgrade(config, "base")

    assert current_revision(db_engine) is None
    baseline_tables = set(inspect(db_engine).get_table_names()) - {
        "alembic_version"
    }
    assert baseline_tables == BASELINE_TABLE_NAMES

    command.upgrade(config, "head")

    assert current_revision(db_engine) == head_revision(db_engine)
    assert schema_diff(db_engine)["missing_tables"] == []
    assert schema_diff(db_engine)["missing_columns"] == []
    db_engine.dispose()


def test_council_live_downgrade_preserves_cancelled_run_as_failed(
    tmp_path: Path,
) -> None:
    db_engine = _sqlite_engine(tmp_path, "cancelled-downgrade.db")
    bootstrap_or_upgrade(db_engine)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO council_runs (
                    id, status, question, mode,
                    synthesis_fallback_used, final_answer,
                    consensus_json, disagreements_json,
                    recommendations_json, member_count,
                    successful_member_count, duration_ms,
                    error_message, metadata_json,
                    started_at, finished_at, created_at
                ) VALUES (
                    'council_cancelled', 'cancelled', 'question', 'universal',
                    0, '', '[]', '[]', '[]', 2, 0, 10,
                    'cancelled', '{}',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )

    config = alembic_config(db_engine)
    command.downgrade(config, "20260716_0044")
    with db_engine.connect() as connection:
        status = connection.execute(
            text(
                "SELECT status FROM council_runs "
                "WHERE id = 'council_cancelled'"
            )
        ).scalar_one()

    assert status == "failed"
    command.upgrade(config, "head")
    db_engine.dispose()


def test_unversioned_baseline_is_stamped_then_upgraded(
    tmp_path: Path,
) -> None:
    db_engine = _sqlite_engine(tmp_path, "baseline-bootstrap.db")
    Base.metadata.create_all(
        bind=db_engine,
        tables=[
            Base.metadata.tables[table_name]
            for table_name in BASELINE_TABLE_NAMES
        ],
    )

    result = bootstrap_or_upgrade(db_engine)

    assert result["action"] == "stamped_baseline"
    assert result["after"] == head_revision(db_engine)
    assert result["schema_diff"]["missing_tables"] == []
    assert result["schema_diff"]["missing_columns"] == []
    db_engine.dispose()


def test_unversioned_current_schema_is_stamped_without_recreation(
    tmp_path: Path,
) -> None:
    db_engine = _sqlite_engine(tmp_path, "current-bootstrap.db")
    Base.metadata.create_all(bind=db_engine)

    result = bootstrap_or_upgrade(db_engine)

    assert result["action"] == "stamped_current"
    assert result["after"] == head_revision(db_engine)
    assert result["schema_diff"]["missing_tables"] == []
    assert result["schema_diff"]["missing_columns"] == []
    db_engine.dispose()


def test_partial_unversioned_schema_is_rejected_before_stamp(
    tmp_path: Path,
) -> None:
    db_engine = _sqlite_engine(tmp_path, "partial-bootstrap.db")
    Base.metadata.tables["workspaces"].create(bind=db_engine)

    with pytest.raises(
        MigrationBootstrapError,
        match="neither a complete current schema nor the P1-013 baseline",
    ):
        bootstrap_or_upgrade(db_engine)

    assert current_revision(db_engine) is None
    db_engine.dispose()
