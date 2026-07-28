from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center.governance import HumanControlGovernanceService
from backend.control_center.governance_schemas import (
    HumanControlApprovalPolicyCreate,
    HumanControlApprovalStepSpec,
    HumanControlApprovalVoteDecision,
    HumanControlApprovalVoteRequest,
    HumanControlBootstrapOwnerRequest,
    HumanControlPermission,
    HumanControlRoleBindingCreate,
)
from backend.control_center.models import HumanControlApprovalStepModel
from backend.control_center.schemas import (
    HumanControlDecisionAction,
    HumanControlDecisionRequest,
    HumanControlRiskLevel,
    HumanControlSourceType,
)
from backend.control_center.service import HumanControlCenterService, HumanControlConflict
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401
from backend.task_engine.models import TaskApprovalModel


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    @contextmanager
    def scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with scope() as session:
        session.add(
            database_models.WorkspaceModel(
                id="workspace_governance",
                name="Governance Test",
            )
        )
    return scope


class FakeTaskApprovalManager:
    def __init__(self, scope) -> None:
        self.scope = scope
        self.calls = 0

    async def decide(self, *, approval_id, request):
        self.calls += 1
        with self.scope() as session:
            row = session.get(TaskApprovalModel, approval_id)
            assert row is not None
            row.status = (
                "approved" if request.decision.value == "approve" else "rejected"
            )
            row.decided_by = request.decided_by
            row.decision_note = request.note
            row.decided_at = datetime.now(timezone.utc)
            return {
                "id": row.id,
                "task_id": row.task_id,
                "workspace_id": row.workspace_id,
                "status": row.status,
            }


def seed_task_approval(
    scope,
    *,
    approval_id: str = "approval_governance",
    requested_by: str = "agent",
    risk_level: str = "high",
) -> None:
    with scope() as session:
        session.add(
            TaskApprovalModel(
                id=approval_id,
                task_id=f"task_{approval_id}",
                workspace_id="workspace_governance",
                gate_key="execution",
                status="pending",
                prompt="Подтвердить выполнение Task?",
                requested_by=requested_by,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                metadata_json={"risk_level": risk_level, "priority": 90},
            )
        )


async def make_services(scope):
    manager = FakeTaskApprovalManager(scope)
    center = HumanControlCenterService(
        event_bus=EventBus(),
        task_approval_manager=manager,
        session_factory=scope,
    )
    await center.sync()
    governance = HumanControlGovernanceService(
        event_bus=EventBus(),
        control_center=center,
        session_factory=scope,
    )
    governance.seed_builtin_roles()
    await governance.bootstrap_owner(
        HumanControlBootstrapOwnerRequest(
            workspace_id="workspace_governance",
            actor_id="owner",
        )
    )
    return manager, center, governance


async def grant_role(governance, *, actor_id: str, role_key: str) -> None:
    await governance.grant_binding(
        HumanControlRoleBindingCreate(
            workspace_id="workspace_governance",
            actor_id=actor_id,
            role_key=role_key,
            granted_by="owner",
            reason=f"Grant {role_key} for test.",
        )
    )


@pytest.mark.asyncio
async def test_roles_enforce_risk_specific_decision_permission() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    _, center, governance = await make_services(scope)
    item = center.list_items()[0]

    await grant_role(governance, actor_id="operator", role_key="operator")
    access = governance.effective_access(
        actor_id="operator",
        workspace_id="workspace_governance",
    )
    assert "operator" in access["role_keys"]

    with pytest.raises(HumanControlConflict):
        await governance.authorize_operator_action(
            item_id=item["id"],
            actor_id="operator",
            permission=HumanControlPermission.DECIDE_HIGH.value,
            decision_action="approve",
        )

    await grant_role(
        governance,
        actor_id="operator",
        role_key="senior_operator",
    )
    result = await governance.authorize_operator_action(
        item_id=item["id"],
        actor_id="operator",
        permission=HumanControlPermission.DECIDE_HIGH.value,
        decision_action="approve",
    )
    assert result["allowed"] is True
    assert "senior_operator" in result["role_keys"]


