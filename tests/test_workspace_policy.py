import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.model_manager import ModelManager
from backend.services.workspace_policy import WorkspacePolicyService


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


@pytest.mark.asyncio
async def test_workspace_policy_update_and_reset() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        event_bus = EventBus()
        settings = make_settings()

        ModelManager(ModelRepository(session), event_bus, settings).seed_defaults()
        workspace = WorkspaceRepository(session).create(
            name="Crypto AI",
            workspace_type="crypto",
        )

        service = WorkspacePolicyService(
            workspace_repository=WorkspaceRepository(session),
            settings_repository=SettingsRepository(session),
            model_repository=ModelRepository(session),
            event_bus=event_bus,
            app_settings=settings,
        )

        updated = await service.update_policy(
            workspace_id=workspace.id,
            values={
                "ai.model": "openai/gpt-oss-20b:free",
                "ai.temperature": 0.2,
                "ai.max_tokens": 2000,
                "memory.mode": "workspace",
                "plugins.enabled": ["example_status"],
            },
        )
        session.commit()

        assert updated.model == "openai/gpt-oss-20b:free"
        assert updated.temperature == 0.2
        assert service.is_plugin_allowed(
            workspace_id=workspace.id,
            plugin_id="example_status",
        ) is True
        assert service.is_plugin_allowed(
            workspace_id=workspace.id,
            plugin_id="unknown_plugin",
        ) is False

        reset = await service.reset_policy(workspace.id)
        session.commit()

        assert reset.model == settings.default_model
        assert reset.temperature == settings.temperature
