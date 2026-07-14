from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.config import AppSettings
from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.models import ModelRepository
from backend.services.model_manager import ModelManager


def make_settings() -> AppSettings:
    return AppSettings(
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


def test_default_models_can_be_seeded() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        ModelManager(
            ModelRepository(session),
            EventBus(),
            make_settings(),
        ).seed_defaults()
        session.commit()

        assert len(ModelRepository(session).list_enabled()) >= 1