@pytest.mark.asyncio
async def test_two_step_chain_executes_after_distinct_approvers() -> None:
    scope = make_scope()
    seed_task_approval(scope)
    manager, center, governance = await make_services(scope)
    await grant_role(governance, actor_id="alice", role_key="senior_operator")
    await grant_role(governance, actor_id="bob", role_key="risk_officer")

    await governance.create_policy(
        HumanControlApprovalPolicyCreate(
            workspace_id="workspace_governance",
            policy_key="high-risk-task",
            name="High-risk Task chain",
            source_types=[HumanControlSourceType.TASK_APPROVAL],
            risk_levels=[HumanControlRiskLevel.HIGH],
            decision_actions=[HumanControlDecisionAction.APPROVE],
            steps=[
                HumanControlApprovalStepSpec(
                    step_key="senior-review",
                    title="Senior operator review",
                    required_role_keys=["senior_operator"],
                ),
                HumanControlApprovalStepSpec(
                    step_key="risk-approval",
                    title="Risk officer approval",
                    required_role_keys=["risk_officer"],
                ),
            ],
            distinct_approvers=True,
            actor_id="owner",
        )
    )

    item = center.list_items()[0]
    submitted = await governance.submit_decision(
        item["id"],
        HumanControlDecisionRequest(
            action=HumanControlDecisionAction.APPROVE,
            actor_id="alice",
            reason="Первичная проверка выполнена.",
            idempotency_key="chain-decision-001",
        ),
    )
    case = submitted["case"]
    assert case["status"] == "pending"
    assert case["current_step_sequence"] == 2
    assert manager.calls == 0

    result = await governance.cast_vote(
        case["id"],
        HumanControlApprovalVoteRequest(
            actor_id="bob",
            decision=HumanControlApprovalVoteDecision.APPROVE,
            reason="Риск принят в пределах политики.",
            idempotency_key="chain-vote-bob-001",
        ),
    )
    assert result["case"]["status"] == "executed"
    assert manager.calls == 1
    with scope() as session:
        approval = session.get(TaskApprovalModel, "approval_governance")
        assert approval is not None
        assert approval.status == "approved"


@pytest.mark.asyncio
async def test_source_requester_cannot_approve_own_request() -> None:
    scope = make_scope()
    seed_task_approval(scope, requested_by="alice")
    _, center, governance = await make_services(scope)
    await grant_role(governance, actor_id="alice", role_key="senior_operator")

    await governance.create_policy(
        HumanControlApprovalPolicyCreate(
            workspace_id="workspace_governance",
            policy_key="requester-separation",
            name="Requester separation",
            source_types=[HumanControlSourceType.TASK_APPROVAL],
            decision_actions=[HumanControlDecisionAction.APPROVE],
            steps=[
                HumanControlApprovalStepSpec(
                    step_key="review",
                    title="Independent review",
                    required_role_keys=["senior_operator"],
                )
            ],
            prohibit_source_requester_approval=True,
            actor_id="owner",
        )
    )

    item = center.list_items()[0]
    submitted = await governance.submit_decision(
        item["id"],
        HumanControlDecisionRequest(
            action=HumanControlDecisionAction.APPROVE,
            actor_id="alice",
            reason="Попытка согласовать собственный запрос.",
            idempotency_key="requester-separation-001",
        ),
    )
    assert submitted["case"]["status"] == "pending"
    assert submitted["initial_vote_error"] is not None

    with pytest.raises(HumanControlConflict):
        await governance.cast_vote(
            submitted["case"]["id"],
            HumanControlApprovalVoteRequest(
                actor_id="alice",
                decision=HumanControlApprovalVoteDecision.APPROVE,
                reason="Повторная попытка.",
                idempotency_key="requester-separation-vote-001",
            ),
        )


@pytest.mark.asyncio
async def test_timeout_escalation_expands_eligible_roles() -> None:
    scope = make_scope()
    seed_task_approval(scope, risk_level="medium")
    manager, center, governance = await make_services(scope)
    await grant_role(governance, actor_id="initiator", role_key="operator")
    await grant_role(governance, actor_id="escalation-owner", role_key="owner")

    await governance.create_policy(
        HumanControlApprovalPolicyCreate(
            workspace_id="workspace_governance",
            policy_key="timeout-escalation",
            name="Timeout escalation",
            source_types=[HumanControlSourceType.TASK_APPROVAL],
            decision_actions=[HumanControlDecisionAction.APPROVE],
            steps=[
                HumanControlApprovalStepSpec(
                    step_key="senior-review",
                    title="Senior review",
                    required_role_keys=["senior_operator"],
                    ttl_seconds=60,
                    escalation_role_keys=["owner"],
                )
            ],
            prohibit_case_initiator_approval=True,
            actor_id="owner",
        )
    )

    item = center.list_items()[0]
    submitted = await governance.submit_decision(
        item["id"],
        HumanControlDecisionRequest(
            action=HumanControlDecisionAction.APPROVE,
            actor_id="initiator",
            reason="Отправить на согласование.",
            idempotency_key="escalation-case-001",
        ),
    )
    case_id = submitted["case"]["id"]
    with scope() as session:
        step = session.scalar(
            select(HumanControlApprovalStepModel).where(
                HumanControlApprovalStepModel.case_id == case_id
            )
        )
        assert step is not None
        step.due_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    scan = await governance.scan_escalations(
        workspace_id="workspace_governance"
    )
    assert scan["created"] == 1
    assert scan["escalations"][0]["target_role_keys"] == ["owner"]

    result = await governance.cast_vote(
        case_id,
        HumanControlApprovalVoteRequest(
            actor_id="escalation-owner",
            decision=HumanControlApprovalVoteDecision.APPROVE,
            reason="Эскалация рассмотрена владельцем.",
            idempotency_key="escalation-owner-vote-001",
        ),
    )
    assert result["case"]["status"] == "executed"
    assert manager.calls == 1
