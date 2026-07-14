from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.core.config import get_settings
from backend.core.events import event_bus
from backend.core.logging import LoggerManager
from backend.core.workspace_context import (
    WorkspaceRequestContext,
    reset_workspace_context,
    set_workspace_context,
)
from backend.database.session import session_scope
from backend.repositories.models import ModelRepository
from backend.repositories.settings import SettingsRepository
from backend.repositories.workspaces import WorkspaceRepository
from backend.services.workspace_context import WorkspaceContextResolver


class WorkspaceContextMiddleware(BaseHTTPMiddleware):
    HEADER_NAME = "X-Workspace-ID"

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = LoggerManager.get_logger("workspace_context")

    async def dispatch(self, request: Request, call_next):
        requested_workspace_id = request.headers.get(self.HEADER_NAME)

        try:
            with session_scope() as session:
                resolved = WorkspaceContextResolver(
                    workspace_repository=WorkspaceRepository(session),
                    settings_repository=SettingsRepository(session),
                    model_repository=ModelRepository(session),
                    event_bus=event_bus,
                    app_settings=get_settings(),
                ).resolve(
                    requested_workspace_id=requested_workspace_id,
                )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"detail": str(exc)},
            )

        context = WorkspaceRequestContext(
            workspace_id=resolved["workspace_id"],
            source=resolved["source"],
            policy=resolved["policy"],
        )
        token = set_workspace_context(context)

        request.state.workspace_context = resolved

        try:
            response = await call_next(request)
        finally:
            reset_workspace_context(token)

        if resolved["workspace_id"]:
            response.headers["X-Workspace-ID"] = resolved["workspace_id"]
            response.headers["X-Workspace-Source"] = resolved["source"]

        return response
