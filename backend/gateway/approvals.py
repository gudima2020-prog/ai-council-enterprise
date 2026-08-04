from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.gateway.schemas import GatewayRequest
from backend.policy_approvals.core import (
    PolicyApprovalExpiredError,
    PolicyApprovalScope,
    PolicyApprovalScopeError,
    PolicyApprovalStateError,
    PolicyApprovalTokenError,
)
from backend.policy_approvals.service import (
    PolicyApprovalNotFoundError,
    PolicyApprovalService,
    PolicyApprovalServiceError,
    PolicyApprovalWorkspaceError,
)
from backend.runtime_policy import (
    PolicyOperation,
    RuntimePolicyDecision,
)


SessionScopeFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class GatewayApprovalOutcome:
    authorized: bool
    error_code: str | None
    message: str | None
    metadata: dict[str, Any]


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def gateway_request_fingerprint(request: GatewayRequest) -> str:
    payload = {
        "schema_version": "p2-012.3a",
        "workspace_id": request.workspace_id,
        "provider": request.provider,
        "model": request.model,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "timeout_seconds": request.timeout_seconds,
        "source": request.source,
        "mode": request.mode,
        "messages": [
            {
                "role": message.role,
                "content_sha256": hashlib.sha256(
                    message.content.encode("utf-8")
                ).hexdigest(),
            }
            for message in request.messages
        ],
    }
    return hashlib.sha256(
        _canonical(payload).encode("utf-8")
    ).hexdigest()


