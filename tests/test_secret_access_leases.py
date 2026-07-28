from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.secrets.injection import REDACTED_SECRET, SecretLeaseAccessor
from backend.secrets.models import SecretLeaseModel
from backend.secrets.schemas import (
    SecretAccessEnforcementMode,
    SecretAccessPolicyCreate,
    SecretAccessSettingsUpsert,
    SecretCreate,
    SecretLeaseCreate,
    SecretPolicyEffect,
    SecretResolveContext,
)
from backend.secrets.service import SecretConflict, SecretManagerService


class FakeEventBus:
    async def publish(self, *args, **kwargs) -> None:
        return None


def make_manager() -> tuple[SecretManagerService, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
        class_=Session,
    )

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

    return SecretManagerService(event_bus=FakeEventBus(), session_factory=scope), factory


async def create_env_secret(
    manager: SecretManagerService,
    monkeypatch,
    *,
    key: str = "AI_STUDIO_TEST_SECRET",
    value: str = "super-secret-runtime-value",
) -> dict:
    monkeypatch.setenv(key, value)
    manager.seed_builtin_providers()
    manager.seed_access_defaults()
    return await manager.create_secret(
        SecretCreate(
            provider_key="env",
            secret_key=key,
            provider_ref=key,
            display_name="Test secret",
            created_by="owner",
        )
    )


@pytest.mark.asyncio
async def test_single_use_lease_never_persists_plaintext_token_or_value(monkeypatch) -> None:
    manager, factory = make_manager()
    secret = await create_env_secret(manager, monkeypatch)

    lease = await manager.issue_lease(
        SecretLeaseCreate(
            reference=secret["reference"],
            actor_id="tool-agent",
            consumer_type="tool",
            consumer_key="test.tool",
            purpose="unit test",
            created_by="tool-agent",
            max_uses=1,
        )
    )
    token = lease["lease_token"]
    context = SecretResolveContext(
        actor_id="tool-agent",
        purpose="unit test",
        consumer_type="tool",
        consumer_key="test.tool",
    )
    assert await manager.resolve_lease(token, context) == "super-secret-runtime-value"

    with factory() as session:
        row = session.scalar(select(SecretLeaseModel))
        assert row is not None
        assert row.status == "consumed"
        assert row.token_hash != token
        assert "super-secret-runtime-value" not in repr(row.metadata_json)

    with pytest.raises(SecretConflict):
        await manager.resolve_lease(token, context)


@pytest.mark.asyncio
async def test_enforce_mode_default_deny_requires_matching_allow_policy(monkeypatch) -> None:
    manager, _ = make_manager()
    secret = await create_env_secret(manager, monkeypatch)

    await manager.upsert_access_settings(
        SecretAccessSettingsUpsert(
            enabled=True,
            enforcement_mode=SecretAccessEnforcementMode.ENFORCE,
            default_effect=SecretPolicyEffect.DENY,
            actor_id="owner",
        )
    )

    with pytest.raises(SecretConflict):
        await manager.issue_lease(
            SecretLeaseCreate(
                reference=secret["reference"],
                actor_id="tool-agent",
                consumer_type="tool",
                consumer_key="allowed.tool",
                purpose="policy test",
                created_by="tool-agent",
            )
        )

    await manager.create_access_policy(
        SecretAccessPolicyCreate(
            policy_key="allow-test-tool",
            name="Allow test tool",
            effect=SecretPolicyEffect.ALLOW,
            actions=["lease"],
            secret_patterns=[secret["reference"]],
            consumer_types=["tool"],
            consumer_keys=["allowed.tool"],
            max_lease_seconds=30,
            max_uses=1,
            created_by="owner",
        )
    )

    lease = await manager.issue_lease(
        SecretLeaseCreate(
            reference=secret["reference"],
            actor_id="tool-agent",
            consumer_type="tool",
            consumer_key="allowed.tool",
            purpose="policy test",
            created_by="tool-agent",
            ttl_seconds=120,
            max_uses=5,
        )
    )
    assert lease["max_uses"] == 1


@pytest.mark.asyncio
async def test_ephemeral_accessor_redacts_secret_from_runtime_output(monkeypatch) -> None:
    manager, _ = make_manager()
    secret = await create_env_secret(manager, monkeypatch)
    accessor = SecretLeaseAccessor(
        manager=manager,
        bindings={"api_key": secret["reference"]},
        context=SecretResolveContext(
            actor_id="gateway",
            purpose="gateway test",
            consumer_type="gateway",
            consumer_key="fake-provider",
        ),
    )

    value = await accessor.get("api_key")
    assert value == "super-secret-runtime-value"
    redacted = accessor.redact_object(
        {
            "message": f"provider error: {value}",
            "nested": [value],
        }
    )
    assert redacted["message"] == f"provider error: {REDACTED_SECRET}"
    assert redacted["nested"] == [REDACTED_SECRET]
    assert value not in repr(redacted)
    await accessor.close()
