from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.database.session import session_scope
from backend.repositories.chats import ChatRepository
from backend.repositories.projects import ProjectRepository
from backend.repositories.workspaces import WorkspaceRepository

router = APIRouter(tags=["repository"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    workspace_id: str | None = None


class ChatCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    mode: str = "universal"
    model_name: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None


class MessageCreateRequest(BaseModel):
    role: str = Field(..., min_length=1, max_length=32)
    content: str = Field(..., min_length=1)
    model_name: str | None = None


@router.post("/projects")
def create_project(request: ProjectCreateRequest) -> dict:
    with session_scope() as session:
        if request.workspace_id:
            workspace = WorkspaceRepository(session).get(request.workspace_id)
            if workspace is None:
                raise HTTPException(
                    status_code=404,
                    detail="Workspace не найден.",
                )

        repo = ProjectRepository(session)
        existing = repo.find_by_name(
            request.name,
            workspace_id=request.workspace_id,
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail="Проект с таким именем уже существует в этом Workspace.",
            )

        project = repo.create(
            name=request.name,
            description=request.description,
            workspace_id=request.workspace_id,
        )

        return {
            "id": project.id,
            "workspace_id": project.workspace_id,
            "name": project.name,
            "description": project.description,
            "status": project.status,
        }


@router.get("/projects")
def list_projects() -> list[dict]:
    with session_scope() as session:
        projects = ProjectRepository(session).list(limit=100)
        return [
            {
                "id": item.id,
                "workspace_id": item.workspace_id,
                "name": item.name,
                "description": item.description,
                "status": item.status,
            }
            for item in projects
        ]


@router.post("/chats")
def create_chat(request: ChatCreateRequest) -> dict:
    with session_scope() as session:
        project = None

        if request.workspace_id:
            workspace = WorkspaceRepository(session).get(request.workspace_id)
            if workspace is None:
                raise HTTPException(
                    status_code=404,
                    detail="Workspace не найден.",
                )

        if request.project_id:
            project = ProjectRepository(session).get(request.project_id)
            if project is None:
                raise HTTPException(
                    status_code=404,
                    detail="Проект не найден.",
                )

            if (
                request.workspace_id
                and project.workspace_id
                and project.workspace_id != request.workspace_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Проект принадлежит другому Workspace.",
                )

        resolved_workspace_id = (
            request.workspace_id
            or (project.workspace_id if project else None)
        )

        chat = ChatRepository(session).create(
            title=request.title,
            mode=request.mode,
            model_name=request.model_name,
            project_id=request.project_id,
            workspace_id=resolved_workspace_id,
        )

        return {
            "id": chat.id,
            "workspace_id": chat.workspace_id,
            "project_id": chat.project_id,
            "title": chat.title,
            "mode": chat.mode,
            "model_name": chat.model_name,
        }


@router.post("/chats/{chat_id}/messages")
def add_chat_message(
    chat_id: str,
    request: MessageCreateRequest,
) -> dict:
    with session_scope() as session:
        repo = ChatRepository(session)

        if repo.get(chat_id) is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")

        message = repo.add_message(
            chat_id=chat_id,
            role=request.role,
            content=request.content,
            model_name=request.model_name,
        )

        return {
            "id": message.id,
            "chat_id": message.chat_id,
            "role": message.role,
            "content": message.content,
            "model_name": message.model_name,
        }


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str) -> dict:
    with session_scope() as session:
        chat = ChatRepository(session).get_with_messages(chat_id)

        if chat is None:
            raise HTTPException(status_code=404, detail="Чат не найден.")

        return {
            "id": chat.id,
            "workspace_id": chat.workspace_id,
            "project_id": chat.project_id,
            "title": chat.title,
            "mode": chat.mode,
            "model_name": chat.model_name,
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "model_name": message.model_name,
                    "created_at": message.created_at.isoformat(),
                }
                for message in chat.messages
            ],
        }
