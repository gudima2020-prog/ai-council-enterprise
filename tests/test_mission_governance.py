from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy import models as autonomy_models  # noqa: F401
from backend.autonomy.governance import MissionGovernanceService
from backend.autonomy.governance_schemas import (
    MissionCheckpointCreate,
    MissionCheckpointDecision,
    MissionCheckpointDecisionRequest,
    MissionCheckpointEvaluateRequest,
    MissionHypothesisCreate,
    MissionHypothesisEvaluateRequest,
    MissionRiskAssessRequest,
    MissionRiskCreate,
    MissionRiskDecision,
)
from backend.autonomy.memory import MissionMemoryService
from backend.autonomy.memory_schemas import (
    MissionEvidenceSubmit,
    MissionEvidenceType,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService, MissionStateError
from backend.core.events import Event, EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.orchestration.planner import ExecutionPlannerService
from backend.task_engine import models as task_models  # noqa: F401


def make_scope():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

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
                name="Mission Governance Test",
            )
        )
    return scope


async def make_services(scope):
    bus = EventBus()
    planner = ExecutionPlannerService(event_bus=bus, session_factory=scope)
    governance = MissionGovernanceService(event_bus=bus, session_factory=scope)
    mission_service = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        governance_context_provider=governance.build_context,
        cycle_admission_guard=governance.evaluate_cycle_admission,
    )
    mission = await mission_service.create_mission(
        MissionCreate(
            workspace_id="workspace_governance",
            title="Governed mission",
            objective="Deliver a verified result while controlling Mission risk.",
            goals=[
                MissionGoalCreate(
                    goal_key="deliver",
                    title="Deliver verified result",
                    success_criteria="Result is verified and accepted.",
                )
            ],
        )
    )
    await mission_service.activate_mission(
        mission["id"],
        MissionActivateRequest(),
    )
    return bus, planner, governance, mission_service, mission


@pytest.mark.asyncio
async def test_high_risk_creates_blocking_checkpoint_and_blocks_cycle() -> None:
    scope = make_scope()
    _, _, governance, mission_service, mission = await make_services(scope)
    result = await governance.create_risk(
        mission["id"],
        MissionRiskCreate(
            risk_key="external.api.outage",
            title="External API outage",
            probability_percent=90,
            impact_percent=90,
        ),
    )
    assert result["risk"]["severity"] == "critical"
    assert result["checkpoint"]["status"] == "ready"
    assert result["checkpoint"]["blocking"] is True

    cycle = await mission_service.create_cycle(
        mission["id"],
        MissionCycleCreateRequest(),
    )
    admission = governance.evaluate_cycle_admission(cycle["id"])
    assert admission["allowed"] is False
    assert admission["blocking_checkpoint_count"] == 1

    with pytest.raises(MissionStateError):
        await mission_service.run_cycle(
            cycle["id"],
            MissionCycleRunRequest(),
        )


@pytest.mark.asyncio
async def test_accepting_risk_resolves_checkpoint_and_allows_cycle() -> None:
    scope = make_scope()
    _, _, governance, mission_service, mission = await make_services(scope)
    result = await governance.create_risk(
        mission["id"],
        MissionRiskCreate(
            risk_key="known.vendor.limit",
            title="Known vendor limit",
            probability_percent=80,
            impact_percent=70,
        ),
    )
    risk_id = result["risk"]["id"]
    await governance.assess_risk(
        risk_id,
        MissionRiskAssessRequest(
            decision=MissionRiskDecision.ACCEPT,
            rationale="The residual risk is within the approved tolerance.",
        ),
    )
    cycle = await mission_service.create_cycle(
        mission["id"],
        MissionCycleCreateRequest(),
    )
    admission = governance.evaluate_cycle_admission(cycle["id"])
    assert admission["allowed"] is True
    assert governance.get_risk(risk_id)["status"] == "accepted"


