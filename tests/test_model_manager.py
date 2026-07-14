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
        default_model="deepseek/deepseek-chat-v3-0324",
        max_tokens=1000,
        temperature=0.4,
        request_timeout_seconds=60,
    )

    with Session(engine) as session:
        manager = ModelManager(ModelRepository(session), EventBus(), settings)
        manager.seed_defaults()
        session.commit()

        assert len(manager.list_models(enabled_only=True)) == 3
        assert manager.resolve_model(require_json=True)["supports_json"] is True
