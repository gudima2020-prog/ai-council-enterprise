from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.config import get_settings
from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.models import ModelRepository
from backend.services.model_manager import ModelManager

router = APIRouter(tags=["models"])


class ModelUpsertRequest(BaseModel):
    provider: str
    slug: str
    display_name: str
    enabled: bool = True
    priority: int = Field(default=100, ge=0)
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json: bool = False
    metadata: dict[str, Any] = {}


def get_manager(session) -> ModelManager:
    return ModelManager(ModelRepository(session), event_bus, get_settings())


@router.get("/models")
def list_models(enabled_only: bool = Query(False)):
    with session_scope() as session:
        return {"models": get_manager(session).list_models(enabled_only)}


@router.get("/models/resolve")
def resolve_model(
    requested_slug: str | None = None,
    require_tools: bool = False,
    require_vision: bool = False,
    require_json: bool = False,
):
    with session_scope() as session:
        try:
            return get_manager(session).resolve_model(
                requested_slug,
                require_tools,
                require_vision,
                require_json,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/models/{slug:path}")
def get_model(slug: str):
    with session_scope() as session:
        result = get_manager(session).get_model(slug)
        if result is None:
            raise HTTPException(status_code=404, detail="Модель не найдена.")
        return result


@router.put("/models")
async def upsert_model(request: ModelUpsertRequest):
    with session_scope() as session:
        return await get_manager(session).upsert_model(
            provider=request.provider,
            slug=request.slug,
            display_name=request.display_name,
            enabled=request.enabled,
            priority=request.priority,
            context_window=request.context_window,
            max_output_tokens=request.max_output_tokens,
            supports_tools=request.supports_tools,
            supports_vision=request.supports_vision,
            supports_json=request.supports_json,
            metadata_json=request.metadata,
        )
