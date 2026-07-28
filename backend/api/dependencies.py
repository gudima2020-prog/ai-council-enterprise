from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.core.container import AppContainer
from backend.code_sandbox.service import CodeSandboxService
from backend.code_sandbox.agent import CodeAgentService
from backend.code_sandbox.runtime import IsolatedRuntimeService
from backend.gateway.factory import build_ai_gateway
from backend.council.control import CouncilControlService
from backend.council.history import CouncilHistoryService
from backend.council.live import CouncilLiveManager
from backend.council.repository import CouncilRunRepository
from backend.database.session import session_scope
from backend.repositories.settings import SettingsRepository
from backend.services.workspace_policy import WorkspacePolicyService
from backend.services.workspace_state import ActiveWorkspaceService
from backend.services.workspaces import WorkspaceService


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)

    if container is None:
        raise RuntimeError("Application dependency container is not initialized.")

    return container


def get_db_session() -> Generator[Session, None, None]:
    with session_scope() as session:
        yield session


def get_workspace_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> WorkspaceService:
    return container.workspace_service(session)


def get_workspace_policy_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> WorkspacePolicyService:
    return container.workspace_policy_service(session)


def get_active_workspace_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> ActiveWorkspaceService:
    return container.active_workspace_service(session)


def get_council_control_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> CouncilControlService:
    return CouncilControlService(
        session=session,
        event_bus=container.event_bus,
    )


def get_council_history_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> CouncilHistoryService:
    return CouncilHistoryService(
        repository=CouncilRunRepository(session),
        event_bus=container.event_bus,
        settings_repository=SettingsRepository(session),
    )


def get_council_live_manager(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> CouncilLiveManager:
    manager = getattr(request.app.state, "council_live_manager", None)
    if manager is None:
        manager = CouncilLiveManager(event_bus=container.event_bus)
        request.app.state.council_live_manager = manager
    return manager


def get_code_sandbox_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> CodeSandboxService:
    return CodeSandboxService(session=session, event_bus=container.event_bus)


def get_isolated_runtime_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> IsolatedRuntimeService:
    sandbox = CodeSandboxService(session=session, event_bus=container.event_bus)
    return IsolatedRuntimeService(
        session=session,
        event_bus=container.event_bus,
        sandbox=sandbox,
    )


def get_code_agent_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> CodeAgentService:
    sandbox = CodeSandboxService(session=session, event_bus=container.event_bus)
    gateway = build_ai_gateway(
        settings=container.settings,
        event_bus=container.event_bus,
        secret_manager=container.secret_manager_service,
    )
    return CodeAgentService(
        session=session,
        event_bus=container.event_bus,
        gateway=gateway,
        sandbox=sandbox,
    )
