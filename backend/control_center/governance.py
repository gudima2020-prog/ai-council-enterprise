from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.control_center.governance_schemas import (
    HumanControlApprovalCaseCancelRequest,
    HumanControlApprovalCaseRetryRequest,
    HumanControlApprovalPolicyCreate,
    HumanControlApprovalPolicyUpdate,
    HumanControlApprovalVoteDecision,
    HumanControlApprovalVoteRequest,
    HumanControlBootstrapOwnerRequest,
    HumanControlEscalationAcknowledgeRequest,
    HumanControlEscalationResolveRequest,
    HumanControlManualEscalationRequest,
    HumanControlPermission,
    HumanControlRoleBindingCreate,
    HumanControlRoleBindingRevoke,
    HumanControlRoleCreate,
    HumanControlRoleUpdate,
)
from backend.control_center.models import (
    HumanControlApprovalCaseModel,
    HumanControlApprovalPolicyModel,
    HumanControlApprovalStepModel,
    HumanControlApprovalVoteModel,
    HumanControlEscalationModel,
    HumanControlItemModel,
    HumanControlRoleBindingModel,
    HumanControlRoleModel,
)
from backend.control_center.schemas import (
    HumanControlBulkDecisionRequest,
    HumanControlDecisionAction,
    HumanControlDecisionRequest,
)
from backend.control_center.service import (
    HumanControlCenterService,
    HumanControlConflict,
    HumanControlError,
    HumanControlNotFound,
    ensure_utc,
    iso,
    utc_now,
)
from backend.core.events import Event, EventBus
from backend.database.session import session_scope


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


BUILTIN_ROLES: tuple[dict[str, Any], ...] = (
    {
        "role_key": "owner",
        "name": "Human Control Owner",
        "description": "Полный административный и операторский доступ.",
        "permissions": ["*"],
    },
    {
        "role_key": "control_admin",
        "name": "Control Center Administrator",
        "description": "Управление ролями, политиками и операторской очередью.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.SNOOZE.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.DECIDE_HIGH.value,
            HumanControlPermission.DECIDE_CRITICAL.value,
            HumanControlPermission.ACCEPT_RISK.value,
            HumanControlPermission.APPROVE_BUDGET.value,
            HumanControlPermission.APPLY_LEARNING.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.APPROVAL_VOTE.value,
            HumanControlPermission.ESCALATE.value,
            HumanControlPermission.OVERRIDE.value,
            HumanControlPermission.MANAGE_ROLES.value,
            HumanControlPermission.MANAGE_POLICIES.value,
            HumanControlPermission.AUDIT.value,
            HumanControlPermission.AUTH_VIEW.value,
            HumanControlPermission.AUTH_MANAGE.value,
            HumanControlPermission.AUTH_TOKEN_MANAGE.value,
            HumanControlPermission.AUTH_BREAK_GLASS.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.NOTIFICATION_MANAGE.value,
            HumanControlPermission.COMPLIANCE_VIEW.value,
            HumanControlPermission.COMPLIANCE_MANAGE.value,
            HumanControlPermission.ACCESS_REVIEW.value,
            HumanControlPermission.RETENTION_VIEW.value,
            HumanControlPermission.RETENTION_MANAGE.value,
            HumanControlPermission.LEGAL_HOLD_MANAGE.value,
            HumanControlPermission.EVIDENCE_EXPORT.value,
            HumanControlPermission.DOCUMENT_VIEW.value,
            HumanControlPermission.DOCUMENT_UPLOAD.value,
            HumanControlPermission.DOCUMENT_EXTRACT.value,
            HumanControlPermission.DOCUMENT_OCR.value,
            HumanControlPermission.DOCUMENT_AI.value,
            HumanControlPermission.DOCUMENT_DELETE.value,
            HumanControlPermission.DOCUMENT_AUDIT.value,
            HumanControlPermission.SECRET_VIEW.value,
            HumanControlPermission.SECRET_MANAGE.value,
            HumanControlPermission.SECRET_ROTATE.value,
            HumanControlPermission.SECRET_AUDIT.value,
        ],
    },
    {
        "role_key": "operator",
        "name": "Operator",
        "description": "Обработка решений низкого и среднего риска.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.SNOOZE.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.DOCUMENT_VIEW.value,
            HumanControlPermission.DOCUMENT_UPLOAD.value,
            HumanControlPermission.DOCUMENT_EXTRACT.value,
            HumanControlPermission.DOCUMENT_OCR.value,
            HumanControlPermission.DOCUMENT_AI.value,
        ],
        "allowed_risk_levels": ["low", "medium"],
    },
    {
        "role_key": "senior_operator",
        "name": "Senior Operator",
        "description": "Решения до высокого риска и участие в согласованиях.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.SNOOZE.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.DECIDE_HIGH.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.APPROVAL_VOTE.value,
            HumanControlPermission.ESCALATE.value,
            HumanControlPermission.DOCUMENT_VIEW.value,
            HumanControlPermission.DOCUMENT_UPLOAD.value,
            HumanControlPermission.DOCUMENT_EXTRACT.value,
            HumanControlPermission.DOCUMENT_OCR.value,
            HumanControlPermission.DOCUMENT_AI.value,
            HumanControlPermission.DOCUMENT_DELETE.value,
        ],
        "allowed_risk_levels": ["low", "medium", "high"],
    },
    {
        "role_key": "risk_officer",
        "name": "Risk Officer",
        "description": "Критические решения и принятие риска.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.DECIDE_HIGH.value,
            HumanControlPermission.DECIDE_CRITICAL.value,
            HumanControlPermission.ACCEPT_RISK.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.APPROVAL_VOTE.value,
            HumanControlPermission.ESCALATE.value,
        ],
    },
    {
        "role_key": "budget_officer",
        "name": "Budget Officer",
        "description": "Согласование Mission и Workspace ресурсов.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.DECIDE_HIGH.value,
            HumanControlPermission.APPROVE_BUDGET.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.APPROVAL_VOTE.value,
        ],
        "allowed_source_types": [
            "mission_resource_allocation",
            "workspace_resource_reservation",
        ],
    },
    {
        "role_key": "learning_officer",
        "name": "Learning Officer",
        "description": "Проверка и применение Mission Learning калибровок.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.NOTIFICATION_ACK.value,
            HumanControlPermission.CLAIM.value,
            HumanControlPermission.DECIDE.value,
            HumanControlPermission.DECIDE_HIGH.value,
            HumanControlPermission.APPLY_LEARNING.value,
            HumanControlPermission.APPROVAL_INITIATE.value,
            HumanControlPermission.APPROVAL_VOTE.value,
        ],
        "allowed_source_types": ["mission_learning_run"],
    },
    {
        "role_key": "auditor",
        "name": "Auditor",
        "description": "Только чтение и аудит операторских решений.",
        "permissions": [
            HumanControlPermission.VIEW.value,
            HumanControlPermission.NOTIFICATION_VIEW.value,
            HumanControlPermission.AUDIT.value,
            HumanControlPermission.AUTH_VIEW.value,
            HumanControlPermission.COMPLIANCE_VIEW.value,
            HumanControlPermission.ACCESS_REVIEW.value,
            HumanControlPermission.RETENTION_VIEW.value,
            HumanControlPermission.EVIDENCE_EXPORT.value,
            HumanControlPermission.DOCUMENT_VIEW.value,
            HumanControlPermission.DOCUMENT_AUDIT.value,
            HumanControlPermission.SECRET_VIEW.value,
            HumanControlPermission.SECRET_AUDIT.value,
        ],
    },
)


