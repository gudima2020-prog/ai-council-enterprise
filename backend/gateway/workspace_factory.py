from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.core.workspace_context import get_workspace_context
from backend.gateway.factory import build_ai_gateway
from backend.gateway.service import AIGateway
from backend.secrets.service import SecretManagerService


def build_workspace_ai_gateway(
    *,
    settings: AppSettings,
    event_bus: EventBus,
    workspace_id: str | None = None,
    secret_manager: SecretManagerService | None = None,
) -> tuple[AIGateway, dict | None]:
    """
    Builds the shared AI Gateway and returns the already-resolved policy
    from the current request context.

    Explicit workspace_id is accepted for service-layer calls outside HTTP.
    HTTP calls should rely on WorkspaceContextMiddleware.
    """
    context = get_workspace_context()

    if workspace_id is not None:
        if context is None or context.workspace_id != workspace_id:
            raise RuntimeError(
                "Explicit workspace_id does not match current request context."
            )

    gateway = build_ai_gateway(
        settings=settings,
        event_bus=event_bus,
        secret_manager=secret_manager,
    )

    return gateway, None if context is None else context.policy
