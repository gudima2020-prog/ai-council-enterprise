from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from backend.code_sandbox.runtime_policy import RuntimePolicyEvaluation
from backend.core.events import EventBus
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
from backend.runtime_policy import PolicyAction, PolicyOperation

SessionScopeFactory = Callable[[], AbstractContextManager[Session]]


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class RuntimeArtifactDescriptor:
    workspace_id: str
    run_id: str
    session_id: str
    profile: str
    bundle_sha256: str
    manifest_fingerprint: str
    bundle_size: int
    artifact_count: int

    def subject_payload(self) -> dict[str, Any]:
        return {
            "bundle_sha256": self.bundle_sha256,
            "manifest_fingerprint": self.manifest_fingerprint,
            "bundle_size": self.bundle_size,
            "artifact_count": self.artifact_count,
            "profile": self.profile,
        }


@dataclass(frozen=True)
class RuntimeArtifactApprovalOutcome:
    authorized: bool
    error_code: str | None
    message: str | None
    metadata: dict[str, Any]


def build_runtime_artifact_descriptor(
    *,
    workspace_id: str,
    run_id: str,
    session_id: str,
    profile: str,
    bundle: bytes,
    artifact_paths: Iterable[str],
) -> RuntimeArtifactDescriptor:
    workspace_id = workspace_id.strip()
    run_id = run_id.strip()
    session_id = session_id.strip()
    profile = profile.strip()
    if not workspace_id:
        raise ValueError("Runtime artifact approval requires a Workspace.")
    if not run_id or not session_id or not profile:
        raise ValueError("Runtime artifact identifiers must not be empty.")
    if not bundle:
        raise ValueError("Runtime artifact bundle must not be empty.")

    paths = tuple(
        sorted({str(value).strip() for value in artifact_paths if str(value).strip()})
    )
    if not paths:
        raise ValueError("Runtime artifact manifest must not be empty.")

    bundle_sha256 = hashlib.sha256(bundle).hexdigest()
    manifest = {
        "schema_version": "p2-012.4b",
        "workspace_id": workspace_id,
        "run_id": run_id,
        "session_id": session_id,
        "profile": profile,
        "bundle_sha256": bundle_sha256,
        "bundle_size": len(bundle),
        "artifact_paths": list(paths),
    }
    manifest_fingerprint = hashlib.sha256(
        _canonical(manifest).encode("utf-8")
    ).hexdigest()

    return RuntimeArtifactDescriptor(
        workspace_id=workspace_id,
        run_id=run_id,
        session_id=session_id,
        profile=profile,
        bundle_sha256=bundle_sha256,
        manifest_fingerprint=manifest_fingerprint,
        bundle_size=len(bundle),
        artifact_count=len(paths),
    )