class GatewayApprovalCoordinator:
    """Creates or consumes exact-scope approvals for one AI route."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory,
        event_bus: EventBus,
    ) -> None:
        self._session_scope_factory = session_scope_factory
        self._event_bus = event_bus

    async def request(
        self,
        *,
        request: GatewayRequest,
        decision: RuntimePolicyDecision,
        requested_by: str | None = None,
    ) -> GatewayApprovalOutcome:
        try:
            scope = self.build_scope(
                request=request,
                decision=decision,
            )
            with self._session_scope_factory() as session:
                service = PolicyApprovalService(
                    session=session,
                    event_bus=self._event_bus,
                )
                result = await service.request(
                    scope=scope,
                    reason_codes=decision.reason_codes,
                    requested_by=(
                        requested_by
                        or request.source
                        or "ai-gateway"
                    ),
                    request_note=(
                        "AI Gateway route requires Runtime Policy "
                        "approval."
                    ),
                    metadata={
                        "gateway_request_id": request.request_id,
                        "route_provider": request.provider,
                        "route_model": request.model,
                    },
                )
                record = result.record
                metadata = {
                    "approval_id": record.id,
                    "status": record.status.value,
                    "created": result.created,
                    "request_id": request.request_id,
                    "scope_fingerprint": (
                        record.scope_fingerprint
                    ),
                    "expires_at": (
                        record.expires_at.isoformat()
                    ),
                }
        except PolicyApprovalWorkspaceError:
            return self.failure(
                code="POLICY_APPROVAL_WORKSPACE_INVALID",
                message=(
                    "The policy approval Workspace is unavailable."
                ),
                metadata={
                    "status": "workspace_invalid",
                    "request_id": request.request_id,
                },
            )
        except (PolicyApprovalServiceError, ValueError):
            return self.failure(
                code="POLICY_APPROVAL_RESOLUTION_FAILED",
                message=(
                    "The policy approval request could not be "
                    "persisted."
                ),
                metadata={
                    "status": "resolution_failed",
                    "request_id": request.request_id,
                },
            )

        return self.failure(
            code="POLICY_APPROVAL_REQUIRED",
            message=(
                "Human approval is required for this exact AI "
                "Gateway route."
            ),
            metadata=metadata,
        )

    async def consume(
        self,
        *,
        request: GatewayRequest,
        decision: RuntimePolicyDecision,
        approval_id: str,
        token: str,
    ) -> GatewayApprovalOutcome:
        normalized_id = approval_id.strip()
        if not normalized_id or not token:
            return self.failure(
                code="POLICY_APPROVAL_CREDENTIALS_INCOMPLETE",
                message=(
                    "Both policy approval id and token are required."
                ),
                metadata={
                    "status": "credentials_incomplete",
                    "request_id": request.request_id,
                },
            )

        try:
            scope = self.build_scope(
                request=request,
                decision=decision,
            )
            with self._session_scope_factory() as session:
                service = PolicyApprovalService(
                    session=session,
                    event_bus=self._event_bus,
                )
                record = await service.consume(
                    approval_id=normalized_id,
                    workspace_id=request.workspace_id or "",
                    token=token,
                    scope=scope,
                )
                metadata = {
                    "approval_id": record.id,
                    "status": record.status.value,
                    "request_id": request.request_id,
                    "scope_fingerprint": (
                        record.scope_fingerprint
                    ),
                    "consumed_at": (
                        record.consumed_at.isoformat()
                        if record.consumed_at is not None
                        else None
                    ),
                }
        except PolicyApprovalNotFoundError:
            return self.failure(
                code="POLICY_APPROVAL_NOT_FOUND",
                message=(
                    "The policy approval was not found in this "
                    "Workspace."
                ),
                metadata={
                    "approval_id": normalized_id,
                    "status": "not_found",
                    "request_id": request.request_id,
                },
            )
        except PolicyApprovalWorkspaceError:
            return self.failure(
                code="POLICY_APPROVAL_WORKSPACE_INVALID",
                message=(
                    "The policy approval Workspace is unavailable."
                ),
                metadata={
                    "approval_id": normalized_id,
                    "status": "workspace_invalid",
                    "request_id": request.request_id,
                },
            )
        except PolicyApprovalExpiredError:
            return self.failure(
                code="POLICY_APPROVAL_EXPIRED",
                message="The policy approval has expired.",
                metadata={
                    "approval_id": normalized_id,
                    "status": "expired",
                    "request_id": request.request_id,
                },
            )
        except PolicyApprovalScopeError:
            return self.failure(
                code="POLICY_APPROVAL_SCOPE_MISMATCH",
                message=(
                    "The policy approval does not match the exact "
                    "AI Gateway request."
                ),
                metadata={
                    "approval_id": normalized_id,
                    "status": "scope_mismatch",
                    "request_id": request.request_id,
                },
            )
        except PolicyApprovalTokenError:
            return self.failure(
                code="POLICY_APPROVAL_TOKEN_INVALID",
                message="The policy approval token is invalid.",
                metadata={
                    "approval_id": normalized_id,
                    "status": "token_invalid",
                    "request_id": request.request_id,
                },
            )
        except PolicyApprovalStateError:
            return self.failure(
                code="POLICY_APPROVAL_INVALID_STATE",
                message=(
                    "The policy approval is not available for "
                    "one-time consumption."
                ),
                metadata={
                    "approval_id": normalized_id,
                    "status": "invalid_state",
                    "request_id": request.request_id,
                },
            )
        except (PolicyApprovalServiceError, ValueError):
            return self.failure(
                code="POLICY_APPROVAL_CONSUME_FAILED",
                message=(
                    "The policy approval could not be consumed."
                ),
                metadata={
                    "approval_id": normalized_id,
                    "status": "consume_failed",
                    "request_id": request.request_id,
                },
            )

        return GatewayApprovalOutcome(
            authorized=True,
            error_code=None,
            message=None,
            metadata=metadata,
        )

    @staticmethod
    def build_scope(
        *,
        request: GatewayRequest,
        decision: RuntimePolicyDecision,
    ) -> PolicyApprovalScope:
        if not request.workspace_id:
            raise PolicyApprovalWorkspaceError(
                "AI Gateway policy approvals require a Workspace."
            )
        return PolicyApprovalScope(
            workspace_id=request.workspace_id,
            operation=PolicyOperation.MODEL_INFERENCE,
            policy_version=decision.policy_version,
            policy_fingerprint=decision.fingerprint,
            subject_type="gateway_route",
            subject_id=request.request_id,
            subject_payload={
                "provider": request.provider,
                "model": request.model,
                "request_fingerprint": (
                    gateway_request_fingerprint(request)
                ),
            },
        )

    @staticmethod
    def failure(
        *,
        code: str,
        message: str,
        metadata: dict[str, Any],
    ) -> GatewayApprovalOutcome:
        return GatewayApprovalOutcome(
            authorized=False,
            error_code=code,
            message=message,
            metadata=metadata,
        )