class HumanControlGovernanceService:
    """RBAC, separation of duties, approval chains and escalation handling.

    The service is deliberately backward compatible. A Workspace remains in
    legacy-permissive mode until it has an active role binding or approval
    policy. The first owner is created through the one-time bootstrap method.
    """

    TERMINAL_CASE_STATUSES = {
        "executed",
        "rejected",
        "expired",
        "cancelled",
    }

    def __init__(
        self,
        *,
        event_bus: EventBus,
        control_center: HumanControlCenterService,
        session_factory: SessionContextFactory = session_scope,
        escalation_scan_interval_seconds: int = 30,
    ) -> None:
        self._event_bus = event_bus
        self._control_center = control_center
        self._session_factory = session_factory
        self._escalation_scan_interval_seconds = max(5, int(escalation_scan_interval_seconds))
        self._monitor_task: asyncio.Task[None] | None = None
        self._cases_created = 0
        self._votes_recorded = 0
        self._escalations_created = 0

    @property
    def running(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(),
            name="human-control-governance-monitor",
        )

    async def shutdown(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await self.scan_escalations(actor_id="governance_monitor")
            except asyncio.CancelledError:
                raise
            except Exception:
                # Event-driven reconciliation and the explicit scan endpoint remain
                # available even when one monitor iteration fails.
                pass
            await asyncio.sleep(self._escalation_scan_interval_seconds)

    def seed_builtin_roles(self) -> int:
        created = 0
        with self._session_factory() as session:
            for definition in BUILTIN_ROLES:
                scope_key = f"global:{definition['role_key']}"
                row = session.scalar(
                    select(HumanControlRoleModel).where(
                        HumanControlRoleModel.scope_key == scope_key
                    )
                )
                if row is None:
                    row = HumanControlRoleModel(
                        scope_key=scope_key,
                        workspace_id=None,
                        role_key=definition["role_key"],
                        name=definition["name"],
                        description=definition["description"],
                        enabled=True,
                        builtin=True,
                        permissions_json=list(definition.get("permissions", [])),
                        allowed_risk_levels_json=list(
                            definition.get("allowed_risk_levels", [])
                        ),
                        allowed_source_types_json=list(
                            definition.get("allowed_source_types", [])
                        ),
                        allowed_actions_json=list(
                            definition.get("allowed_actions", [])
                        ),
                        metadata_json={"seeded": True},
                        created_by="system",
                    )
                    session.add(row)
                    created += 1
                else:
                    row.name = definition["name"]
                    row.description = definition["description"]
                    row.permissions_json = list(definition.get("permissions", []))
                    row.allowed_risk_levels_json = list(
                        definition.get("allowed_risk_levels", [])
                    )
                    row.allowed_source_types_json = list(
                        definition.get("allowed_source_types", [])
                    )
                    row.allowed_actions_json = list(
                        definition.get("allowed_actions", [])
                    )
                    row.builtin = True
        return created

    def status(self) -> dict[str, Any]:
        with self._session_factory() as session:
            def count(model: Any, *criteria: Any) -> int:
                statement = select(func.count()).select_from(model)
                if criteria:
                    statement = statement.where(*criteria)
                return int(session.scalar(statement) or 0)

            result = {
                "running": self.running,
                "roles": count(HumanControlRoleModel),
                "active_bindings": count(
                    HumanControlRoleBindingModel,
                    HumanControlRoleBindingModel.enabled.is_(True),
                ),
                "enabled_policies": count(
                    HumanControlApprovalPolicyModel,
                    HumanControlApprovalPolicyModel.enabled.is_(True),
                ),
                "pending_cases": count(
                    HumanControlApprovalCaseModel,
                    HumanControlApprovalCaseModel.status.in_(
                        ["pending", "approved", "executing", "execution_failed"]
                    ),
                ),
                "open_escalations": count(
                    HumanControlEscalationModel,
                    HumanControlEscalationModel.status.in_(
                        ["open", "acknowledged"]
                    ),
                ),
            }
        result.update(
            {
                "cases_created": self._cases_created,
                "escalation_scan_interval_seconds": (
                    self._escalation_scan_interval_seconds
                ),
                "votes_recorded": self._votes_recorded,
                "escalations_created": self._escalations_created,
                "legacy_mode": (
                    "Workspace remains permissive until a binding or policy exists."
                ),
                "capabilities": [
                    "operator_rbac",
                    "workspace_and_global_role_bindings",
                    "risk_source_and_action_restrictions",
                    "multi_step_approval_chains",
                    "separation_of_duties",
                    "distinct_approvers",
                    "timeout_and_manual_escalation",
                    "idempotent_votes_and_case_execution",
                ],
            }
        )
        return result

    async def bootstrap_owner(
        self,
        request: HumanControlBootstrapOwnerRequest,
    ) -> dict[str, Any]:
        self.seed_builtin_roles()
        now = utc_now()
        with self._session_factory() as session:
            if self._has_effective_binding(session, request.workspace_id, now):
                raise HumanControlConflict(
                    "Owner bootstrap недоступен: для области уже существуют "
                    "активные назначения ролей."
                )
            role = session.scalar(
                select(HumanControlRoleModel).where(
                    HumanControlRoleModel.scope_key == "global:owner"
                )
            )
            if role is None:
                raise HumanControlError("Builtin owner role не создан.")
            binding = HumanControlRoleBindingModel(
                binding_key=self._binding_key(
                    request.workspace_id,
                    request.actor_id,
                    role.id,
                ),
                workspace_id=request.workspace_id,
                actor_id=request.actor_id,
                role_id=role.id,
                enabled=True,
                granted_by=request.actor_id,
                reason=request.reason,
                metadata_json=dict(request.metadata),
            )
            session.add(binding)
            session.flush()
            result = self._binding_to_dict(binding, role=role)
        await self._publish(
            "human_control.governance.owner_bootstrapped",
            workspace_id=request.workspace_id,
            correlation_id=result["id"],
            payload={"binding": result},
        )
        return result

    def list_roles(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlRoleModel)
            if workspace_id is not None:
                statement = statement.where(
                    or_(
                        HumanControlRoleModel.workspace_id == workspace_id,
                        HumanControlRoleModel.workspace_id.is_(None),
                    )
                )
            if enabled is not None:
                statement = statement.where(HumanControlRoleModel.enabled.is_(enabled))
            rows = session.scalars(
                statement.order_by(
                    HumanControlRoleModel.builtin.desc(),
                    HumanControlRoleModel.role_key.asc(),
                )
            ).all()
            return [self._role_to_dict(row) for row in rows]

    async def create_role(self, request: HumanControlRoleCreate) -> dict[str, Any]:
        self._assert_permission(
            request.actor_id,
            request.workspace_id,
            HumanControlPermission.MANAGE_ROLES.value,
        )
        scope_key = self._role_scope_key(request.workspace_id, request.role_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlRoleModel).where(
                    HumanControlRoleModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Role с таким key уже существует.")
            row = HumanControlRoleModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                role_key=request.role_key,
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                builtin=False,
                permissions_json=self._string_values(request.permissions),
                allowed_risk_levels_json=self._string_values(
                    request.allowed_risk_levels
                ),
                allowed_source_types_json=self._string_values(
                    request.allowed_source_types
                ),
                allowed_actions_json=self._string_values(request.allowed_actions),
                metadata_json=dict(request.metadata),
                created_by=request.actor_id,
            )
            session.add(row)
            session.flush()
            result = self._role_to_dict(row)
        await self._publish(
            "human_control.governance.role.created",
            workspace_id=request.workspace_id,
            correlation_id=result["id"],
            payload={"role": result, "actor_id": request.actor_id},
        )
        return result

    async def update_role(
        self,
        role_id: str,
        request: HumanControlRoleUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlRoleModel, role_id)
            if row is None:
                raise HumanControlNotFound("Human Control Role не найдена.")
            workspace_id = row.workspace_id
        self._assert_permission(
            request.actor_id,
            workspace_id,
            HumanControlPermission.MANAGE_ROLES.value,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlRoleModel, role_id)
            assert row is not None
            if request.name is not None:
                row.name = request.name
            if request.description is not None:
                row.description = request.description
            if request.enabled is not None:
                row.enabled = request.enabled
            if request.permissions is not None:
                if row.builtin:
                    raise HumanControlConflict(
                        "Permissions встроенной роли изменять нельзя."
                    )
                row.permissions_json = self._string_values(request.permissions)
            if request.allowed_risk_levels is not None:
                if row.builtin:
                    raise HumanControlConflict(
                        "Ограничения встроенной роли изменять нельзя."
                    )
                row.allowed_risk_levels_json = self._string_values(
                    request.allowed_risk_levels
                )
            if request.allowed_source_types is not None:
                if row.builtin:
                    raise HumanControlConflict(
                        "Ограничения встроенной роли изменять нельзя."
                    )
                row.allowed_source_types_json = self._string_values(
                    request.allowed_source_types
                )
            if request.allowed_actions is not None:
                if row.builtin:
                    raise HumanControlConflict(
                        "Ограничения встроенной роли изменять нельзя."
                    )
                row.allowed_actions_json = self._string_values(
                    request.allowed_actions
                )
            if request.metadata is not None:
                row.metadata_json = dict(request.metadata)
            result = self._role_to_dict(row)
        await self._publish(
            "human_control.governance.role.updated",
            workspace_id=workspace_id,
            correlation_id=role_id,
            payload={"role": result, "actor_id": request.actor_id},
        )
        return result

    def list_bindings(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlRoleBindingModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlRoleBindingModel.workspace_id == workspace_id
                )
            if actor_id is not None:
                statement = statement.where(
                    HumanControlRoleBindingModel.actor_id == actor_id
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlRoleBindingModel.enabled.is_(enabled)
                )
            rows = session.scalars(
                statement.order_by(HumanControlRoleBindingModel.created_at.desc())
            ).all()
            result = []
            for row in rows:
                result.append(
                    self._binding_to_dict(
                        row,
                        role=session.get(HumanControlRoleModel, row.role_id),
                    )
                )
            return result

    async def grant_binding(
        self,
        request: HumanControlRoleBindingCreate,
    ) -> dict[str, Any]:
        self._assert_permission(
            request.granted_by,
            request.workspace_id,
            HumanControlPermission.MANAGE_ROLES.value,
        )
        now = utc_now()
        with self._session_factory() as session:
            role = self._resolve_role(
                session,
                role_id=request.role_id,
                role_key=request.role_key,
                workspace_id=request.workspace_id,
            )
            if role.workspace_id not in {None, request.workspace_id}:
                raise HumanControlConflict(
                    "Workspace-specific role нельзя назначить в другой Workspace."
                )
            key = self._binding_key(request.workspace_id, request.actor_id, role.id)
            row = session.scalar(
                select(HumanControlRoleBindingModel).where(
                    HumanControlRoleBindingModel.binding_key == key
                )
            )
            if row is None:
                row = HumanControlRoleBindingModel(
                    binding_key=key,
                    workspace_id=request.workspace_id,
                    actor_id=request.actor_id,
                    role_id=role.id,
                    granted_by=request.granted_by,
                )
                session.add(row)
            row.enabled = True
            row.valid_from = ensure_utc(request.valid_from) or now
            row.expires_at = ensure_utc(request.expires_at)
            row.reason = request.reason
            row.metadata_json = dict(request.metadata)
            session.flush()
            result = self._binding_to_dict(row, role=role)
        await self._publish(
            "human_control.governance.binding.granted",
            workspace_id=request.workspace_id,
            correlation_id=result["id"],
            payload={"binding": result, "granted_by": request.granted_by},
        )
        return result

    async def revoke_binding(
        self,
        binding_id: str,
        request: HumanControlRoleBindingRevoke,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlRoleBindingModel, binding_id)
            if row is None:
                raise HumanControlNotFound("Role Binding не найден.")
            workspace_id = row.workspace_id
        self._assert_permission(
            request.actor_id,
            workspace_id,
            HumanControlPermission.MANAGE_ROLES.value,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlRoleBindingModel, binding_id)
            assert row is not None
            row.enabled = False
            metadata = dict(row.metadata_json or {})
            metadata.update(
                {
                    "revoked_by": request.actor_id,
                    "revoked_at": iso(utc_now()),
                    "revocation_reason": request.reason,
                }
            )
            row.metadata_json = metadata
            role = session.get(HumanControlRoleModel, row.role_id)
            result = self._binding_to_dict(row, role=role)
        await self._publish(
            "human_control.governance.binding.revoked",
            workspace_id=workspace_id,
            correlation_id=binding_id,
            payload={"binding": result, "actor_id": request.actor_id},
        )
        return result

    def effective_access(
        self,
        *,
        actor_id: str,
        workspace_id: str | None,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            roles = self._effective_roles(session, actor_id, workspace_id)
            return {
                "actor_id": actor_id,
                "workspace_id": workspace_id,
                "governed": self._is_governed(session, workspace_id),
                "role_keys": sorted({role.role_key for role in roles}),
                "permissions": sorted(
                    {
                        permission
                        for role in roles
                        for permission in list(role.permissions_json or [])
                    }
                ),
                "roles": [self._role_to_dict(role) for role in roles],
            }

    async def create_policy(
        self,
        request: HumanControlApprovalPolicyCreate,
    ) -> dict[str, Any]:
        self._assert_permission(
            request.actor_id,
            request.workspace_id,
            HumanControlPermission.MANAGE_POLICIES.value,
        )
        scope_key = self._policy_scope_key(request.workspace_id, request.policy_key)
        with self._session_factory() as session:
            if session.scalar(
                select(HumanControlApprovalPolicyModel).where(
                    HumanControlApprovalPolicyModel.scope_key == scope_key
                )
            ):
                raise HumanControlConflict("Approval Policy с таким key уже существует.")
            row = HumanControlApprovalPolicyModel(
                scope_key=scope_key,
                workspace_id=request.workspace_id,
                policy_key=request.policy_key,
                name=request.name,
                description=request.description,
                enabled=request.enabled,
                priority=request.priority,
                source_types_json=self._string_values(request.source_types),
                action_kinds_json=list(request.action_kinds),
                risk_levels_json=self._string_values(request.risk_levels),
                decision_actions_json=self._string_values(request.decision_actions),
                steps_json=[step.model_dump(mode="json") for step in request.steps],
                distinct_approvers=request.distinct_approvers,
                prohibit_source_requester_approval=(
                    request.prohibit_source_requester_approval
                ),
                prohibit_case_initiator_approval=(
                    request.prohibit_case_initiator_approval
                ),
                rejection_mode=request.rejection_mode,
                case_ttl_seconds=request.case_ttl_seconds,
                default_step_ttl_seconds=request.default_step_ttl_seconds,
                escalation_after_seconds=request.escalation_after_seconds,
                metadata_json=dict(request.metadata),
                created_by=request.actor_id,
            )
            session.add(row)
            session.flush()
            result = self._policy_to_dict(row)
        await self._publish(
            "human_control.governance.policy.created",
            workspace_id=request.workspace_id,
            correlation_id=result["id"],
            payload={"policy": result, "actor_id": request.actor_id},
        )
        return result

    async def update_policy(
        self,
        policy_id: str,
        request: HumanControlApprovalPolicyUpdate,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlApprovalPolicyModel, policy_id)
            if row is None:
                raise HumanControlNotFound("Approval Policy не найдена.")
            workspace_id = row.workspace_id
        self._assert_permission(
            request.actor_id,
            workspace_id,
            HumanControlPermission.MANAGE_POLICIES.value,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlApprovalPolicyModel, policy_id)
            assert row is not None
            scalar_fields = (
                "name",
                "description",
                "enabled",
                "priority",
                "distinct_approvers",
                "prohibit_source_requester_approval",
                "prohibit_case_initiator_approval",
                "rejection_mode",
                "case_ttl_seconds",
                "default_step_ttl_seconds",
                "escalation_after_seconds",
            )
            for field in scalar_fields:
                value = getattr(request, field)
                if value is not None:
                    setattr(row, field, value)
            if request.source_types is not None:
                row.source_types_json = self._string_values(request.source_types)
            if request.action_kinds is not None:
                row.action_kinds_json = list(request.action_kinds)
            if request.risk_levels is not None:
                row.risk_levels_json = self._string_values(request.risk_levels)
            if request.decision_actions is not None:
                row.decision_actions_json = self._string_values(
                    request.decision_actions
                )
            if request.steps is not None:
                row.steps_json = [
                    step.model_dump(mode="json") for step in request.steps
                ]
            if request.metadata is not None:
                row.metadata_json = dict(request.metadata)
            result = self._policy_to_dict(row)
        await self._publish(
            "human_control.governance.policy.updated",
            workspace_id=workspace_id,
            correlation_id=policy_id,
            payload={"policy": result, "actor_id": request.actor_id},
        )
        return result

    def list_policies(
        self,
        *,
        workspace_id: str | None = None,
        enabled: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlApprovalPolicyModel)
            if workspace_id is not None:
                statement = statement.where(
                    or_(
                        HumanControlApprovalPolicyModel.workspace_id == workspace_id,
                        HumanControlApprovalPolicyModel.workspace_id.is_(None),
                    )
                )
            if enabled is not None:
                statement = statement.where(
                    HumanControlApprovalPolicyModel.enabled.is_(enabled)
                )
            rows = session.scalars(
                statement.order_by(
                    HumanControlApprovalPolicyModel.priority.desc(),
                    HumanControlApprovalPolicyModel.created_at.asc(),
                )
            ).all()
            return [self._policy_to_dict(row) for row in rows]

    def match_policy(
        self,
        *,
        item_id: str,
        action: HumanControlDecisionAction,
    ) -> dict[str, Any] | None:
        item = self._control_center.get_item(item_id)
        if item is None:
            raise HumanControlNotFound("Human Control Item не найден.")
        with self._session_factory() as session:
            row = self._match_policy_row(session, item, action.value)
            return None if row is None else self._policy_to_dict(row)

    async def authorize_operator_action(
        self,
        *,
        item_id: str,
        actor_id: str,
        permission: str,
        decision_action: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        item = self._control_center.get_item(item_id)
        if item is None:
            raise HumanControlNotFound("Human Control Item не найден.")
        with self._session_factory() as session:
            if not self._is_governed(session, item.get("workspace_id")):
                return {
                    "allowed": True,
                    "mode": "legacy",
                    "actor_id": actor_id,
                }
            required = {permission}
            if force:
                required.add(HumanControlPermission.OVERRIDE.value)
            roles = self._eligible_roles(
                session,
                actor_id=actor_id,
                workspace_id=item.get("workspace_id"),
                item=item,
                action=decision_action,
                required_permissions=required,
            )
            if not roles:
                raise HumanControlConflict(
                    f"Оператор {actor_id} не имеет полномочия {permission} "
                    "для этого решения."
                )
            return {
                "allowed": True,
                "mode": "governed",
                "actor_id": actor_id,
                "role_keys": sorted({role.role_key for role in roles}),
                "permissions": sorted(required),
            }

    async def submit_decision(
        self,
        item_id: str,
        request: HumanControlDecisionRequest,
    ) -> dict[str, Any]:
        item = self._control_center.get_item(item_id)
        if item is None:
            raise HumanControlNotFound("Human Control Item не найден.")
        if request.action.value not in list(item.get("decision_options") or []):
            raise HumanControlConflict(
                f"Действие {request.action.value} недоступно для Item."
            )

        with self._session_factory() as session:
            policy = self._match_policy_row(session, item, request.action.value)

        if policy is None:
            await self._authorize_decision(item, request)
            result = await self._control_center.decide(item_id, request)
            return {"mode": "direct", **result}

        await self.authorize_operator_action(
            item_id=item_id,
            actor_id=request.actor_id,
            permission=HumanControlPermission.APPROVAL_INITIATE.value,
            decision_action=request.action.value,
            force=request.force,
        )

        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlApprovalCaseModel).where(
                    HumanControlApprovalCaseModel.request_idempotency_key
                    == request.idempotency_key
                )
            )
            if existing is not None:
                return {
                    "mode": "approval_chain",
                    "case": self._case_to_dict(session, existing),
                    "idempotent_replay": True,
                }

            now = utc_now()
            source_requester = self._source_requester(item)
            case = HumanControlApprovalCaseModel(
                item_id=item_id,
                policy_id=policy.id,
                workspace_id=item.get("workspace_id"),
                requested_action=request.action.value,
                requested_by=request.actor_id,
                source_requester=source_requester,
                request_idempotency_key=request.idempotency_key,
                status="pending",
                current_step_sequence=1,
                total_steps=len(list(policy.steps_json or [])),
                distinct_approvers=policy.distinct_approvers,
                prohibit_source_requester_approval=(
                    policy.prohibit_source_requester_approval
                ),
                prohibit_case_initiator_approval=(
                    policy.prohibit_case_initiator_approval
                ),
                request_json=request.model_dump(mode="json"),
                due_at=now + timedelta(seconds=policy.case_ttl_seconds),
            )
            session.add(case)
            session.flush()
            for sequence, spec in enumerate(list(policy.steps_json or []), start=1):
                ttl = int(
                    spec.get("ttl_seconds")
                    or policy.default_step_ttl_seconds
                    or policy.escalation_after_seconds
                )
                session.add(
                    HumanControlApprovalStepModel(
                        case_id=case.id,
                        step_key=str(spec.get("step_key") or f"step-{sequence}"),
                        sequence=sequence,
                        title=str(spec.get("title") or f"Approval step {sequence}"),
                        status="pending",
                        required_role_keys_json=list(
                            spec.get("required_role_keys") or []
                        ),
                        escalation_role_keys_json=list(
                            spec.get("escalation_role_keys") or []
                        ),
                        min_approvals=max(1, int(spec.get("min_approvals") or 1)),
                        due_at=now + timedelta(seconds=ttl),
                        metadata_json=dict(spec.get("metadata") or {}),
                    )
                )
            session.flush()
            case_id = case.id
            case_result = self._case_to_dict(session, case)
        self._cases_created += 1
        await self._publish(
            "human_control.governance.case.created",
            workspace_id=item.get("workspace_id"),
            correlation_id=case_id,
            payload={
                "case": case_result,
                "policy_id": policy.id,
                "item_id": item_id,
            },
        )

        initial_vote: dict[str, Any] | None = None
        initial_vote_error: str | None = None
        if not policy.prohibit_case_initiator_approval:
            try:
                initial_vote = await self.cast_vote(
                    case_id,
                    HumanControlApprovalVoteRequest(
                        actor_id=request.actor_id,
                        decision=HumanControlApprovalVoteDecision.APPROVE,
                        reason=request.reason,
                        idempotency_key=f"{request.idempotency_key}:initial-vote",
                        force=request.force,
                        metadata={"automatic_initial_vote": True},
                    ),
                )
            except HumanControlConflict as exc:
                initial_vote_error = str(exc)

        result_case = self.get_case(case_id)
        return {
            "mode": "approval_chain",
            "case": result_case,
            "initial_vote": initial_vote,
            "initial_vote_error": initial_vote_error,
            "idempotent_replay": False,
        }

    async def bulk_submit_decisions(
        self,
        request: HumanControlBulkDecisionRequest,
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        failed = 0
        for entry in request.decisions:
            try:
                result = await self.submit_decision(entry.item_id, entry.decision)
                results.append({"item_id": entry.item_id, "ok": True, "result": result})
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "item_id": entry.item_id,
                        "ok": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                if request.stop_on_error:
                    break
        return {
            "requested": len(request.decisions),
            "processed": len(results),
            "succeeded": len(results) - failed,
            "failed": failed,
            "stopped_early": len(results) < len(request.decisions),
            "results": results,
        }

    async def cast_vote(
        self,
        case_id: str,
        request: HumanControlApprovalVoteRequest,
    ) -> dict[str, Any]:
        execute_case = False
        workspace_id: str | None = None
        with self._session_factory() as session:
            existing = session.scalar(
                select(HumanControlApprovalVoteModel).where(
                    HumanControlApprovalVoteModel.idempotency_key
                    == request.idempotency_key
                )
            )
            if existing is not None:
                case = session.get(HumanControlApprovalCaseModel, existing.case_id)
                return {
                    "case": self._case_to_dict(session, case) if case else None,
                    "vote": self._vote_to_dict(existing),
                    "idempotent_replay": True,
                }

            case = session.get(HumanControlApprovalCaseModel, case_id)
            if case is None:
                raise HumanControlNotFound("Approval Case не найден.")
            if case.status != "pending":
                raise HumanControlConflict(
                    f"Approval Case имеет статус {case.status}; голосование закрыто."
                )
            now = utc_now()
            if ensure_utc(case.due_at) is not None and ensure_utc(case.due_at) <= now:
                case.status = "expired"
                case.resolution_reason = "Approval Case expired before vote."
                raise HumanControlConflict("Срок Approval Case истёк.")
            step = session.scalar(
                select(HumanControlApprovalStepModel).where(
                    HumanControlApprovalStepModel.case_id == case.id,
                    HumanControlApprovalStepModel.sequence
                    == case.current_step_sequence,
                )
            )
            if step is None or step.status != "pending":
                raise HumanControlConflict("Активный Approval Step не найден.")
            item = session.get(HumanControlItemModel, case.item_id)
            if item is None:
                raise HumanControlNotFound("Human Control Item не найден.")
            workspace_id = case.workspace_id

            roles = self._effective_roles(session, request.actor_id, workspace_id)
            eligible = self._filter_roles(
                roles,
                item=self._item_model_to_dict(item),
                action=case.requested_action,
                required_permissions={
                    HumanControlPermission.APPROVAL_VOTE.value,
                },
            )
            required_role_keys = set(step.required_role_keys_json or [])
            if step.escalated:
                required_role_keys.update(step.escalation_role_keys_json or [])
            if required_role_keys:
                eligible = [
                    role for role in eligible if role.role_key in required_role_keys
                ]
            if request.force:
                override_roles = self._filter_roles(
                    roles,
                    item=self._item_model_to_dict(item),
                    action=case.requested_action,
                    required_permissions={HumanControlPermission.OVERRIDE.value},
                )
                if override_roles:
                    eligible = override_roles
            if not eligible:
                raise HumanControlConflict(
                    "Оператор не соответствует required_role_keys активного шага."
                )

            if not request.force:
                if (
                    case.prohibit_source_requester_approval
                    and case.source_requester
                    and case.source_requester == request.actor_id
                ):
                    raise HumanControlConflict(
                        "Разделение полномочий запрещает requester подтверждать "
                        "собственное решение."
                    )
                if (
                    case.prohibit_case_initiator_approval
                    and case.requested_by == request.actor_id
                ):
                    raise HumanControlConflict(
                        "Инициатор Approval Case не может участвовать в согласовании."
                    )
                if case.distinct_approvers:
                    prior_vote = session.scalar(
                        select(HumanControlApprovalVoteModel).where(
                            HumanControlApprovalVoteModel.case_id == case.id,
                            HumanControlApprovalVoteModel.actor_id == request.actor_id,
                            HumanControlApprovalVoteModel.decision.in_(
                                ["approve", "reject"]
                            ),
                        )
                    )
                    if prior_vote is not None:
                        raise HumanControlConflict(
                            "Для цепочки требуются разные согласующие."
                        )

            vote = HumanControlApprovalVoteModel(
                case_id=case.id,
                step_id=step.id,
                actor_id=request.actor_id,
                decision=request.decision.value,
                reason=request.reason,
                role_keys_json=sorted({role.role_key for role in eligible}),
                idempotency_key=request.idempotency_key,
                metadata_json={
                    **dict(request.metadata),
                    "force": request.force,
                },
            )
            session.add(vote)
            session.flush()

            policy = (
                session.get(HumanControlApprovalPolicyModel, case.policy_id)
                if case.policy_id
                else None
            )
            rejection_mode = policy.rejection_mode if policy else "any"
            if request.decision == HumanControlApprovalVoteDecision.APPROVE:
                step.approvals_received += 1
                if step.approvals_received >= step.min_approvals:
                    step.status = "approved"
                    step.completed_at = now
                    next_step = session.scalar(
                        select(HumanControlApprovalStepModel).where(
                            HumanControlApprovalStepModel.case_id == case.id,
                            HumanControlApprovalStepModel.sequence
                            == case.current_step_sequence + 1,
                        )
                    )
                    if next_step is None:
                        case.status = "approved"
                        case.approved_at = now
                        case.resolved_by = request.actor_id
                        case.resolution_reason = "All approval steps completed."
                        execute_case = True
                    else:
                        case.current_step_sequence = next_step.sequence
            elif request.decision == HumanControlApprovalVoteDecision.REJECT:
                step.rejections_received += 1
                reject_threshold = 1
                if rejection_mode == "majority":
                    reject_threshold = max(1, step.min_approvals)
                if step.rejections_received >= reject_threshold:
                    step.status = "rejected"
                    step.completed_at = now
                    case.status = "rejected"
                    case.rejected_at = now
                    case.resolved_by = request.actor_id
                    case.resolution_reason = request.reason
            session.flush()
            vote_result = self._vote_to_dict(vote)
            case_result = self._case_to_dict(session, case)

        self._votes_recorded += 1
        await self._publish(
            "human_control.governance.vote.recorded",
            workspace_id=workspace_id,
            correlation_id=case_id,
            payload={"case_id": case_id, "vote": vote_result},
        )
        if execute_case:
            case_result = await self._execute_case(case_id)
        return {
            "case": case_result,
            "vote": vote_result,
            "idempotent_replay": False,
        }

    async def retry_case_execution(
        self,
        case_id: str,
        request: HumanControlApprovalCaseRetryRequest,
    ) -> dict[str, Any]:
        case = self.get_case(case_id)
        if case is None:
            raise HumanControlNotFound("Approval Case не найден.")
        await self.authorize_operator_action(
            item_id=case["item_id"],
            actor_id=request.actor_id,
            permission=HumanControlPermission.OVERRIDE.value,
            decision_action=case["requested_action"],
            force=request.force,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlApprovalCaseModel, case_id)
            assert row is not None
            if row.status not in {"approved", "execution_failed"}:
                raise HumanControlConflict(
                    "Повторное выполнение разрешено только для approved или "
                    "execution_failed Case."
                )
            row.status = "approved"
            row.resolution_reason = request.reason
        return await self._execute_case(case_id)

    async def cancel_case(
        self,
        case_id: str,
        request: HumanControlApprovalCaseCancelRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            case = session.get(HumanControlApprovalCaseModel, case_id)
            if case is None:
                raise HumanControlNotFound("Approval Case не найден.")
            if case.status in self.TERMINAL_CASE_STATUSES:
                return self._case_to_dict(session, case)
            item_id = case.item_id
            workspace_id = case.workspace_id
            requested_by = case.requested_by
        if request.actor_id != requested_by:
            await self.authorize_operator_action(
                item_id=item_id,
                actor_id=request.actor_id,
                permission=HumanControlPermission.OVERRIDE.value,
                force=request.force,
            )
        with self._session_factory() as session:
            case = session.get(HumanControlApprovalCaseModel, case_id)
            assert case is not None
            case.status = "cancelled"
            case.resolved_by = request.actor_id
            case.resolution_reason = request.reason
            result = self._case_to_dict(session, case)
        await self._publish(
            "human_control.governance.case.cancelled",
            workspace_id=workspace_id,
            correlation_id=case_id,
            payload={"case": result},
        )
        return result

    def list_cases(
        self,
        *,
        workspace_id: str | None = None,
        item_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlApprovalCaseModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlApprovalCaseModel.workspace_id == workspace_id
                )
            if item_id is not None:
                statement = statement.where(
                    HumanControlApprovalCaseModel.item_id == item_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlApprovalCaseModel.status == status
                )
            rows = session.scalars(
                statement.order_by(HumanControlApprovalCaseModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._case_to_dict(session, row) for row in rows]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = session.get(HumanControlApprovalCaseModel, case_id)
            return None if row is None else self._case_to_dict(session, row)

    async def scan_escalations(
        self,
        *,
        workspace_id: str | None = None,
        actor_id: str = "system",
    ) -> dict[str, Any]:
        now = utc_now()
        created: list[dict[str, Any]] = []
        expired_cases = 0
        with self._session_factory() as session:
            case_statement = select(HumanControlApprovalCaseModel).where(
                HumanControlApprovalCaseModel.status == "pending"
            )
            if workspace_id is not None:
                case_statement = case_statement.where(
                    HumanControlApprovalCaseModel.workspace_id == workspace_id
                )
            for case in session.scalars(case_statement).all():
                if ensure_utc(case.due_at) is not None and ensure_utc(case.due_at) <= now:
                    case.status = "expired"
                    case.resolution_reason = "Approval Case TTL expired."
                    expired_cases += 1
                    continue
                step = session.scalar(
                    select(HumanControlApprovalStepModel).where(
                        HumanControlApprovalStepModel.case_id == case.id,
                        HumanControlApprovalStepModel.sequence
                        == case.current_step_sequence,
                        HumanControlApprovalStepModel.status == "pending",
                    )
                )
                if step is None:
                    continue
                if ensure_utc(step.due_at) is None or ensure_utc(step.due_at) > now:
                    continue
                existing = session.scalar(
                    select(HumanControlEscalationModel).where(
                        HumanControlEscalationModel.step_id == step.id,
                        HumanControlEscalationModel.status.in_(
                            ["open", "acknowledged"]
                        ),
                    )
                )
                if existing is not None:
                    continue
                target_roles = list(step.escalation_role_keys_json or []) or ["owner"]
                escalation = HumanControlEscalationModel(
                    case_id=case.id,
                    step_id=step.id,
                    workspace_id=case.workspace_id,
                    escalation_type="timeout",
                    status="open",
                    source_role_keys_json=list(step.required_role_keys_json or []),
                    target_role_keys_json=target_roles,
                    reason="Approval Step exceeded its response time.",
                    created_by=actor_id,
                    due_at=now,
                    metadata_json={"step_sequence": step.sequence},
                )
                session.add(escalation)
                step.escalated = True
                step.escalated_at = now
                session.flush()
                created.append(self._escalation_to_dict(escalation))
        self._escalations_created += len(created)
        for escalation in created:
            await self._publish(
                "human_control.governance.escalation.created",
                workspace_id=escalation["workspace_id"],
                correlation_id=escalation["id"],
                payload={"escalation": escalation},
            )
        return {
            "workspace_id": workspace_id,
            "created": len(created),
            "expired_cases": expired_cases,
            "escalations": created,
            "scanned_at": iso(now),
        }

    async def create_manual_escalation(
        self,
        case_id: str,
        request: HumanControlManualEscalationRequest,
    ) -> dict[str, Any]:
        case = self.get_case(case_id)
        if case is None:
            raise HumanControlNotFound("Approval Case не найден.")
        await self.authorize_operator_action(
            item_id=case["item_id"],
            actor_id=request.actor_id,
            permission=HumanControlPermission.ESCALATE.value,
            decision_action=case["requested_action"],
        )
        with self._session_factory() as session:
            row = session.get(HumanControlApprovalCaseModel, case_id)
            assert row is not None
            step = session.scalar(
                select(HumanControlApprovalStepModel).where(
                    HumanControlApprovalStepModel.case_id == row.id,
                    HumanControlApprovalStepModel.sequence
                    == row.current_step_sequence,
                )
            )
            escalation = HumanControlEscalationModel(
                case_id=row.id,
                step_id=step.id if step else None,
                workspace_id=row.workspace_id,
                escalation_type="manual",
                status="open",
                source_role_keys_json=(
                    list(step.required_role_keys_json or []) if step else []
                ),
                target_role_keys_json=list(request.target_role_keys) or ["owner"],
                reason=request.reason,
                created_by=request.actor_id,
                assigned_to=request.assigned_to,
                due_at=ensure_utc(request.due_at),
                metadata_json=dict(request.metadata),
            )
            session.add(escalation)
            if step is not None:
                step.escalated = True
                step.escalated_at = utc_now()
                if request.target_role_keys:
                    step.escalation_role_keys_json = list(request.target_role_keys)
            session.flush()
            result = self._escalation_to_dict(escalation)
        self._escalations_created += 1
        await self._publish(
            "human_control.governance.escalation.created",
            workspace_id=result["workspace_id"],
            correlation_id=result["id"],
            payload={"escalation": result},
        )
        return result

    def list_escalations(
        self,
        *,
        workspace_id: str | None = None,
        case_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            statement = select(HumanControlEscalationModel)
            if workspace_id is not None:
                statement = statement.where(
                    HumanControlEscalationModel.workspace_id == workspace_id
                )
            if case_id is not None:
                statement = statement.where(
                    HumanControlEscalationModel.case_id == case_id
                )
            if status is not None:
                statement = statement.where(
                    HumanControlEscalationModel.status == status
                )
            rows = session.scalars(
                statement.order_by(HumanControlEscalationModel.created_at.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return [self._escalation_to_dict(row) for row in rows]

    async def acknowledge_escalation(
        self,
        escalation_id: str,
        request: HumanControlEscalationAcknowledgeRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlEscalationModel, escalation_id)
            if row is None:
                raise HumanControlNotFound("Escalation не найдена.")
            case = session.get(HumanControlApprovalCaseModel, row.case_id)
            if case is None:
                raise HumanControlNotFound("Approval Case не найден.")
            item_id = case.item_id
        await self.authorize_operator_action(
            item_id=item_id,
            actor_id=request.actor_id,
            permission=HumanControlPermission.ESCALATE.value,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlEscalationModel, escalation_id)
            assert row is not None
            if row.status == "resolved":
                return self._escalation_to_dict(row)
            row.status = "acknowledged"
            row.acknowledged_by = request.actor_id
            row.acknowledged_at = utc_now()
            metadata = dict(row.metadata_json or {})
            metadata["acknowledgement_reason"] = request.reason
            row.metadata_json = metadata
            result = self._escalation_to_dict(row)
        await self._publish(
            "human_control.governance.escalation.acknowledged",
            workspace_id=result["workspace_id"],
            correlation_id=escalation_id,
            payload={"escalation": result},
        )
        return result

    async def resolve_escalation(
        self,
        escalation_id: str,
        request: HumanControlEscalationResolveRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(HumanControlEscalationModel, escalation_id)
            if row is None:
                raise HumanControlNotFound("Escalation не найдена.")
            case = session.get(HumanControlApprovalCaseModel, row.case_id)
            if case is None:
                raise HumanControlNotFound("Approval Case не найден.")
            item_id = case.item_id
        await self.authorize_operator_action(
            item_id=item_id,
            actor_id=request.actor_id,
            permission=HumanControlPermission.ESCALATE.value,
        )
        with self._session_factory() as session:
            row = session.get(HumanControlEscalationModel, escalation_id)
            assert row is not None
            row.status = "resolved"
            row.resolved_by = request.actor_id
            row.resolved_at = utc_now()
            row.resolution = request.resolution
            result = self._escalation_to_dict(row)
        await self._publish(
            "human_control.governance.escalation.resolved",
            workspace_id=result["workspace_id"],
            correlation_id=escalation_id,
            payload={"escalation": result},
        )
        return result

    async def _authorize_decision(
        self,
        item: dict[str, Any],
        request: HumanControlDecisionRequest,
    ) -> dict[str, Any]:
        risk = str(item.get("risk_level") or "medium")
        permission = HumanControlPermission.DECIDE.value
        if risk == "high":
            permission = HumanControlPermission.DECIDE_HIGH.value
        elif risk == "critical":
            permission = HumanControlPermission.DECIDE_CRITICAL.value
        result = await self.authorize_operator_action(
            item_id=item["id"],
            actor_id=request.actor_id,
            permission=permission,
            decision_action=request.action.value,
            force=request.force,
        )
        extra_permissions: list[str] = []
        if request.action == HumanControlDecisionAction.ACCEPT_RISK:
            extra_permissions.append(HumanControlPermission.ACCEPT_RISK.value)
        if (
            request.action == HumanControlDecisionAction.APPROVE
            and item.get("source_type")
            in {"mission_resource_allocation", "workspace_resource_reservation"}
        ):
            extra_permissions.append(HumanControlPermission.APPROVE_BUDGET.value)
        if (
            request.action == HumanControlDecisionAction.APPLY
            and item.get("source_type") == "mission_learning_run"
        ):
            extra_permissions.append(HumanControlPermission.APPLY_LEARNING.value)
        for extra in extra_permissions:
            await self.authorize_operator_action(
                item_id=item["id"],
                actor_id=request.actor_id,
                permission=extra,
                decision_action=request.action.value,
                force=request.force,
            )
        return result

    async def _execute_case(self, case_id: str) -> dict[str, Any]:
        with self._session_factory() as session:
            case = session.get(HumanControlApprovalCaseModel, case_id)
            if case is None:
                raise HumanControlNotFound("Approval Case не найден.")
            if case.status == "executed":
                return self._case_to_dict(session, case)
            if case.status not in {"approved", "execution_failed"}:
                raise HumanControlConflict(
                    f"Approval Case {case.status} не готов к выполнению."
                )
            case.status = "executing"
            request_payload = dict(case.request_json or {})
            workspace_id = case.workspace_id
            item_id = case.item_id
            approvers = sorted(
                set(
                    session.scalars(
                        select(HumanControlApprovalVoteModel.actor_id).where(
                            HumanControlApprovalVoteModel.case_id == case.id,
                            HumanControlApprovalVoteModel.decision == "approve",
                        )
                    ).all()
                )
            )

        original = HumanControlDecisionRequest.model_validate(request_payload)
        metadata = dict(original.metadata)
        metadata.update(
            {
                "approval_case_id": case_id,
                "approval_chain_approvers": approvers,
            }
        )
        governed_request = original.model_copy(
            update={
                "force": True,
                "metadata": metadata,
            }
        )
        try:
            source_result = await self._control_center.decide(
                item_id,
                governed_request,
            )
        except Exception as exc:
            with self._session_factory() as session:
                case = session.get(HumanControlApprovalCaseModel, case_id)
                assert case is not None
                case.status = "execution_failed"
                case.final_result_json = {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
                case.resolution_reason = str(exc)
                result = self._case_to_dict(session, case)
            await self._publish(
                "human_control.governance.case.execution_failed",
                workspace_id=workspace_id,
                correlation_id=case_id,
                payload={"case": result},
            )
            return result

        with self._session_factory() as session:
            case = session.get(HumanControlApprovalCaseModel, case_id)
            assert case is not None
            case.status = "executed"
            case.executed_at = utc_now()
            case.final_result_json = dict(source_result)
            case.resolution_reason = "Governed decision executed."
            for escalation in session.scalars(
                select(HumanControlEscalationModel).where(
                    HumanControlEscalationModel.case_id == case.id,
                    HumanControlEscalationModel.status.in_(["open", "acknowledged"]),
                )
            ).all():
                escalation.status = "resolved"
                escalation.resolved_by = "system"
                escalation.resolved_at = utc_now()
                escalation.resolution = "Approval Case executed."
            result = self._case_to_dict(session, case)
        await self._publish(
            "human_control.governance.case.executed",
            workspace_id=workspace_id,
            correlation_id=case_id,
            payload={"case": result},
        )
        return result

    def _assert_permission(
        self,
        actor_id: str,
        workspace_id: str | None,
        permission: str,
    ) -> None:
        with self._session_factory() as session:
            if not self._is_governed(session, workspace_id):
                raise HumanControlConflict(
                    "Сначала выполните one-time bootstrap owner для этой области."
                )
            roles = self._effective_roles(session, actor_id, workspace_id)
            if not any(self._role_has_permissions(role, {permission}) for role in roles):
                raise HumanControlConflict(
                    f"Оператор {actor_id} не имеет полномочия {permission}."
                )

    def _is_governed(self, session: Session, workspace_id: str | None) -> bool:
        now = utc_now()
        if self._has_effective_binding(session, workspace_id, now):
            return True
        statement = select(func.count()).select_from(
            HumanControlApprovalPolicyModel
        ).where(HumanControlApprovalPolicyModel.enabled.is_(True))
        if workspace_id is None:
            statement = statement.where(
                HumanControlApprovalPolicyModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    HumanControlApprovalPolicyModel.workspace_id == workspace_id,
                    HumanControlApprovalPolicyModel.workspace_id.is_(None),
                )
            )
        return int(session.scalar(statement) or 0) > 0

    @staticmethod
    def _has_effective_binding(
        session: Session,
        workspace_id: str | None,
        now: datetime,
    ) -> bool:
        statement = select(func.count()).select_from(
            HumanControlRoleBindingModel
        ).where(
            HumanControlRoleBindingModel.enabled.is_(True),
            or_(
                HumanControlRoleBindingModel.valid_from.is_(None),
                HumanControlRoleBindingModel.valid_from <= now,
            ),
            or_(
                HumanControlRoleBindingModel.expires_at.is_(None),
                HumanControlRoleBindingModel.expires_at > now,
            ),
        )
        if workspace_id is None:
            statement = statement.where(
                HumanControlRoleBindingModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    HumanControlRoleBindingModel.workspace_id == workspace_id,
                    HumanControlRoleBindingModel.workspace_id.is_(None),
                )
            )
        return int(session.scalar(statement) or 0) > 0

    def _effective_roles(
        self,
        session: Session,
        actor_id: str,
        workspace_id: str | None,
    ) -> list[HumanControlRoleModel]:
        now = utc_now()
        statement = select(HumanControlRoleBindingModel).where(
            HumanControlRoleBindingModel.actor_id == actor_id,
            HumanControlRoleBindingModel.enabled.is_(True),
            or_(
                HumanControlRoleBindingModel.valid_from.is_(None),
                HumanControlRoleBindingModel.valid_from <= now,
            ),
            or_(
                HumanControlRoleBindingModel.expires_at.is_(None),
                HumanControlRoleBindingModel.expires_at > now,
            ),
        )
        if workspace_id is None:
            statement = statement.where(
                HumanControlRoleBindingModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    HumanControlRoleBindingModel.workspace_id == workspace_id,
                    HumanControlRoleBindingModel.workspace_id.is_(None),
                )
            )
        roles: list[HumanControlRoleModel] = []
        seen: set[str] = set()
        for binding in session.scalars(statement).all():
            role = session.get(HumanControlRoleModel, binding.role_id)
            if role is None or not role.enabled or role.id in seen:
                continue
            if role.workspace_id not in {None, workspace_id}:
                continue
            seen.add(role.id)
            roles.append(role)
        return roles

    def _eligible_roles(
        self,
        session: Session,
        *,
        actor_id: str,
        workspace_id: str | None,
        item: dict[str, Any],
        action: str | None,
        required_permissions: set[str],
    ) -> list[HumanControlRoleModel]:
        return self._filter_roles(
            self._effective_roles(session, actor_id, workspace_id),
            item=item,
            action=action,
            required_permissions=required_permissions,
        )

    def _filter_roles(
        self,
        roles: list[HumanControlRoleModel],
        *,
        item: dict[str, Any],
        action: str | None,
        required_permissions: set[str],
    ) -> list[HumanControlRoleModel]:
        result = []
        for role in roles:
            if not self._role_has_permissions(role, required_permissions):
                continue
            risks = set(role.allowed_risk_levels_json or [])
            if risks and item.get("risk_level") not in risks:
                continue
            sources = set(role.allowed_source_types_json or [])
            if sources and item.get("source_type") not in sources:
                continue
            actions = set(role.allowed_actions_json or [])
            if actions and action not in actions:
                continue
            result.append(role)
        return result

    @staticmethod
    def _role_has_permissions(
        role: HumanControlRoleModel,
        required: set[str],
    ) -> bool:
        permissions = set(role.permissions_json or [])
        return "*" in permissions or required.issubset(permissions)

    def _match_policy_row(
        self,
        session: Session,
        item: dict[str, Any],
        action: str,
    ) -> HumanControlApprovalPolicyModel | None:
        workspace_id = item.get("workspace_id")
        statement = select(HumanControlApprovalPolicyModel).where(
            HumanControlApprovalPolicyModel.enabled.is_(True)
        )
        if workspace_id is None:
            statement = statement.where(
                HumanControlApprovalPolicyModel.workspace_id.is_(None)
            )
        else:
            statement = statement.where(
                or_(
                    HumanControlApprovalPolicyModel.workspace_id == workspace_id,
                    HumanControlApprovalPolicyModel.workspace_id.is_(None),
                )
            )
        rows = session.scalars(statement).all()
        matched = []
        for row in rows:
            if row.source_types_json and item.get("source_type") not in row.source_types_json:
                continue
            if row.action_kinds_json and item.get("action_kind") not in row.action_kinds_json:
                continue
            if row.risk_levels_json and item.get("risk_level") not in row.risk_levels_json:
                continue
            if row.decision_actions_json and action not in row.decision_actions_json:
                continue
            matched.append(row)
        matched.sort(
            key=lambda row: (
                1 if row.workspace_id == workspace_id and workspace_id is not None else 0,
                row.priority,
                -int(row.created_at.timestamp()) if row.created_at else 0,
            ),
            reverse=True,
        )
        return matched[0] if matched else None

    def _resolve_role(
        self,
        session: Session,
        *,
        role_id: str | None,
        role_key: str | None,
        workspace_id: str | None,
    ) -> HumanControlRoleModel:
        if role_id:
            role = session.get(HumanControlRoleModel, role_id)
        else:
            rows = session.scalars(
                select(HumanControlRoleModel).where(
                    HumanControlRoleModel.role_key == role_key,
                    HumanControlRoleModel.enabled.is_(True),
                    or_(
                        HumanControlRoleModel.workspace_id == workspace_id,
                        HumanControlRoleModel.workspace_id.is_(None),
                    ),
                )
            ).all()
            rows = sorted(
                rows,
                key=lambda row: row.workspace_id is not None,
                reverse=True,
            )
            role = rows[0] if rows else None
        if role is None:
            raise HumanControlNotFound("Human Control Role не найдена.")
        return role

    @staticmethod
    def _source_requester(item: dict[str, Any]) -> str | None:
        payload = dict(item.get("payload") or {})
        for key in ("requested_by", "created_by", "actor_id", "owner_id"):
            value = payload.get(key)
            if value:
                return str(value)
        metadata = dict(payload.get("metadata") or {})
        for key in ("requested_by", "created_by", "actor_id"):
            value = metadata.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _role_scope_key(workspace_id: str | None, role_key: str) -> str:
        return f"{workspace_id or 'global'}:{role_key}"

    @staticmethod
    def _policy_scope_key(workspace_id: str | None, policy_key: str) -> str:
        return f"{workspace_id or 'global'}:{policy_key}"

    @staticmethod
    def _binding_key(
        workspace_id: str | None,
        actor_id: str,
        role_id: str,
    ) -> str:
        return f"{workspace_id or 'global'}:{actor_id}:{role_id}"

    @staticmethod
    def _string_values(values: Any) -> list[str]:
        return [str(getattr(value, "value", value)) for value in list(values or [])]

    @staticmethod
    def _role_to_dict(row: HumanControlRoleModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "role_key": row.role_key,
            "name": row.name,
            "description": row.description,
            "enabled": row.enabled,
            "builtin": row.builtin,
            "permissions": list(row.permissions_json or []),
            "allowed_risk_levels": list(row.allowed_risk_levels_json or []),
            "allowed_source_types": list(row.allowed_source_types_json or []),
            "allowed_actions": list(row.allowed_actions_json or []),
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    def _binding_to_dict(
        self,
        row: HumanControlRoleBindingModel,
        *,
        role: HumanControlRoleModel | None,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "binding_key": row.binding_key,
            "workspace_id": row.workspace_id,
            "actor_id": row.actor_id,
            "role_id": row.role_id,
            "role_key": role.role_key if role is not None else None,
            "enabled": row.enabled,
            "valid_from": iso(row.valid_from),
            "expires_at": iso(row.expires_at),
            "granted_by": row.granted_by,
            "reason": row.reason,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _policy_to_dict(row: HumanControlApprovalPolicyModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "scope_key": row.scope_key,
            "workspace_id": row.workspace_id,
            "policy_key": row.policy_key,
            "name": row.name,
            "description": row.description,
            "enabled": row.enabled,
            "priority": row.priority,
            "source_types": list(row.source_types_json or []),
            "action_kinds": list(row.action_kinds_json or []),
            "risk_levels": list(row.risk_levels_json or []),
            "decision_actions": list(row.decision_actions_json or []),
            "steps": list(row.steps_json or []),
            "distinct_approvers": row.distinct_approvers,
            "prohibit_source_requester_approval": (
                row.prohibit_source_requester_approval
            ),
            "prohibit_case_initiator_approval": (
                row.prohibit_case_initiator_approval
            ),
            "rejection_mode": row.rejection_mode,
            "case_ttl_seconds": row.case_ttl_seconds,
            "default_step_ttl_seconds": row.default_step_ttl_seconds,
            "escalation_after_seconds": row.escalation_after_seconds,
            "metadata": dict(row.metadata_json or {}),
            "created_by": row.created_by,
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    def _case_to_dict(
        self,
        session: Session,
        row: HumanControlApprovalCaseModel,
    ) -> dict[str, Any]:
        steps = session.scalars(
            select(HumanControlApprovalStepModel)
            .where(HumanControlApprovalStepModel.case_id == row.id)
            .order_by(HumanControlApprovalStepModel.sequence.asc())
        ).all()
        votes = session.scalars(
            select(HumanControlApprovalVoteModel)
            .where(HumanControlApprovalVoteModel.case_id == row.id)
            .order_by(HumanControlApprovalVoteModel.created_at.asc())
        ).all()
        escalations = session.scalars(
            select(HumanControlEscalationModel)
            .where(HumanControlEscalationModel.case_id == row.id)
            .order_by(HumanControlEscalationModel.created_at.asc())
        ).all()
        return {
            "id": row.id,
            "item_id": row.item_id,
            "policy_id": row.policy_id,
            "workspace_id": row.workspace_id,
            "requested_action": row.requested_action,
            "requested_by": row.requested_by,
            "source_requester": row.source_requester,
            "request_idempotency_key": row.request_idempotency_key,
            "status": row.status,
            "current_step_sequence": row.current_step_sequence,
            "total_steps": row.total_steps,
            "distinct_approvers": row.distinct_approvers,
            "prohibit_source_requester_approval": (
                row.prohibit_source_requester_approval
            ),
            "prohibit_case_initiator_approval": (
                row.prohibit_case_initiator_approval
            ),
            "request": dict(row.request_json or {}),
            "final_result": dict(row.final_result_json or {}),
            "due_at": iso(row.due_at),
            "approved_at": iso(row.approved_at),
            "rejected_at": iso(row.rejected_at),
            "executed_at": iso(row.executed_at),
            "resolved_by": row.resolved_by,
            "resolution_reason": row.resolution_reason,
            "steps": [self._step_to_dict(step) for step in steps],
            "votes": [self._vote_to_dict(vote) for vote in votes],
            "escalations": [
                self._escalation_to_dict(escalation) for escalation in escalations
            ],
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _step_to_dict(row: HumanControlApprovalStepModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "case_id": row.case_id,
            "step_key": row.step_key,
            "sequence": row.sequence,
            "title": row.title,
            "status": row.status,
            "required_role_keys": list(row.required_role_keys_json or []),
            "escalation_role_keys": list(row.escalation_role_keys_json or []),
            "min_approvals": row.min_approvals,
            "approvals_received": row.approvals_received,
            "rejections_received": row.rejections_received,
            "due_at": iso(row.due_at),
            "escalated": row.escalated,
            "escalated_at": iso(row.escalated_at),
            "completed_at": iso(row.completed_at),
            "metadata": dict(row.metadata_json or {}),
        }

    @staticmethod
    def _vote_to_dict(row: HumanControlApprovalVoteModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "case_id": row.case_id,
            "step_id": row.step_id,
            "actor_id": row.actor_id,
            "decision": row.decision,
            "reason": row.reason,
            "role_keys": list(row.role_keys_json or []),
            "idempotency_key": row.idempotency_key,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
        }

    @staticmethod
    def _escalation_to_dict(row: HumanControlEscalationModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "case_id": row.case_id,
            "step_id": row.step_id,
            "workspace_id": row.workspace_id,
            "escalation_type": row.escalation_type,
            "status": row.status,
            "source_role_keys": list(row.source_role_keys_json or []),
            "target_role_keys": list(row.target_role_keys_json or []),
            "reason": row.reason,
            "created_by": row.created_by,
            "assigned_to": row.assigned_to,
            "due_at": iso(row.due_at),
            "acknowledged_by": row.acknowledged_by,
            "acknowledged_at": iso(row.acknowledged_at),
            "resolved_by": row.resolved_by,
            "resolved_at": iso(row.resolved_at),
            "resolution": row.resolution,
            "metadata": dict(row.metadata_json or {}),
            "created_at": iso(row.created_at),
            "updated_at": iso(row.updated_at),
        }

    @staticmethod
    def _item_model_to_dict(row: HumanControlItemModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_type": row.source_type,
            "action_kind": row.action_kind,
            "risk_level": row.risk_level,
            "payload": dict(row.payload_json or {}),
        }

    async def _publish(
        self,
        event_type: str,
        *,
        workspace_id: str | None,
        correlation_id: str | None,
        payload: dict[str, Any],
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="human_control_governance",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
