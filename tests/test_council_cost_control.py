from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.core.events import EventBus
from backend.council.control import (
    CouncilControlService,
    CouncilCostApprovalInvalidError,
)
from backend.council.schemas import (
    CouncilBudgetPolicyUpdate,
    CouncilMemberRequest,
    CouncilPresetCreate,
    CouncilRole,
    CouncilRunRequest,
)
from backend.database.base import Base
from backend.database.models import ModelConfigModel


def make_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def request() -> CouncilRunRequest:
    return CouncilRunRequest(
        question="Сравни два варианта архитектуры и оцени риски.",
        mode="code",
        members=[
            CouncilMemberRequest(
                provider="openrouter",
                model="free-model:free",
                role=CouncilRole.ANALYST,
            ),
            CouncilMemberRequest(
                provider="openrouter",
                model="paid-model",
                role=CouncilRole.CRITIC,
            ),
        ],
        synthesizer_provider="openrouter",
        synthesizer_model="paid-model",
    )


@pytest.mark.asyncio
async def test_cost_preflight_approval_is_one_time_and_fingerprint_bound() -> None:
    with make_session() as session:
        session.add_all(
            [
                ModelConfigModel(
                    provider="openrouter",
                    slug="free-model:free",
                    display_name="Free",
                    metadata_json={"billing": "free"},
                ),
                ModelConfigModel(
                    provider="openrouter",
                    slug="paid-model",
                    display_name="Paid",
                    metadata_json={
                        "billing": "metered",
                        "pricing": {
                            "input_per_million_usd": 2.0,
                            "output_per_million_usd": 8.0,
                        },
                    },
                ),
            ]
        )
        session.commit()
        service = CouncilControlService(session=session, event_bus=EventBus())
        await service.update_budget_policy(
            None,
            CouncilBudgetPolicyUpdate(approval_threshold_usd=0.001),
        )
        session.commit()

        estimate = service.estimate(request(), None)
        assert estimate.estimate_status == "known"
        assert estimate.estimated_cost_usd is not None
        assert estimate.estimated_cost_usd > 0
        assert estimate.decision == "approval_required"

        approval = await service.approve(request(), None)
        authorized, _ = await service.authorize(
            request().model_copy(update={"cost_approval_token": approval.token}),
            None,
        )
        assert authorized.cost_approval_id == approval.approval_id
        assert authorized.estimated_cost_usd == estimate.estimated_cost_usd

        with pytest.raises(CouncilCostApprovalInvalidError):
            await service.authorize(
                request().model_copy(update={"cost_approval_token": approval.token}),
                None,
            )


@pytest.mark.asyncio
async def test_unknown_metered_price_requires_approval_by_default() -> None:
    with make_session() as session:
        session.add_all(
            [
                ModelConfigModel(
                    provider="openrouter",
                    slug="free-model:free",
                    display_name="Free",
                    metadata_json={"billing": "free"},
                ),
                ModelConfigModel(
                    provider="openrouter",
                    slug="paid-model",
                    display_name="Paid",
                    metadata_json={"billing": "metered"},
                ),
            ]
        )
        session.commit()
        service = CouncilControlService(session=session, event_bus=EventBus())

        estimate = service.estimate(request(), None)
        assert estimate.estimate_status in {"partial", "unknown"}
        assert "paid-model" in estimate.unknown_models
        assert estimate.decision == "approval_required"
        assert estimate.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_presets_keep_roles_and_separate_chair() -> None:
    with make_session() as session:
        service = CouncilControlService(session=session, event_bus=EventBus())
        created = await service.create_preset(
            CouncilPresetCreate(
                name="Code Review",
                description="Два независимых взгляда и отдельный председатель.",
                mode="code",
                members=request().members,
                synthesizer_provider="openrouter",
                synthesizer_model="paid-model",
                member_timeout_seconds=90,
            ),
            None,
        )
        session.commit()

        listed = service.list_presets(None)
        assert len(listed.items) == 1
        assert listed.items[0].id == created.id
        assert listed.items[0].members[0].role == CouncilRole.ANALYST
        assert listed.items[0].members[1].role == CouncilRole.CRITIC
        assert listed.items[0].synthesizer_model == "paid-model"
        assert listed.items[0].member_timeout_seconds == 90