@pytest.mark.asyncio
async def test_automatic_hypothesis_evaluation_uses_evidence_effects() -> None:
    scope = make_scope()
    bus, _, governance, _, mission = await make_services(scope)
    memory = MissionMemoryService(event_bus=bus, session_factory=scope)
    goal = mission["goals"][0]
    hypothesis = await governance.create_hypothesis(
        mission["id"],
        MissionHypothesisCreate(
            hypothesis_key="solution.is_viable",
            statement="The proposed solution is technically viable.",
            goal_id=goal["id"],
            target_confidence_percent=50,
            min_evidence_count=2,
            support_threshold_percent=60,
            reject_threshold_percent=30,
        ),
    )
    first = await memory.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.TEST_RESULT,
            source_type="ci",
            source_ref="run-a",
            content={
                "status": "passed",
                "passed": True,
                "artifact_id": "artifact-a",
                "checksum": "aaa",
                "hypothesis_effect": "support",
            },
        ),
    )
    second = await memory.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.TEST_RESULT,
            source_type="ci",
            source_ref="run-b",
            content={
                "status": "passed",
                "passed": True,
                "artifact_id": "artifact-b",
                "checksum": "bbb",
                "hypothesis_effect": "support",
            },
        ),
    )
    evaluated = await governance.evaluate_hypothesis(
        hypothesis["id"],
        MissionHypothesisEvaluateRequest(
            automatic=True,
            actor_id="system",
            evidence_ids=[
                first["evidence"]["id"],
                second["evidence"]["id"],
            ],
        ),
    )
    assert evaluated["hypothesis"]["status"] == "supported"
    assert evaluated["evaluation"]["decision"] == "support"
    assert evaluated["evaluation"]["support_score_percent"] == 100.0


@pytest.mark.asyncio
async def test_safe_automatic_checkpoint_decision() -> None:
    scope = make_scope()
    _, _, governance, _, mission = await make_services(scope)
    checkpoint = await governance.create_checkpoint(
        mission["id"],
        MissionCheckpointCreate(
            checkpoint_key="quality.safe_continue",
            title="Safe continuation",
            blocking=True,
            requires_human=False,
            auto_decision_enabled=True,
            auto_decision_threshold_percent=90,
            recommended_decision=MissionCheckpointDecision.CONTINUE,
            recommendation_confidence_percent=99,
        ),
    )
    result = await governance.evaluate_checkpoint(
        checkpoint["id"],
        MissionCheckpointEvaluateRequest(auto_decide=True),
    )
    assert result["decision"] is not None
    assert result["decision"]["checkpoint"]["status"] == "resolved"
    assert result["decision"]["decision"]["automatic"] is True


@pytest.mark.asyncio
async def test_checkpoint_pause_changes_mission_state() -> None:
    scope = make_scope()
    _, _, governance, _, mission = await make_services(scope)
    checkpoint = await governance.create_checkpoint(
        mission["id"],
        MissionCheckpointCreate(
            checkpoint_key="manual.pause",
            title="Manual pause decision",
        ),
    )
    result = await governance.decide_checkpoint(
        checkpoint["id"],
        MissionCheckpointDecisionRequest(
            decision=MissionCheckpointDecision.PAUSE,
            rationale="A manual review is required.",
        ),
    )
    assert result["checkpoint"]["status"] == "resolved"
    assert result["mission"]["status"] == "paused"


@pytest.mark.asyncio
async def test_cycle_failure_event_is_captured_as_materialized_risk() -> None:
    scope = make_scope()
    _, _, governance, _, mission = await make_services(scope)
    await governance.handle_event(
        Event(
            event_type="mission.cycle.failed",
            source="test",
            workspace_id=mission["workspace_id"],
            correlation_id=mission["id"],
            payload={
                "mission_id": mission["id"],
                "cycle_id": "mcycle_failed_1",
                "error": "Temporary provider failure.",
            },
        )
    )
    risks = governance.list_risks(mission["id"])
    assert len(risks) == 1
    assert risks[0]["risk_key"] == "cycle_failure.mcycle_failed_1"
    assert risks[0]["severity"] == "high"
    checkpoints = governance.list_checkpoints(mission["id"], blocking=True)
    assert len(checkpoints) == 1
