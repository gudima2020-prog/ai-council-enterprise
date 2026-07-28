from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.council.control import CouncilControlService
from backend.council.history import CouncilHistoryService
from backend.council.models import CouncilCostLedgerModel, CouncilCostReservationModel
from backend.council.repository import CouncilRunRepository
from backend.council.schemas import (
    CouncilBudgetPolicyUpdate,
    CouncilMemberRequest,
    CouncilMemberResult,
    CouncilRole,
    CouncilRunRequest,
    CouncilRunResponse,
    CouncilSynthesis,
    CouncilUsage,
)
from backend.database.base import Base
from backend.database.models import ModelConfigModel


def make_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def add_models(session: Session) -> None:
    session.add_all(
        [
            ModelConfigModel(
                provider="openrouter",
                slug="expensive-a",
                display_name="Expensive A",
                priority=20,
                metadata_json={
                    "billing": "metered",
                    "pricing": {
                        "input_per_million_usd": 10.0,
                        "output_per_million_usd": 30.0,
                    },
                },
            ),
            ModelConfigModel(
                provider="openrouter",
                slug="expensive-b",
                display_name="Expensive B",
                priority=20,
                metadata_json={
                    "billing": "metered",
                    "pricing": {
                        "input_per_million_usd": 8.0,
                        "output_per_million_usd": 24.0,
                    },
                },
            ),
            ModelConfigModel(
                provider="openrouter",
                slug="cheap-a",
                display_name="Cheap A",
                priority=10,
                metadata_json={
                    "billing": "metered",
                    "pricing": {
                        "input_per_million_usd": 0.5,
                        "output_per_million_usd": 1.0,
                    },
                },
            ),
            ModelConfigModel(
                provider="openrouter",
                slug="cheap-b",
                display_name="Cheap B",
                priority=11,
                metadata_json={
                    "billing": "metered",
                    "pricing": {
                        "input_per_million_usd": 0.7,
                        "output_per_million_usd": 1.2,
                    },
                },
            ),
        ]
    )
    session.commit()


def request() -> CouncilRunRequest:
    return CouncilRunRequest(
        question="Сравни архитектуры и выбери безопасный вариант.",
        mode="code",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="expensive-a",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="expensive-b",
                role=CouncilRole.CRITIC,
            ),
        ],
        synthesizer_provider="openrouter",
        synthesizer_model="expensive-a",
    )


def result(run_id: str, *, provider_costs: bool) -> CouncilRunResponse:
    now = datetime.now(timezone.utc)
    members = [
        CouncilMemberResult(
            provider="openrouter",
            model="expensive-a",
            requested_model="expensive-a",
            role=CouncilRole.ANALYST,
            label="analyst",
            status="success",
            answer="A",
            usage=CouncilUsage(input_tokens=1000, output_tokens=200, total_tokens=1200),
            provider_reported_cost_usd=0.016 if provider_costs else None,
        ),
        CouncilMemberResult(
            provider="openrouter",
            model="expensive-b",
            requested_model="expensive-b",
            role=CouncilRole.CRITIC,
            label="critic",
            status="success",
            answer="B",
            usage=CouncilUsage(input_tokens=900, output_tokens=250, total_tokens=1150),
            provider_reported_cost_usd=0.0132 if provider_costs else None,
        ),
    ]
    synthesis = CouncilSynthesis(
        provider="openrouter",
        model="expensive-a",
        requested_model="expensive-a",
        status="structured",
        final_answer="Итог",
        usage=CouncilUsage(input_tokens=2000, output_tokens=300, total_tokens=2300),
        provider_reported_cost_usd=0.029 if provider_costs else None,
    )
    return CouncilRunResponse(
        run_id=run_id,
        status="completed",
        question=request().question,
        mode="code",
        members=members,
        synthesis=synthesis,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        duration_ms=1000.0,
    )


