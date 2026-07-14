from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspaces import WorkspaceService

router = APIRouter(tags=["workspaces"])


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    workspace_type: str = "general"
    icon: str | None = None
    color: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = {}


class WorkspaceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    workspace_type: str | None = None
    icon: str | None = None
    color: str | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


def get_service(session) -> WorkspaceService:
    return WorkspaceService(
        repository=WorkspaceRepository(session),
        event_bus=event_bus,
    )


def ensure_workspace(session, workspace_id: str):
    workspace = WorkspaceRepository(session).get(workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace не найден.")
    return workspace


async def apply_lifecycle_action(
    *,
    workspace_id: str,
    action: str,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            workspace = await get_service(session).change_lifecycle(
                workspace_id=workspace_id,
                action=action,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace не найден.")

        return workspace


@router.post("/workspaces")
async def create_workspace(request: WorkspaceCreateRequest) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return await get_service(session).create_workspace(
                name=request.name,
                description=request.description,
                workspace_type=request.workspace_type,
                icon=request.icon,
                color=request.color,
                status=request.status,
                metadata_json=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workspaces")
def list_workspaces(
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        return {
            "workspaces": get_service(session).list_workspaces(
                active_only=active_only,
                limit=limit,
            )
        }


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> dict[str, Any]:
    with session_scope() as session:
        workspace = get_service(session).get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace не найден.")
        return workspace


@router.get("/workspaces/{workspace_id}/summary")
def get_workspace_summary(workspace_id: str) -> dict[str, Any]:
    with session_scope() as session:
        workspace = ensure_workspace(session, workspace_id)

        projects = ProjectRepository(session).list_by_workspace(
            workspace_id,
            limit=500,
        )
        chats = ChatRepository(session).list_by_workspace(
            workspace_id,
            limit=500,
        )
        memory_items = MemoryRepository(session).list_filtered(
            workspace_id=workspace_id,
            limit=500,
        )

        return {
            "workspace": {
                "id": workspace.id,
                "name": workspace.name,
                "workspace_type": workspace.workspace_type,
                "status": workspace.status,
            },
            "counts": {
                "projects": len(projects),
                "chats": len(chats),
                "memory_items": len(memory_items),
            },
        }


@router.get("/workspaces/{workspace_id}/projects")
def list_workspace_projects(workspace_id: str) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        ensure_workspace(session, workspace_id)
        rows = ProjectRepository(session).list_by_workspace(workspace_id)

        return {
            "projects": [
                {
                    "id": row.id,
                    "workspace_id": row.workspace_id,
                    "name": row.name,
                    "description": row.description,
                    "status": row.status,
                }
                for row in rows
            ]
        }


@router.get("/workspaces/{workspace_id}/chats")
def list_workspace_chats(workspace_id: str) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        ensure_workspace(session, workspace_id)
        rows = ChatRepository(session).list_by_workspace(workspace_id)

        return {
            "chats": [
                {
                    "id": row.id,
                    "workspace_id": row.workspace_id,
                    "project_id": row.project_id,
                    "title": row.title,
                    "mode": row.mode,
                    "model_name": row.model_name,
                }
                for row in rows
            ]
        }


@router.get("/workspaces/{workspace_id}/memory")
def list_workspace_memory(workspace_id: str) -> dict[str, list[dict[str, Any]]]:
    with session_scope() as session:
        ensure_workspace(session, workspace_id)
        rows = MemoryRepository(session).list_filtered(
            workspace_id=workspace_id,
            limit=500,
        )

        return {
            "items": [
                {
                    "id": row.id,
                    "workspace_id": row.workspace_id,
                    "project_id": row.project_id,
                    "scope": row.scope,
                    "source_type": row.source_type,
                    "title": row.title,
                    "content": row.content,
                    "importance": row.importance,
                    "tags": row.tags_json,
                }
                for row in rows
            ]
        }


@router.post("/workspaces/{workspace_id}/archive")
async def archive_workspace(workspace_id: str) -> dict[str, Any]:
    return await apply_lifecycle_action(
        workspace_id=workspace_id,
        action="archive",
    )


@router.post("/workspaces/{workspace_id}/restore")
async def restore_workspace(workspace_id: str) -> dict[str, Any]:
    return await apply_lifecycle_action(
        workspace_id=workspace_id,
        action="restore",
    )


@router.post("/workspaces/{workspace_id}/disable")
async def disable_workspace(workspace_id: str) -> dict[str, Any]:
    return await apply_lifecycle_action(
        workspace_id=workspace_id,
        action="disable",
    )


@router.post("/workspaces/{workspace_id}/enable")
async def enable_workspace(workspace_id: str) -> dict[str, Any]:
    return await apply_lifecycle_action(
        workspace_id=workspace_id,
        action="enable",
    )


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdateRequest,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            workspace = await get_service(session).update_workspace(
                workspace_id=workspace_id,
                name=request.name,
                description=request.description,
                workspace_type=request.workspace_type,
                icon=request.icon,
                color=request.color,
                status=request.status,
                metadata_json=request.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if workspace is None:
            raise HTTPException(status_code=404, detail="Workspace не найден.")

        return workspace


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str) -> dict[str, bool]:
    with session_scope() as session:
        deleted = await get_service(session).delete_workspace(workspace_id)

        if not deleted:
            raise HTTPException(status_code=404, detail="Workspace не найден.")

        return {"deleted": True}
