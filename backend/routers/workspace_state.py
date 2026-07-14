from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.core.events import event_bus
from backend.database.session import session_scope
from backend.repositories.chats import ChatRepository
from backend.repositories.memory import MemoryRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspace_state import ActiveWorkspaceService

router = APIRouter(tags=["workspace-state"])


class ActivateWorkspaceRequest(BaseModel):
    workspace_id: str = Field(..., min_length=1, max_length=64)


def get_service(session) -> ActiveWorkspaceService:
    return ActiveWorkspaceService(
        workspace_repository=WorkspaceRepository(session),
        settings_repository=SettingsRepository(session),
        project_repository=ProjectRepository(session),
        chat_repository=ChatRepository(session),
        memory_repository=MemoryRepository(session),
        event_bus=event_bus,
    )


@router.get("/workspaces/active")
def get_active_workspace() -> dict[str, Any]:
    with session_scope() as session:
        workspace = get_service(session).get_active_workspace()

        return {
            "active": workspace is not None,
            "workspace": workspace,
        }


@router.put("/workspaces/active")
async def activate_workspace(
    request: ActivateWorkspaceRequest,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            workspace = await get_service(session).activate_workspace(
                request.workspace_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "active": True,
            "workspace": workspace,
        }


@router.delete("/workspaces/active")
async def clear_active_workspace() -> dict[str, bool]:
    with session_scope() as session:
        cleared = await get_service(session).clear_active_workspace()

        return {
            "cleared": cleared,
        }
