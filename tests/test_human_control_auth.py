from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.control_center.auth import HumanControlAuthService
from backend.control_center.auth_schemas import (
    HumanControlApiTokenCreate,
    HumanControlApiTokenRevoke,
    HumanControlBreakGlassActivate,
    HumanControlBreakGlassDecision,
    HumanControlBreakGlassRequestCreate,
    HumanControlIdentityCreate,
    HumanControlLoginRequest,
    HumanControlSessionRevokeRequest,
)
from backend.control_center.models import (
    HumanControlApiTokenModel,
    HumanControlIdentityModel,
    HumanControlSessionModel,
)
from backend.control_center.service import HumanControlConflict, HumanControlError
from backend.core.events import EventBus
from backend.database import models as database_models
from backend.database.base import Base
from backend.orchestration import models as orchestration_models  # noqa: F401
from backend.task_engine import models as task_models  # noqa: F401


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
        session.add(database_models.WorkspaceModel(id="workspace_auth", name="Auth Test"))
    return scope


async def create_identity(service: HumanControlAuthService, actor_id: str, username: str):
    return await service.create_identity(
        HumanControlIdentityCreate(
            actor_id=actor_id,
            username=username,
            display_name=username.title(),
            password="correct-horse-battery-staple",
            created_by="test",
        )
    )


@pytest.mark.asyncio
async def test_password_login_creates_hashed_session_and_authenticates() -> None:
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    identity = await create_identity(service, "alice", "alice")
    login = await service.login(
        HumanControlLoginRequest(
            username="ALICE",
            password="correct-horse-battery-staple",
            workspace_id="workspace_auth",
        )
    )
    assert login["access_token"].startswith("hc_sess_")
    principal = service.authenticate(login["access_token"])
    assert principal["actor_id"] == "alice"
    with scope() as session:
        row = session.get(HumanControlIdentityModel, identity["id"])
        session_row = session.scalar(select(HumanControlSessionModel))
        assert row.password_hash != "correct-horse-battery-staple"
        assert session_row.token_hash != login["access_token"]


@pytest.mark.asyncio
async def test_session_revocation_invalidates_bearer() -> None:
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    await create_identity(service, "alice", "alice")
    login = await service.login(HumanControlLoginRequest(username="alice", password="correct-horse-battery-staple"))
    await service.revoke_session(
        login["session"]["id"],
        HumanControlSessionRevokeRequest(actor_id="owner", reason="Test revoke"),
    )
    with pytest.raises(HumanControlError):
        service.authenticate(login["access_token"])


@pytest.mark.asyncio
async def test_api_token_is_returned_once_and_can_be_revoked() -> None:
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    identity = await create_identity(service, "robot", "robot")
    created = await service.create_api_token(
        HumanControlApiTokenCreate(
            identity_id=identity["id"],
            workspace_id="workspace_auth",
            name="automation",
            scopes=["human_control.view"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            created_by="owner",
        )
    )
    assert created["token"].startswith("hc_api_")
    assert service.authenticate(created["token"])["auth_method"] == "api_token"
    with scope() as session:
        row = session.get(HumanControlApiTokenModel, created["id"])
        assert row.token_hash != created["token"]
    await service.revoke_api_token(
        created["id"], HumanControlApiTokenRevoke(actor_id="owner", reason="Done")
    )
    with pytest.raises(HumanControlError):
        service.authenticate(created["token"])


@pytest.mark.asyncio
async def test_break_glass_requires_distinct_approver_and_creates_scoped_session() -> None:
    scope = make_scope()
    service = HumanControlAuthService(event_bus=EventBus(), session_factory=scope)
    requester = await create_identity(service, "alice", "alice")
    approver = await create_identity(service, "bob", "bob")
    grant = await service.request_break_glass(
        HumanControlBreakGlassRequestCreate(
            requested_by_identity_id=requester["id"],
            workspace_id="workspace_auth",
            reason="Production recovery requires temporary override.",
            scopes=["human_control.override"],
            idempotency_key="bg-001",
        )
    )
    with pytest.raises(HumanControlConflict):
        await service.decide_break_glass(
            grant["id"],
            HumanControlBreakGlassDecision(
                approver_identity_id=requester["id"],
                approve=True,
                reason="Self approval must fail",
            ),
        )
    approved = await service.decide_break_glass(
        grant["id"],
        HumanControlBreakGlassDecision(
            approver_identity_id=approver["id"],
            approve=True,
            reason="Emergency request independently verified.",
        ),
    )
    activated = await service.activate_break_glass(
        HumanControlBreakGlassActivate(activation_token=approved["activation_token"])
    )
    principal = service.authenticate(activated["access_token"])
    assert principal["auth_method"] == "break_glass"
    assert principal["scopes"] == ["human_control.override"]
