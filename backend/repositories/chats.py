from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.database.models import ChatMessageModel, ChatModel
from backend.repositories.base import Repository


class ChatRepository(Repository[ChatModel]):
    def __init__(self, session: Session) -> None:
        super().__init__(session, ChatModel)

    def create(
        self,
        *,
        title: str,
        mode: str = "universal",
        model_name: str | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
    ) -> ChatModel:
        chat = ChatModel(
            title=title.strip(),
            mode=mode,
            model_name=model_name,
            project_id=project_id,
            workspace_id=workspace_id,
        )
        return self.add(chat)

    def get_with_messages(self, chat_id: str) -> ChatModel | None:
        statement = (
            select(ChatModel)
            .options(selectinload(ChatModel.messages))
            .where(ChatModel.id == chat_id)
        )
        return self.session.scalar(statement)

    def list_by_workspace(
        self,
        workspace_id: str,
        *,
        limit: int = 100,
    ) -> list[ChatModel]:
        statement = (
            select(ChatModel)
            .where(ChatModel.workspace_id == workspace_id)
            .order_by(ChatModel.updated_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement).all())

    def add_message(
        self,
        *,
        chat_id: str,
        role: str,
        content: str,
        model_name: str | None = None,
    ) -> ChatMessageModel:
        message = ChatMessageModel(
            chat_id=chat_id,
            role=role,
            content=content,
            model_name=model_name,
        )
        self.session.add(message)
        self.session.flush()
        return message
