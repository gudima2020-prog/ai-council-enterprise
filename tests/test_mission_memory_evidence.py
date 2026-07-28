from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.autonomy import models as autonomy_models  # noqa: F401
from backend.autonomy.memory import MissionMemoryService
from backend.autonomy.memory_schemas import (
    MissionEvidencePolicyUpsert,
    MissionEvidenceReviewRequest,
    MissionEvidenceSubmit,
    MissionEvidenceType,
    MissionGoalConfirmRequest,
    MissionGoalReopenRequest,
    MissionMemoryCategory,
    MissionMemoryUpsert,
)
from backend.autonomy.schemas import (
    MissionActivateRequest,
    MissionCreate,
    MissionCycleCreateRequest,
    MissionCycleRunRequest,
    MissionGoalCreate,
)
from backend.autonomy.service import AutonomousMissionService
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
                id="workspace_memory",
                name="Mission Memory Test",
            )
        )
    return scope


async def make_mission(scope, *, goals=1):
    bus = EventBus()
    planner = ExecutionPlannerService(event_bus=bus, session_factory=scope)
    mission_service = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
    )
    mission = await mission_service.create_mission(
        MissionCreate(
            workspace_id="workspace_memory",
            title="Evidence mission",
            objective="Produce a verified deployment result with tests.",
            goals=[
                MissionGoalCreate(
                    goal_key=f"goal_{index}",
                    title=f"Verify deployment result {index}",
                    success_criteria="Deployment test passed with an artifact checksum.",
                )
                for index in range(goals)
            ],
        )
    )
    memory_service = MissionMemoryService(event_bus=bus, session_factory=scope)
    return mission_service, memory_service, mission, bus


@pytest.mark.asyncio
async def test_mission_memory_is_versioned_and_builds_context() -> None:
    scope = make_scope()
    _, service, mission, _ = await make_mission(scope)

    first = await service.upsert_memory(
        mission["id"],
        "deployment.constraint",
        MissionMemoryUpsert(
            category=MissionMemoryCategory.CONSTRAINT,
            content={"region": "eu-north", "max_downtime_minutes": 5},
            importance_score=90,
        ),
    )
    second = await service.upsert_memory(
        mission["id"],
        "deployment.constraint",
        MissionMemoryUpsert(
            category=MissionMemoryCategory.CONSTRAINT,
            content={"region": "eu-north", "max_downtime_minutes": 2},
            importance_score=95,
        ),
    )

    assert first["version"] == 1
    assert second["version"] == 2
    context = service.build_context(mission["id"])
    assert context["entry_count"] == 1
    assert context["context"]["deployment.constraint"]["max_downtime_minutes"] == 2


@pytest.mark.asyncio
async def test_default_policy_requires_human_review() -> None:
    scope = make_scope()
    _, service, mission, _ = await make_mission(scope)
    goal = mission["goals"][0]

    policy = service.get_evidence_policy(mission["id"], goal["id"])
    assert policy["require_human_review"] is True
    assert policy["auto_confirm_enabled"] is False

    result = await service.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.TEST_RESULT,
            source_type="ci",
            source_ref="run-100",
            content={
                "status": "passed",
                "artifact_id": "artifact-100",
                "checksum": "abc123",
                "summary": "Deployment test passed.",
            },
        ),
    )
    assert result["evidence"]["status"] == "needs_review"
    current = service.list_evidence(mission["id"])
    assert len(current) == 1


@pytest.mark.asyncio
async def test_auto_confirmation_completes_single_goal_mission() -> None:
    scope = make_scope()
    mission_service, service, mission, _ = await make_mission(scope)
    goal = mission["goals"][0]
    await mission_service.activate_mission(mission["id"], MissionActivateRequest())

    await service.upsert_evidence_policy(
        mission["id"],
        goal["id"],
        MissionEvidencePolicyUpsert(
            required_evidence_count=1,
            min_individual_score=50,
            min_average_score=50,
            require_human_review=False,
            auto_confirm_enabled=True,
        ),
    )
    result = await service.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.TEST_RESULT,
            source_type="ci",
            source_ref="run-200",
            content={
                "status": "passed",
                "passed": True,
                "artifact_id": "artifact-200",
                "checksum": "def456",
                "result": "Verified deployment result and tests.",
            },
        ),
    )

    assert result["evidence"]["status"] == "accepted"
    confirmation = result["goal_evaluation"]["automatic_confirmation"]
    assert confirmation is not None
    assert confirmation["confirmation"]["automatic"] is True
    assert confirmation["goal"]["status"] == "achieved"
    assert confirmation["mission"]["status"] == "completed"
    memory = service.build_context(mission["id"])
    assert f"goal.{goal['goal_key']}.confirmation" in memory["context"]


