from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from backend.agent_governance.service import AgentProfileService
from backend.code_sandbox.agent import CodeAgentService
from backend.code_sandbox.artifact_approvals import (
    RuntimeArtifactApprovalCoordinator,
)
from backend.code_sandbox.runtime import IsolatedRuntimeService
from backend.code_sandbox.runtime_policy import (
    IsolatedRuntimePolicy,
)
from backend.code_sandbox.service import CodeSandboxService
from backend.core.container import AppContainer
from backend.council.control import CouncilControlService
from backend.council.history import CouncilHistoryService
from backend.council.live import CouncilLiveManager
from backend.council.repository import CouncilRunRepository
from backend.database.session import session_scope
from backend.documents.ai_service import DocumentAIAnalysisService
from backend.documents.extraction_service import (
    DocumentExtractionService,
)
from backend.documents.ocr_service import DocumentOCRService
from backend.documents.service import DocumentRegistryService
from backend.gateway.factory import build_ai_gateway
from backend.gateway.service import AIGateway
from backend.policy_approvals.service import PolicyApprovalService
from backend.repositories.settings import SettingsRepository
from backend.runtime_policy import DataClassification
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


def get_agent_profile_service(
    session: Session = Depends(get_db_session),
) -> AgentProfileService:
    return AgentProfileService(session)


def get_policy_approval_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> PolicyApprovalService:
    return PolicyApprovalService(
        session=session,
        event_bus=container.event_bus,
    )


def get_document_registry_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> DocumentRegistryService:
    return DocumentRegistryService(
        session=session,
        event_bus=container.event_bus,
    )


def get_document_extraction_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> DocumentExtractionService:
    return DocumentExtractionService(
        session=session,
        event_bus=container.event_bus,
    )


def get_document_ocr_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> DocumentOCRService:
    return DocumentOCRService(
        session=session,
        event_bus=container.event_bus,
    )


def get_document_ai_analysis_service(
    session: Session = Depends(get_db_session),
    container: AppContainer = Depends(get_container),
) -> DocumentAIAnalysisService:
    return DocumentAIAnalysisService(
        session=session,
        event_bus=container.event_bus,
        gateway=build_ai_gateway(
            settings=container.settings,
            event_bus=container.event_bus,
            secret_manager=container.secret_manager_service,
        ),
        workspace_policy=container.workspace_policy_service(session),
    )


def get_ai_gateway(
    container: AppContainer = Depends(get_container),
) -> AIGateway:
    return build_ai_gateway(
        settings=container.settings,
        event_bus=container.event_bus,
        secret_manager=container.secret_manager_service,
    )


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
    sandbox = CodeSandboxService(
        session=session,
        event_bus=container.event_bus,
    )
    policy_service = (
        container.workspace_policy_service(
            session
        )
    )

    def resolve_data_classification(
        workspace_id: str | None,
    ) -> DataClassification:
        if workspace_id is None:
            return DataClassification.INTERNAL

        return (
            policy_service
            .get_effective_policy(workspace_id)
            .data_classification
        )

    runtime_policy = IsolatedRuntimePolicy(
        event_bus=container.event_bus,
        resolver=resolve_data_classification,
    )

    return IsolatedRuntimeService(
        session=session,
        event_bus=container.event_bus,
        sandbox=sandbox,
        runtime_policy=runtime_policy,
        artifact_approval_coordinator=(
            RuntimeArtifactApprovalCoordinator(
                session_scope_factory=session_scope,
                event_bus=container.event_bus,
            )
        ),
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
