from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.config import get_settings
from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.runtime_policy import (
    DataClassification,
    ProviderTrust,
)
from backend.services.workspace_policy import (
    WorkspacePolicyService,
)

router = APIRouter(tags=["workspace-policy"])


class WorkspacePolicyUpdateRequest(BaseModel):
    ai_provider: str | None = Field(
        default=None,
        min_length=1,
    )
    ai_model: str | None = Field(
        default=None,
        min_length=1,
    )
    ai_temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
    )
    ai_max_tokens: int | None = Field(
        default=None,
        ge=1,
    )
    memory_mode: str | None = None
    network_access: str | None = None
    filesystem_access: str | None = None
    data_classification: DataClassification | None = None
    provider_trust: dict[str, ProviderTrust] | None = None
    enabled_plugins: list[str] | None = None
    disabled_plugins: list[str] | None = None

    def to_policy_values(self) -> dict[str, Any]:
        mapping = {
            "ai_provider": "ai.provider",
            "ai_model": "ai.model",
            "ai_temperature": "ai.temperature",
            "ai_max_tokens": "ai.max_tokens",
            "memory_mode": "memory.mode",
            "network_access": "security.network_access",
            "filesystem_access": "security.filesystem_access",
            "data_classification": (
                "security.data_classification"
            ),
            "provider_trust": "security.provider_trust",
            "enabled_plugins": "plugins.enabled",
            "disabled_plugins": "plugins.disabled",
        }

        values: dict[str, Any] = {}

        for field_name, policy_key in mapping.items():
            value = getattr(self, field_name)

            if value is None:
                continue

            if field_name == "data_classification":
                values[policy_key] = value.value
                continue

            if field_name == "provider_trust":
                values[policy_key] = {
                    str(provider).strip().lower(): trust.value
                    for provider, trust in value.items()
                }
                continue

            values[policy_key] = value

        return values


def get_service(session) -> WorkspacePolicyService:
    return WorkspacePolicyService(
        workspace_repository=WorkspaceRepository(session),
        settings_repository=SettingsRepository(session),
        model_repository=ModelRepository(session),
        event_bus=event_bus,
        app_settings=get_settings(),
    )


@router.get("/workspaces/{workspace_id}/policy")
def get_workspace_policy(
    workspace_id: str,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return get_service(
                session
            ).get_effective_policy(
                workspace_id
            ).to_dict()
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc


@router.put("/workspaces/{workspace_id}/policy")
async def update_workspace_policy(
    workspace_id: str,
    request: WorkspacePolicyUpdateRequest,
) -> dict[str, Any]:
    values = request.to_policy_values()

    if not values:
        raise HTTPException(
            status_code=400,
            detail=(
                "?? ???????? ?? ?????? ????????? ????????."
            ),
        )

    with session_scope() as session:
        try:
            result = await get_service(
                session
            ).update_policy(
                workspace_id=workspace_id,
                values=values,
            )
            return result.to_dict()
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc


@router.delete("/workspaces/{workspace_id}/policy")
async def reset_workspace_policy(
    workspace_id: str,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            result = await get_service(
                session
            ).reset_policy(
                workspace_id
            )
            return result.to_dict()
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc


@router.get(
    "/workspaces/{workspace_id}/plugins/{plugin_id}/allowed"
)
def is_workspace_plugin_allowed(
    workspace_id: str,
    plugin_id: str,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            allowed = get_service(
                session
            ).is_plugin_allowed(
                workspace_id=workspace_id,
                plugin_id=plugin_id,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail=str(exc),
            ) from exc

        return {
            "workspace_id": workspace_id,
            "plugin_id": plugin_id,
            "allowed": allowed,
        }
