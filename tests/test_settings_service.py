import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.settings import SettingsRepository
from backend.services.settings import SettingsService


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.asyncio
async def test_upsert_and_list_settings() -> None:
    with create_test_session() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=EventBus(),
        )

        first = await service.set_setting(
            scope="global",
            key="theme",
            value="dark",
        )
        second = await service.set_setting(
            scope="global",
            key="theme",
            value="light",
        )
        session.commit()

        items = service.list_settings(scope="global")

        assert first["id"] == second["id"]
        assert items[0]["key"] == "theme"
        assert items[0]["value"] == "light"


@pytest.mark.asyncio
async def test_delete_setting() -> None:
    with create_test_session() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=EventBus(),
        )

        created = await service.set_setting(
            scope="user",
            key="language",
            value="ru",
        )
        session.commit()

        deleted = await service.delete_setting(created["id"])
        session.commit()

        assert deleted is True
        assert service.list_settings(scope="user") == []


def test_rejects_unknown_scope() -> None:
    with create_test_session() as session:
        service = SettingsService(
            repository=SettingsRepository(session),
            event_bus=EventBus(),
        )

        with pytest.raises(ValueError):
            service.list_settings(scope="invalid")