@pytest.mark.asyncio
async def test_provider_reported_actual_cost_settles_reservation_and_ledger() -> None:
    with make_session() as session:
        add_models(session)
        control = CouncilControlService(session=session, event_bus=EventBus())
        authorized, estimate = await control.authorize(request(), None)
        assert authorized.cost_reservation_id
        assert estimate.estimated_cost_usd is not None

        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        detail = await history.record_success(
            request=authorized,
            result=result("cost_provider_run", provider_costs=True),
        )
        session.commit()

        assert detail.actual_cost_status == "known"
        assert detail.actual_cost_usd == pytest.approx(0.0582)
        assert detail.actual_total_tokens == 4650

        ledger = list(
            session.scalars(
                select(CouncilCostLedgerModel)
                .where(CouncilCostLedgerModel.run_id == "cost_provider_run")
                .order_by(CouncilCostLedgerModel.line_key)
            ).all()
        )
        assert len(ledger) == 3
        assert all(row.cost_source == "provider" for row in ledger)
        assert sum(row.actual_cost_usd or 0.0 for row in ledger) == pytest.approx(0.0582)

        reservation = session.get(
            CouncilCostReservationModel,
            authorized.cost_reservation_id,
        )
        assert reservation is not None
        assert reservation.status == "settled"
        assert reservation.run_id == "cost_provider_run"
        assert reservation.actual_cost_usd == pytest.approx(0.0582)
        assert reservation.actual_total_tokens == 4650


@pytest.mark.asyncio
async def test_known_catalog_pricing_calculates_actual_cost_when_provider_cost_missing() -> None:
    with make_session() as session:
        add_models(session)
        history = CouncilHistoryService(
            repository=CouncilRunRepository(session),
            event_bus=EventBus(),
        )
        detail = await history.record_success(
            request=request(),
            result=result("cost_calculated_run", provider_costs=False),
        )
        session.commit()

        # 1000*10 + 200*30 + 900*8 + 250*24 + 2000*10 + 300*30, all per million.
        expected = (10000 + 6000 + 7200 + 6000 + 20000 + 9000) / 1_000_000
        assert detail.actual_cost_status == "known"
        assert detail.actual_cost_usd == pytest.approx(expected)

        sources = list(
            session.scalars(
                select(CouncilCostLedgerModel.cost_source).where(
                    CouncilCostLedgerModel.run_id == "cost_calculated_run"
                )
            ).all()
        )
        assert sources == ["calculated", "calculated", "calculated"]


@pytest.mark.asyncio
async def test_reservation_enforces_monthly_run_quota_before_parallel_launch() -> None:
    with make_session() as session:
        add_models(session)
        control = CouncilControlService(session=session, event_bus=EventBus())
        await control.update_budget_policy(
            None,
            CouncilBudgetPolicyUpdate(monthly_run_limit=1),
        )
        session.commit()

        authorized, _ = await control.authorize(request(), None)
        assert authorized.cost_reservation_id

        second = control.estimate(request(), None)
        assert second.decision == "blocked"
        assert "workspace_monthly_run_quota_exceeded" in second.reasons
        usage = control.get_usage(None)
        assert usage.completed_runs == 0
        assert usage.reserved_runs == 1


@pytest.mark.asyncio
async def test_budget_aware_router_returns_cheaper_distinct_composition() -> None:
    with make_session() as session:
        add_models(session)
        control = CouncilControlService(session=session, event_bus=EventBus())
        await control.update_budget_policy(
            None,
            CouncilBudgetPolicyUpdate(
                routing_mode="advisory",
                routing_min_savings_percent=10,
            ),
        )
        session.commit()

        plan = control.route(request(), None)

        assert plan.available is True
        assert plan.routed_estimate is not None
        recommended = [member.model for member in plan.recommended_members]
        assert len(set(recommended)) == 2
        assert set(recommended) == {"cheap-a", "cheap-b"}
        assert plan.synthesizer_model in {"cheap-a", "cheap-b"}
        assert len(plan.changes) >= 2
        assert plan.original_estimate.estimated_cost_usd is not None
        assert plan.routed_estimate.estimated_cost_usd is not None
        assert (
            plan.routed_estimate.estimated_cost_usd
            < plan.original_estimate.estimated_cost_usd
        )
        assert plan.estimated_savings_usd is not None
        assert plan.estimated_savings_usd > 0
