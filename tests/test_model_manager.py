from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.models import ModelRepository
from backend.services.model_manager import ModelManager


def test_seed_and_resolve_model() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    settings = AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )

    with Session(engine) as session:
        manager = ModelManager(ModelRepository(session), EventBus(), settings)
        manager.seed_defaults()
        session.commit()

        enabled_models = manager.list_models(enabled_only=True)
        assert len(enabled_models) == 5
        assert [model["slug"] for model in enabled_models[:3]] == [
            "openrouter/free",
            "openai/gpt-oss-20b:free",
            "google/gemma-4-31b-it:free",
        ]
        assert all(
            model["metadata"]["billing"] == "free"
            for model in enabled_models[:3]
        )
        assert manager.resolve_model(require_json=True)["supports_json"] is True


def test_seed_hides_only_unchanged_legacy_defaults() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    settings = AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )

    with Session(engine) as session:
        repository = ModelRepository(session)
        repository.upsert(
            provider="openrouter",
            slug="deepseek/deepseek-chat-v3-0324",
            display_name="DeepSeek Chat V3",
            enabled=True,
            priority=10,
            context_window=163840,
            max_output_tokens=8192,
            supports_tools=False,
            supports_vision=False,
            supports_json=True,
            metadata_json={},
        )
        repository.upsert(
            provider="openrouter",
            slug="qwen/qwen3-30b-a3b",
            display_name="Моя настроенная Qwen",
            enabled=True,
            priority=20,
            context_window=131072,
            max_output_tokens=8192,
            supports_tools=True,
            supports_vision=False,
            supports_json=True,
            metadata_json={},
        )
        repository.upsert(
            provider="openrouter",
            slug="google/gemini-2.0-flash-001",
            display_name="Gemini 2.0 Flash",
            enabled=True,
            priority=30,
            context_window=1048576,
            max_output_tokens=8192,
            supports_tools=True,
            supports_vision=True,
            supports_json=True,
            metadata_json={"user_managed": True},
        )

        manager = ModelManager(repository, EventBus(), settings)
        manager.seed_defaults()
        session.commit()

        retired = manager.get_model("deepseek/deepseek-chat-v3-0324")
        customized = manager.get_model("qwen/qwen3-30b-a3b")
        user_managed = manager.get_model("google/gemini-2.0-flash-001")

        assert retired is not None
        assert retired["enabled"] is True
        assert retired["metadata"]["retired_default"] is True
        assert retired["metadata"]["catalog_hidden"] is True
        assert customized is not None
        assert customized["enabled"] is True
        assert customized["display_name"] == "Моя настроенная Qwen"
        assert user_managed is not None
        assert user_managed["enabled"] is True
        assert user_managed["metadata"]["user_managed"] is True


def test_seed_repairs_p2_001_2_disabled_legacy_default() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    settings = AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )

    with Session(engine) as session:
        repository = ModelRepository(session)
        repository.upsert(
            provider="openrouter",
            slug="deepseek/deepseek-chat-v3-0324",
            display_name="DeepSeek Chat V3",
            enabled=False,
            priority=10,
            context_window=163840,
            max_output_tokens=8192,
            supports_tools=False,
            supports_vision=False,
            supports_json=True,
            metadata_json={
                "retired_default": True,
                "retired_in": "P2-001.2",
                "replacement": "openrouter/free",
            },
        )

        manager = ModelManager(repository, EventBus(), settings)
        manager.seed_defaults()
        session.commit()

        repaired = manager.get_model("deepseek/deepseek-chat-v3-0324")

        assert repaired is not None
        assert repaired["enabled"] is True
        assert repaired["metadata"]["catalog_hidden"] is True
        assert repaired["metadata"]["retired_in"] == "P2-001.3"


def test_svrtr_models_are_enabled_only_when_provider_is_configured(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    settings = AppSettings(
        app_name="Test",
        app_version="0",
        environment="test",
        debug=True,
        host="127.0.0.1",
        port=8000,
        openrouter_api_key="",
        default_provider="openrouter",
        default_model="openrouter/free",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )
    monkeypatch.setenv("SVRTR_API_KEY", "test-only-key")
    with Session(engine) as session:
        manager = ModelManager(ModelRepository(session), EventBus(), settings)
        manager.seed_defaults()
        session.commit()
        sonnet = manager.get_model("claude-sonnet-5")
        assert sonnet is not None
        assert sonnet["provider"] == "svrtr"
        assert sonnet["enabled"] is True
        assert sonnet["metadata"]["pricing"]["input_per_million_usd"] == 2.0
        assert sonnet["metadata"]["pricing"]["valid_until"] == "2026-08-31"
