from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.database.base import Base
from backend.repositories.chats import ChatRepository
from backend.repositories.projects import ProjectRepository


def create_test_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_project_repository_create_and_find() -> None:
    with create_test_session() as session:
        repo = ProjectRepository(session)
        project = repo.create(
            name="Test Project",
            description="Description",
        )
        session.commit()

        found = repo.find_by_name("Test Project")

        assert found is not None
        assert found.id == project.id
        assert found.description == "Description"


def test_chat_repository_with_messages() -> None:
    with create_test_session() as session:
        chat_repo = ChatRepository(session)
        chat = chat_repo.create(
            title="Test Chat",
            mode="universal",
        )
        chat_repo.add_message(
            chat_id=chat.id,
            role="user",
            content="Hello",
        )
        chat_repo.add_message(
            chat_id=chat.id,
            role="assistant",
            content="Hi",
        )
        session.commit()

        loaded = chat_repo.get_with_messages(chat.id)

        assert loaded is not None
        assert len(loaded.messages) == 2
        assert loaded.messages[0].content == "Hello"
