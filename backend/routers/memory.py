from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.memory import MemoryRepository
from backend.services.memory import MemoryService

router = APIRouter(tags=["memory"])


class MemoryCreateRequest(BaseModel):
    scope: str = Field(..., min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    source_type: str = Field(..., min_length=1, max_length=32)
    project_id: str | None = None
    workspace_id: str | None = None
    source_id: str | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = []
    metadata: dict[str, Any] = {}


class MemoryUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, min_length=1)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None


def get_service(session) -> MemoryService:
    return MemoryService(
        repository=MemoryRepository(session),
        event_bus=event_bus,
    )


@router.post("/memory")
async def create_memory_item(
    request: MemoryCreateRequest,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return await get_service(session).create_item(
                scope=request.scope,
                title=request.title,
                content=request.content,
                source_type=request.source_type,
                project_id=request.project_id,
                workspace_id=request.workspace_id,
                source_id=request.source_id,
                importance=request.importance,
                tags=request.tags,
                metadata_json=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory")
def list_memory_items(
    scope: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        try:
            items = get_service(session).list_items(
                scope=scope,
                project_id=project_id,
                workspace_id=workspace_id,
                source_type=source_type,
                limit=limit,
            )
            return {"items": items}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/search")
def search_memory(
    query: str = Query(..., min_length=1),
    scope: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        try:
            items = get_service(session).search(
                query=query,
                scope=scope,
                project_id=project_id,
                workspace_id=workspace_id,
                limit=limit,
            )
            return {"items": items}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/{memory_item_id}")
def get_memory_item(memory_item_id: str) -> dict[str, Any]:
    with session_scope() as session:
        item = get_service(session).get_item(memory_item_id)

        if item is None:
            raise HTTPException(
                status_code=404,
                detail="Memory item не найден.",
            )

        return item


@router.patch("/memory/{memory_item_id}")
async def update_memory_item(
    memory_item_id: str,
    request: MemoryUpdateRequest,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            item = await get_service(session).update_item(
                memory_item_id=memory_item_id,
                title=request.title,
                content=request.content,
                importance=request.importance,
                tags=request.tags,
                metadata_json=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if item is None:
            raise HTTPException(
                status_code=404,
                detail="Memory item не найден.",
            )

        return item


@router.delete("/memory/{memory_item_id}")
async def delete_memory_item(memory_item_id: str) -> dict[str, bool]:
    with session_scope() as session:
        deleted = await get_service(session).delete_item(memory_item_id)

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail="Memory item не найден.",
            )

        return {"deleted": True}
