import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.core.events import EventBus
from backend.database.base import Base
from backend.repositories.memory import MemoryRepository
from backend.services.memory import MemoryService


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


@pytest.mark.asyncio
async def test_create_and_search_memory_item() -> None:
    with create_test_session() as session:
        service = MemoryService(
            repository=MemoryRepository(session),
            event_bus=EventBus(),
        )

        created = await service.create_item(
            scope="project",
            title="ENA short analysis",
            content="Resistance was rejected on the 15m timeframe.",
            source_type="crypto",
            importance=0.8,
            tags=["ENA", "short"],
        )
        session.commit()

        results = service.search(
            query="Resistance",
            scope="project",
        )

        assert created["scope"] == "project"
        assert len(results) == 1
        assert results[0]["title"] == "ENA short analysis"


@pytest.mark.asyncio
async def test_update_and_delete_memory_item() -> None:
    with create_test_session() as session:
        service = MemoryService(
            repository=MemoryRepository(session),
            event_bus=EventBus(),
        )

        created = await service.create_item(
            scope="long_term",
            title="Initial",
            content="Original content",
            source_type="manual",
        )
        session.commit()

        updated = await service.update_item(
            memory_item_id=created["id"],
            title="Updated",
            importance=1.0,
        )
        session.commit()

        deleted = await service.delete_item(created["id"])
        session.commit()

        assert updated is not None
        assert updated["title"] == "Updated"
        assert updated["importance"] == 1.0
        assert deleted is True


def test_rejects_unknown_scope() -> None:
    with create_test_session() as session:
        service = MemoryService(
            repository=MemoryRepository(session),
            event_bus=EventBus(),
        )

        with pytest.raises(ValueError):
            service.list_items(scope="unknown")
