from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.model_manager import ModelManager
from backend.services.workspace_context import WorkspaceContextResolver
from backend.services.workspace_state import ACTIVE_WORKSPACE_KEY


def make_settings() -> AppSettings:
    return AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )


def test_header_workspace_overrides_active_workspace() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        settings = make_settings()
        event_bus = EventBus()

        ModelManager(
            ModelRepository(session),
            event_bus,
            settings,
        ).seed_defaults()

        active_workspace = WorkspaceRepository(session).create(
            name="Active",
            status="active",
        )
        header_workspace = WorkspaceRepository(session).create(
            name="Header",
            status="active",
        )

        SettingsRepository(session).upsert(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
            value=active_workspace.id,
        )
        session.commit()

        resolved = WorkspaceContextResolver(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            model_repository=ModelRepository(session),
            event_bus=event_bus,
            app_settings=settings,
        ).resolve(
            requested_workspace_id=header_workspace.id,
        )

        assert resolved["workspace_id"] == header_workspace.id
        assert resolved["source"] == "request_header"


def test_falls_back_to_active_workspace() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        settings = make_settings()
        event_bus = EventBus()

        ModelManager(
            ModelRepository(session),
            event_bus,
            settings,
        ).seed_defaults()

        workspace = WorkspaceRepository(session).create(
            name="Crypto AI",
            status="active",
        )

        SettingsRepository(session).upsert(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
            value=workspace.id,
        )
        session.commit()

        resolved = WorkspaceContextResolver(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            model_repository=ModelRepository(session),
            event_bus=event_bus,
            app_settings=settings,
        ).resolve(
            requested_workspace_id=None,
        )

        assert resolved["workspace_id"] == workspace.id
        assert resolved["source"] == "active_workspace"
        assert resolved["policy"]["workspace_id"] == workspace.id


def test_p2_001_2_legacy_model_repair_preserves_workspace_context() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        settings = make_settings()
        event_bus = EventBus()
        models = ModelRepository(session)
        models.upsert(
            provider="openrouter",
            slug="deepseek/deepseek-chat-v3-0324",
            display_name="DeepSeek Chat V3",
            enabled=False,
            priority=10,
            context_window=163840,
            max_output_tokens=8192,
            supports_tools=False,
            supports_vision=False,
            supports_json=True,
            metadata_json={
                "retired_default": True,
                "retired_in": "P2-001.2",
                "replacement": "openrouter/free",
            },
        )
        ModelManager(models, event_bus, settings).seed_defaults()

        workspace = WorkspaceRepository(session).create(
            name="Legacy Workspace",
            status="active",
        )
        repository = SettingsRepository(session)
        repository.upsert(
            scope="workspace",
            key="ai.model",
            value="deepseek/deepseek-chat-v3-0324",
            workspace_id=workspace.id,
        )
        repository.upsert(
            scope="user",
            key=ACTIVE_WORKSPACE_KEY,
            value=workspace.id,
        )
        session.commit()

        resolved = WorkspaceContextResolver(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            model_repository=ModelRepository(session),
            event_bus=event_bus,
            app_settings=settings,
        ).resolve(
            requested_workspace_id=None,
        )

        assert resolved["workspace_id"] == workspace.id
        assert resolved["policy"]["model"] == (
            "deepseek/deepseek-chat-v3-0324"
        )
