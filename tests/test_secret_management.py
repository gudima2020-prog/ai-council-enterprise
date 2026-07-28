from __future__ import annotations

import os

import pytest
from pydantic import SecretStr

from backend.secrets.providers import EnvironmentSecretProvider, ProviderRegistry
from backend.secrets.schemas import SecretCreate, validate_public_provider_config
from backend.secrets.references import parse_secret_reference


def test_secret_reference_parser() -> None:
    assert parse_secret_reference("secret://env/OPENAI_API_KEY") == (
        "env",
        "OPENAI_API_KEY",
    )
    assert parse_secret_reference("secret://windows-dpapi/openai/main") == (
        "windows-dpapi",
        "openai/main",
    )


def test_environment_provider_resolves_without_persisting_plaintext(monkeypatch) -> None:
    monkeypatch.setenv("AI_TEST_SECRET", "not-stored-in-db")
    provider = EnvironmentSecretProvider()
    assert provider.resolve(
        provider_ref="AI_TEST_SECRET",
        encrypted_payload=None,
        provider_version="external",
        config={},
    ) == "not-stored-in-db"


def test_provider_config_rejects_secret_material() -> None:
    with pytest.raises(ValueError):
        validate_public_provider_config({"api_key": "must-not-be-here"})
    assert validate_public_provider_config(
        {"endpoint": "https://vault.internal", "credential_ref": "secret://env/VAULT_TOKEN_REF"}
    )
    assert validate_public_provider_config(
        {"address": "https://vault.internal", "auth_env": "VAULT_TOKEN"}
    )


def test_secret_value_is_masked_by_pydantic() -> None:
    request = SecretCreate(
        provider_key="windows-dpapi",
        secret_key="provider/key",
        display_name="Provider key",
        value=SecretStr("super-secret-value"),
        created_by="owner",
    )
    dumped = request.model_dump()
    assert str(dumped["value"]) == "**********"
    assert "super-secret-value" not in repr(request)


def test_provider_registry_has_safe_builtin_adapters() -> None:
    registry = ProviderRegistry()
    assert "env" in registry.keys()
    assert "windows_dpapi" in registry.keys()


class _FakeExternalAdapter:
    key = "fake-vault"
    read_only = True

    def health(self, config):
        return True, "ready"

    def store(self, **kwargs):
        raise RuntimeError("read-only")

    def resolve(self, *, provider_ref, encrypted_payload, provider_version, config):
        del encrypted_payload, provider_version, config
        return f"resolved:{provider_ref}"


def test_external_provider_adapter_can_be_registered() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeExternalAdapter())
    adapter = registry.get("fake-vault")
    assert adapter.resolve(
        provider_ref="kv/app/key",
        encrypted_payload=None,
        provider_version="remote",
        config={},
    ) == "resolved:kv/app/key"