@pytest.mark.asyncio
async def test_distinct_source_policy_waits_for_second_source() -> None:
    scope = make_scope()
    mission_service, service, mission, _ = await make_mission(scope)
    goal = mission["goals"][0]
    await mission_service.activate_mission(mission["id"], MissionActivateRequest())
    await service.upsert_evidence_policy(
        mission["id"],
        goal["id"],
        MissionEvidencePolicyUpsert(
            required_evidence_count=2,
            min_individual_score=40,
            min_average_score=40,
            require_distinct_sources=True,
            min_distinct_sources=2,
            require_human_review=False,
            auto_confirm_enabled=True,
        ),
    )

    first = await service.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.ARTIFACT,
            source_type="builder",
            source_ref="build-1",
            content={"artifact_id": "a1", "checksum": "111", "status": "ready"},
        ),
    )
    assert first["goal_evaluation"]["eligible"] is False

    second = await service.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.TEST_RESULT,
            source_type="qa",
            source_ref="qa-1",
            content={"test_id": "t1", "passed": True, "result": "verified"},
        ),
    )
    assert second["goal_evaluation"]["eligible"] is True
    assert second["goal_evaluation"]["automatic_confirmation"] is not None


@pytest.mark.asyncio
async def test_manual_review_confirmation_and_reopen() -> None:
    scope = make_scope()
    _, service, mission, _ = await make_mission(scope)
    goal = mission["goals"][0]
    submitted = await service.submit_evidence(
        mission["id"],
        goal["id"],
        MissionEvidenceSubmit(
            evidence_type=MissionEvidenceType.HUMAN_ATTESTATION,
            source_type="reviewer",
            source_ref="review-1",
            content={"result": "approved", "reviewer": "owner"},
        ),
    )
    evidence_id = submitted["evidence"]["id"]
    reviewed = await service.review_evidence(
        evidence_id,
        MissionEvidenceReviewRequest(
            decision="accept",
            actor_id="owner",
            reason="Evidence checked manually.",
            auto_confirm=False,
        ),
    )
    assert reviewed["evidence"]["status"] == "accepted"

    confirmed = await service.confirm_goal(
        mission["id"],
        goal["id"],
        MissionGoalConfirmRequest(
            actor_id="owner",
            reason="Goal accepted after manual review.",
            evidence_ids=[evidence_id],
        ),
    )
    assert confirmed["goal"]["status"] == "achieved"

    reopened = await service.reopen_goal(
        mission["id"],
        goal["id"],
        MissionGoalReopenRequest(
            actor_id="owner",
            reason="Additional validation is required.",
            progress_percent=80,
        ),
    )
    assert reopened["goal"]["status"] == "active"
    assert reopened["mission"]["status"] == "active"
    assert len(service.list_confirmations(mission["id"])) == 2


@pytest.mark.asyncio
async def test_mission_memory_is_injected_into_planner_context() -> None:
    scope = make_scope()
    bus = EventBus()

    class CapturingPlanner:
        request = None

        async def generate(self, request):
            self.request = request
            return {
                "plan": {"id": "plan_memory_context", "status": "ready"},
                "planner_run": {"id": "planner_run_memory_context"},
                "runtime": None,
            }

    planner = CapturingPlanner()
    memory_service = MissionMemoryService(event_bus=bus, session_factory=scope)
    mission_service = AutonomousMissionService(
        event_bus=bus,
        planner=planner,
        session_factory=scope,
        scheduler_interval_seconds=3600,
        memory_context_provider=memory_service.build_context,
    )
    mission = await mission_service.create_mission(
        MissionCreate(
            workspace_id="workspace_memory",
            title="Planner memory mission",
            objective="Use retained constraints during planning.",
            goals=[MissionGoalCreate(goal_key="plan", title="Create plan")],
        )
    )
    await memory_service.upsert_memory(
        mission["id"],
        "execution.constraint",
        MissionMemoryUpsert(
            category=MissionMemoryCategory.CONSTRAINT,
            content={"network_access": False},
        ),
    )
    await mission_service.activate_mission(mission["id"], MissionActivateRequest())
    cycle = await mission_service.create_cycle(
        mission["id"],
        MissionCycleCreateRequest(idempotency_key="memory-context-cycle"),
    )
    await mission_service.run_cycle(
        cycle["id"],
        MissionCycleRunRequest(auto_start=False),
    )

    assert planner.request is not None
    assert planner.request.context["mission_memory"]["execution.constraint"] == {
        "network_access": False
    }
