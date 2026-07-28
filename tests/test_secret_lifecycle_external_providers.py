from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.base import Base
from backend.database import models as database_models  # noqa: F401
from backend.secrets.models import SecretHealthAlertModel, SecretRotationRunModel
from backend.secrets.providers import ProviderRegistry, StoredSecretMaterial, material_hash
from backend.secrets.schemas import (
    SecretCreate,
    SecretLifecycleScanRequest,
    SecretProviderCreate,
    SecretProviderType,
    SecretRotationMode,
    SecretRotationPolicyUpsert,
    SecretRotationRunCreate,
    SecretRotationRunDecision,
)
from backend.secrets.service import SecretManagerService, utc_now


class FakeEventBus:
    async def publish(self, *args, **kwargs) -> None:
        return None


class FakeWritableExternalProvider:
    key = "fake_external"
    read_only = False

    def __init__(self) -> None:
        self.values: dict[str, str] = {"remote/key": "initial"}

    def health(self, config):
        return True, "ready"

    def store(self, *, provider_ref, value, config):
        del config
        self.values[provider_ref] = value
        return StoredSecretMaterial(
            encrypted_payload=None,
            provider_version="remote-v2",
            material_hash=material_hash(value),
        )

    def resolve(self, *, provider_ref, encrypted_payload, provider_version, config):
        del encrypted_payload, provider_version, config
        return self.values[provider_ref]


def make_manager():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, class_=Session)

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

    registry = ProviderRegistry()
    fake = FakeWritableExternalProvider()
    registry.register(fake)
    return SecretManagerService(event_bus=FakeEventBus(), session_factory=scope, provider_registry=registry), factory, fake


@pytest.mark.asyncio
async def test_external_secret_can_register_existing_remote_material_without_plaintext() -> None:
    manager, _factory, fake = make_manager()
    provider = await manager.create_provider(
        SecretProviderCreate(
            provider_key="remote",
            name="Remote provider",
            provider_type=SecretProviderType.EXTERNAL,
            adapter_key="fake_external",
            read_only=False,
            created_by="owner",
        )
    )
    assert provider["adapter_key"] == "fake_external"
    secret = await manager.create_secret(
        SecretCreate(
            provider_key="remote",
            secret_key="remote/key",
            provider_ref="remote/key",
            display_name="Existing remote secret",
            created_by="owner",
        )
    )
    assert secret["current_version"] == 1
    assert fake.values["remote/key"] == "initial"


@pytest.mark.asyncio
async def test_managed_random_rotation_waits_for_human_approval_and_never_persists_value() -> None:
    manager, factory, fake = make_manager()
    await manager.create_provider(
        SecretProviderCreate(
            provider_key="remote",
            name="Remote provider",
            provider_type=SecretProviderType.EXTERNAL,
            adapter_key="fake_external",
            read_only=False,
            created_by="owner",
        )
    )
    secret = await manager.create_secret(
        SecretCreate(
            provider_key="remote",
            secret_key="remote/key",
            provider_ref="remote/key",
            display_name="Managed secret",
            created_by="owner",
        )
    )
    policy = await manager.upsert_rotation_policy(
        secret["id"],
        SecretRotationPolicyUpsert(
            rotation_mode=SecretRotationMode.MANAGED_RANDOM,
            interval_seconds=60,
            auto_rotate_enabled=True,
            require_human_approval=True,
            actor_id="owner",
        ),
    )
    assert policy["rotation_mode"] == "managed_random"
    run = await manager.create_rotation_run(
        secret["id"],
        SecretRotationRunCreate(
            actor_id="owner",
            reason="scheduled rotation test",
            execute_now=True,
        ),
    )
    assert run["status"] == "proposed"
    with factory() as session:
        row = session.scalar(select(SecretRotationRunModel))
        assert row is not None
        assert "initial" not in repr(row.metadata_json)
        assert row.new_version == 0
    assert fake.values["remote/key"] == "initial"

    completed = await manager.decide_rotation_run(
        run["id"],
        SecretRotationRunDecision(
            actor_id="second-operator",
            approve=True,
            reason="approved in unit test",
        ),
    )
    assert completed["status"] == "completed"
    assert completed["new_version"] == 2
    assert fake.values["remote/key"] != "initial"
    with factory() as session:
        row = session.get(SecretRotationRunModel, run["id"])
        assert row is not None
        assert fake.values["remote/key"] not in repr(row.metadata_json)


@pytest.mark.asyncio
async def test_lifecycle_scan_creates_health_alert_for_expired_secret() -> None:
    manager, factory, _fake = make_manager()
    await manager.create_provider(
        SecretProviderCreate(
            provider_key="remote",
            name="Remote provider",
            provider_type=SecretProviderType.EXTERNAL,
            adapter_key="fake_external",
            read_only=False,
            created_by="owner",
        )
    )
    secret = await manager.create_secret(
        SecretCreate(
            provider_key="remote",
            secret_key="remote/key",
            provider_ref="remote/key",
            display_name="Expiring secret",
            expires_at=utc_now() - timedelta(seconds=1),
            created_by="owner",
        )
    )
    result = await manager.scan_lifecycle(
        SecretLifecycleScanRequest(actor_id="system", check_providers=True, auto_rotate=False)
    )
    assert result["health_checks"] == 1
    with factory() as session:
        alert = session.scalar(
            select(SecretHealthAlertModel).where(
                SecretHealthAlertModel.secret_id == secret["id"],
                SecretHealthAlertModel.status == "open",
            )
        )
        assert alert is not None
        assert alert.severity == "critical"
        assert alert.alert_key == "secret_expired"


def test_registry_contains_optional_external_provider_adapters_without_importing_sdks() -> None:
    keys = ProviderRegistry().keys()
    assert "hashicorp_vault_kv2" in keys
    assert "aws_secrets_manager" in keys
    assert "azure_key_vault" in keys
    assert "gcp_secret_manager" in keys


@pytest.mark.asyncio
async def test_lifecycle_monitor_starts_and_stops_cleanly() -> None:
    manager, _factory, _fake = make_manager()
    await manager.start_lifecycle_monitor()
    assert manager.status()["lifecycle_monitor_running"] is True
    await manager.shutdown_lifecycle_monitor()
    assert manager.status()["lifecycle_monitor_running"] is False