class RuntimeArtifactApprovalCoordinator:
    """Creates and consumes exact-scope approvals for immutable ZIP bundles."""

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
        descriptor: RuntimeArtifactDescriptor,
        evaluation: RuntimePolicyEvaluation,
        requested_by: str | None = None,
    ) -> RuntimeArtifactApprovalOutcome:
        try:
            scope = self.build_scope(descriptor=descriptor, evaluation=evaluation)
            with self._session_scope_factory() as session:
                result = await PolicyApprovalService(
                    session=session,
                    event_bus=self._event_bus,
                ).request(
                    scope=scope,
                    reason_codes=evaluation.reason_codes,
                    requested_by=requested_by or "isolated-runtime",
                    request_note=(
                        "Runtime artifact export requires Runtime Policy approval."
                    ),
                    metadata={
                        "runtime_run_id": descriptor.run_id,
                        "runtime_session_id": descriptor.session_id,
                        "runtime_profile": descriptor.profile,
                        "bundle_sha256": descriptor.bundle_sha256,
                        "manifest_fingerprint": descriptor.manifest_fingerprint,
                    },
                )
                record = result.record
                metadata = {
                    "approval_id": record.id,
                    "status": record.status.value,
                    "created": result.created,
                    "run_id": descriptor.run_id,
                    "scope_fingerprint": record.scope_fingerprint,
                    "bundle_sha256": descriptor.bundle_sha256,
                    "manifest_fingerprint": descriptor.manifest_fingerprint,
                    "expires_at": record.expires_at.isoformat(),
                }
        except PolicyApprovalWorkspaceError:
            return self.failure(
                code="POLICY_APPROVAL_WORKSPACE_INVALID",
                message="The artifact approval Workspace is unavailable.",
                metadata={"status": "workspace_invalid", "run_id": descriptor.run_id},
            )
        except (PolicyApprovalServiceError, PolicyApprovalScopeError, ValueError):
            return self.failure(
                code="POLICY_APPROVAL_RESOLUTION_FAILED",
                message="The artifact approval request could not be persisted.",
                metadata={"status": "resolution_failed", "run_id": descriptor.run_id},
            )

        return self.failure(
            code="POLICY_APPROVAL_REQUIRED",
            message=(
                "Human approval is required for this exact runtime artifact bundle."
            ),
            metadata=metadata,
        )

    async def consume(
        self,
        *,
        descriptor: RuntimeArtifactDescriptor,
        evaluation: RuntimePolicyEvaluation,
        approval_id: str,
        token: str,
    ) -> RuntimeArtifactApprovalOutcome:
        approval_id = approval_id.strip()
        if not approval_id or not token:
            return self.failure(
                code="POLICY_APPROVAL_CREDENTIALS_INCOMPLETE",
                message="Both policy approval id and token are required.",
                metadata={"status": "credentials_incomplete", "run_id": descriptor.run_id},
            )

        try:
            scope = self.build_scope(descriptor=descriptor, evaluation=evaluation)
            with self._session_scope_factory() as session:
                record = await PolicyApprovalService(
                    session=session,
                    event_bus=self._event_bus,
                ).consume(
                    approval_id=approval_id,
                    workspace_id=descriptor.workspace_id,
                    token=token,
                    scope=scope,
                )
                metadata = {
                    "approval_id": record.id,
                    "status": record.status.value,
                    "run_id": descriptor.run_id,
                    "scope_fingerprint": record.scope_fingerprint,
                    "bundle_sha256": descriptor.bundle_sha256,
                    "manifest_fingerprint": descriptor.manifest_fingerprint,
                    "consumed_at": (
                        record.consumed_at.isoformat()
                        if record.consumed_at is not None
                        else None
                    ),
                }
        except PolicyApprovalNotFoundError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_NOT_FOUND", "not_found",
                "The artifact approval was not found in this Workspace.",
            )
        except PolicyApprovalWorkspaceError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_WORKSPACE_INVALID",
                "workspace_invalid", "The artifact approval Workspace is unavailable.",
            )
        except PolicyApprovalExpiredError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_EXPIRED", "expired",
                "The artifact approval has expired.",
            )
        except PolicyApprovalScopeError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_SCOPE_MISMATCH",
                "scope_mismatch",
                "The approval does not match the exact runtime artifact bundle.",
            )
        except PolicyApprovalTokenError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_TOKEN_INVALID",
                "token_invalid", "The artifact approval token is invalid.",
            )
        except PolicyApprovalStateError:
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_INVALID_STATE",
                "invalid_state",
                "The artifact approval is not available for one-time consumption.",
            )
        except (PolicyApprovalServiceError, ValueError):
            return self._consume_failure(
                descriptor, approval_id, "POLICY_APPROVAL_CONSUME_FAILED",
                "consume_failed", "The artifact approval could not be consumed.",
            )

        return RuntimeArtifactApprovalOutcome(
            authorized=True,
            error_code=None,
            message=None,
            metadata=metadata,
        )

    @staticmethod
    def build_scope(
        *,
        descriptor: RuntimeArtifactDescriptor,
        evaluation: RuntimePolicyEvaluation,
    ) -> PolicyApprovalScope:
        if evaluation.operation != PolicyOperation.ARTIFACT_EXPORT:
            raise PolicyApprovalScopeError(
                "Artifact approval requires artifact_export policy evaluation."
            )
        if evaluation.action != PolicyAction.REQUIRE_APPROVAL:
            raise PolicyApprovalScopeError(
                "Artifact approval requires a require_approval decision."
            )
        if not evaluation.fingerprint:
            raise PolicyApprovalScopeError("Artifact policy fingerprint is required.")
        return PolicyApprovalScope(
            workspace_id=descriptor.workspace_id,
            operation=PolicyOperation.ARTIFACT_EXPORT,
            policy_version=evaluation.policy_version,
            policy_fingerprint=evaluation.fingerprint,
            subject_type="runtime_artifact",
            subject_id=descriptor.run_id,
            subject_payload=descriptor.subject_payload(),
        )

    @classmethod
    def _consume_failure(
        cls,
        descriptor: RuntimeArtifactDescriptor,
        approval_id: str,
        code: str,
        status: str,
        message: str,
    ) -> RuntimeArtifactApprovalOutcome:
        return cls.failure(
            code=code,
            message=message,
            metadata={
                "approval_id": approval_id,
                "status": status,
                "run_id": descriptor.run_id,
            },
        )

    @staticmethod
    def failure(
        *,
        code: str,
        message: str,
        metadata: dict[str, Any],
    ) -> RuntimeArtifactApprovalOutcome:
        return RuntimeArtifactApprovalOutcome(
            authorized=False,
            error_code=code,
            message=message,
            metadata=metadata,
        )
