from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.settings import SettingsRepository
from backend.services.settings import SettingsService

router = APIRouter(tags=["settings"])


class SettingUpsertRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=32)
    key: str = Field(..., min_length=1, max_length=255)
    value: Any
    project_id: str | None = None
    workspace_id: str | None = None


@router.get("/settings")
def read_effective_settings() -> dict[str, Any]:
    with session_scope() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=event_bus,
        )
        return service.effective_runtime_settings()


@router.get("/settings/items")
def list_setting_items(
    scope: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    with session_scope() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=event_bus,
        )
        try:
            return service.list_settings(
                scope=scope,
                project_id=project_id,
                workspace_id=workspace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/items")
async def upsert_setting(
    request: SettingUpsertRequest,
) -> dict[str, Any]:
    with session_scope() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=event_bus,
        )
        try:
            return await service.set_setting(
                scope=request.scope,
                key=request.key,
                value=request.value,
                project_id=request.project_id,
                workspace_id=request.workspace_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/settings/items/{setting_id}")
async def delete_setting(setting_id: str) -> dict[str, bool]:
    with session_scope() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=event_bus,
        )
        deleted = await service.delete_setting(setting_id)

        if not deleted:
            raise HTTPException(status_code=404, detail="Настройка не найдена.")

        return {"deleted": True}
