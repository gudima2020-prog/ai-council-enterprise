import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.routers.workspace_policy import (
    WorkspacePolicyUpdateRequest,
)
from backend.runtime_policy import (
    DataClassification,
    ProviderTrust,
)
from backend.services.model_manager import ModelManager
from backend.services.workspace_policy import (
    WorkspacePolicyService,
)


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


def make_service(
    session: Session,
) -> tuple[WorkspacePolicyService, str]:
    event_bus = EventBus()
    settings = make_settings()

    ModelManager(
        ModelRepository(session),
        event_bus,
        settings,
    ).seed_defaults()

    workspace = WorkspaceRepository(session).create(
        name="Policy Test",
        workspace_type="chat",
    )

    service = WorkspacePolicyService(
        workspace_repository=WorkspaceRepository(session),
        settings_repository=SettingsRepository(session),
        model_repository=ModelRepository(session),
        event_bus=event_bus,
        app_settings=settings,
    )

    return service, workspace.id


@pytest.mark.asyncio
async def test_workspace_policy_security_defaults_and_update() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        service, workspace_id = make_service(session)

        initial = service.get_effective_policy(
            workspace_id
        )

        assert (
            initial.data_classification
            == DataClassification.INTERNAL
        )
        assert (
            initial.provider_trust_for("openrouter")
            == ProviderTrust.EXTERNAL
        )
        assert initial.provider_trust == {}

        updated = await service.update_policy(
            workspace_id=workspace_id,
            values={
                "security.data_classification": (
                    "confidential"
                ),
                "security.provider_trust": {
                    "OpenRouter": "trusted_external",
                    "ollama": "local",
                },
            },
        )

        assert (
            updated.data_classification
            == DataClassification.CONFIDENTIAL
        )
        assert (
            updated.provider_trust_for("openrouter")
            == ProviderTrust.TRUSTED_EXTERNAL
        )
        assert (
            updated.provider_trust_for("ollama")
            == ProviderTrust.LOCAL
        )
        assert (
            updated.provider_trust_for("unknown")
            == ProviderTrust.EXTERNAL
        )

        payload = updated.to_dict()

        assert payload["data_classification"] == "confidential"
        assert payload["provider_trust"] == {
            "ollama": "local",
            "openrouter": "trusted_external",
        }

        reset = await service.reset_policy(
            workspace_id
        )

        assert (
            reset.data_classification
            == DataClassification.INTERNAL
        )
        assert reset.provider_trust == {}


@pytest.mark.asyncio
async def test_workspace_policy_rejects_unknown_trust_level() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        service, workspace_id = make_service(session)

        with pytest.raises(
            ValueError,
            match="Unsupported provider trust level",
        ):
            await service.update_policy(
                workspace_id=workspace_id,
                values={
                    "security.provider_trust": {
                        "openrouter": "super_trusted",
                    },
                },
            )


@pytest.mark.asyncio
async def test_workspace_policy_rejects_unknown_classification() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        service, workspace_id = make_service(session)

        with pytest.raises(
            ValueError,
            match=(
                "Unsupported "
                "security.data_classification"
            ),
        ):
            await service.update_policy(
                workspace_id=workspace_id,
                values={
                    "security.data_classification": (
                        "top_secret"
                    ),
                },
            )


def test_workspace_policy_request_serializes_security_enums() -> None:
    request = WorkspacePolicyUpdateRequest(
        data_classification=(
            DataClassification.CONFIDENTIAL
        ),
        provider_trust={
            "OpenRouter": ProviderTrust.TRUSTED_EXTERNAL,
            "ollama": ProviderTrust.LOCAL,
        },
    )

    assert request.to_policy_values() == {
        "security.data_classification": "confidential",
        "security.provider_trust": {
            "openrouter": "trusted_external",
            "ollama": "local",
        },
    }
