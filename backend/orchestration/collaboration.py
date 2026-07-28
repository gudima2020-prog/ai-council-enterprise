from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.events import Event, EventBus
from backend.database.session import session_scope
from backend.orchestration.collaboration_schemas import (
    AgentDelegationStatus,
    AgentMessageCreateRequest,
    AgentMessageType,
    CollaborationDirectiveDelegation,
    CollaborationDirectiveMessage,
    ContextEntryUpsertRequest,
    ContextScopeType,
    ConversationCreateRequest,
    ConversationStatus,
    DelegationCreateRequest,
)
from backend.orchestration.models import (
    AgentConversationModel,
    AgentDelegationModel,
    AgentMessageModel,
    AgentProfileModel,
    ExecutionContextEntryModel,
    ExecutionPlanModel,
    ExecutionPlanStepModel,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SessionContextFactory = Callable[[], AbstractContextManager[Session]]


class CollaborationError(ValueError):
    pass


class CollaborationNotFound(CollaborationError):
    pass


class ContextVersionConflict(CollaborationError):
    pass


class AgentCollaborationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_plan(self, plan_id: str) -> ExecutionPlanModel | None:
        return self.session.get(ExecutionPlanModel, plan_id)

    def get_step(self, step_id: str) -> ExecutionPlanStepModel | None:
        return self.session.get(ExecutionPlanStepModel, step_id)

    def get_agent(self, agent_id: str | None) -> AgentProfileModel | None:
        if agent_id is None:
            return None
        return self.session.get(AgentProfileModel, agent_id)

    def create_conversation(
        self,
        *,
        plan: ExecutionPlanModel,
        request: ConversationCreateRequest,
    ) -> AgentConversationModel:
        row = AgentConversationModel(
            workspace_id=plan.workspace_id,
            plan_id=plan.id,
            topic_key=request.topic_key,
            title=request.title.strip(),
            created_by_agent_id=request.created_by_agent_id,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_conversation(
        self,
        conversation_id: str,
    ) -> AgentConversationModel | None:
        return self.session.get(AgentConversationModel, conversation_id)

    def get_conversation_by_topic(
        self,
        plan_id: str,
        topic_key: str,
    ) -> AgentConversationModel | None:
        statement = select(AgentConversationModel).where(
            AgentConversationModel.plan_id == plan_id,
            AgentConversationModel.topic_key == topic_key,
        )
        return self.session.scalar(statement)

    def list_conversations(
        self,
        plan_id: str,
    ) -> list[AgentConversationModel]:
        statement = (
            select(AgentConversationModel)
            .where(AgentConversationModel.plan_id == plan_id)
            .order_by(AgentConversationModel.created_at.asc())
        )
        return list(self.session.scalars(statement).all())

    def next_message_sequence(self, conversation_id: str) -> int:
        statement = select(
            func.coalesce(func.max(AgentMessageModel.sequence), 0) + 1
        ).where(AgentMessageModel.conversation_id == conversation_id)
        return int(self.session.scalar(statement) or 1)

    def create_message(
        self,
        *,
        conversation: AgentConversationModel,
        request: AgentMessageCreateRequest,
    ) -> AgentMessageModel:
        row = AgentMessageModel(
            conversation_id=conversation.id,
            plan_id=conversation.plan_id,
            sequence=self.next_message_sequence(conversation.id),
            sender_agent_id=request.sender_agent_id,
            recipient_agent_id=request.recipient_agent_id,
            message_type=request.message_type.value,
            subject=request.subject.strip(),
            content_json=request.content,
            correlation_id=request.correlation_id,
            reply_to_message_id=request.reply_to_message_id,
            priority=request.priority.value,
        )
        self.session.add(row)
        conversation.updated_at = utc_now()
        self.session.flush()
        return row

    def get_message(self, message_id: str) -> AgentMessageModel | None:
        return self.session.get(AgentMessageModel, message_id)

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[AgentMessageModel]:
        statement = select(AgentMessageModel).where(
            AgentMessageModel.conversation_id == conversation_id
        )
        if after_sequence is not None:
            statement = statement.where(
                AgentMessageModel.sequence > after_sequence
            )
        statement = statement.order_by(
            AgentMessageModel.sequence.asc()
        ).limit(limit)
        return list(self.session.scalars(statement).all())

    def get_context_entry(
        self,
        *,
        plan_id: str,
        scope_type: str,
        scope_id: str,
        key: str,
    ) -> ExecutionContextEntryModel | None:
        statement = select(ExecutionContextEntryModel).where(
            ExecutionContextEntryModel.plan_id == plan_id,
            ExecutionContextEntryModel.scope_type == scope_type,
            ExecutionContextEntryModel.scope_id == scope_id,
            ExecutionContextEntryModel.key == key,
        )
        return self.session.scalar(statement)

    def list_context_entries(
        self,
        plan_id: str,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> list[ExecutionContextEntryModel]:
        statement = select(ExecutionContextEntryModel).where(
            ExecutionContextEntryModel.plan_id == plan_id
        )
        if scope_type is not None:
            statement = statement.where(
                ExecutionContextEntryModel.scope_type == scope_type
            )
        if scope_id is not None:
            statement = statement.where(
                ExecutionContextEntryModel.scope_id == scope_id
            )
        statement = statement.order_by(
            ExecutionContextEntryModel.scope_type.asc(),
            ExecutionContextEntryModel.scope_id.asc(),
            ExecutionContextEntryModel.key.asc(),
        )
        return list(self.session.scalars(statement).all())

    def upsert_context(
        self,
        *,
        plan: ExecutionPlanModel,
        key: str,
        request: ContextEntryUpsertRequest,
    ) -> ExecutionContextEntryModel:
        normalized_key = key.strip()
        scope_type = request.scope_type.value
        scope_id = request.scope_id

        if scope_type == ContextScopeType.PLAN.value:
            scope_id = ""
        elif not scope_id:
            raise CollaborationError(
                f"scope_id обязателен для scope_type={scope_type}."
            )

        row = self.get_context_entry(
            plan_id=plan.id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=normalized_key,
        )

        if row is None:
            if request.expected_version is not None:
                raise ContextVersionConflict(
                    "Context entry ещё не существует; expected_version "
                    "должен быть пустым."
                )
            row = ExecutionContextEntryModel(
                workspace_id=plan.workspace_id,
                plan_id=plan.id,
                scope_type=scope_type,
                scope_id=scope_id,
                key=normalized_key,
                value_json=request.value,
                version=1,
                writer_agent_id=request.writer_agent_id,
                metadata_json=request.metadata,
            )
            self.session.add(row)
        else:
            if (
                request.expected_version is not None
                and request.expected_version != row.version
            ):
                raise ContextVersionConflict(
                    "Context version conflict: "
                    f"expected={request.expected_version}, actual={row.version}."
                )
            row.value_json = request.value
            row.version += 1
            row.writer_agent_id = request.writer_agent_id
            row.metadata_json = request.metadata
            row.updated_at = utc_now()

        self.session.flush()
        return row

    def delete_context(
        self,
        *,
        plan_id: str,
        scope_type: str,
        scope_id: str,
        key: str,
    ) -> bool:
        row = self.get_context_entry(
            plan_id=plan_id,
            scope_type=scope_type,
            scope_id=scope_id,
            key=key,
        )
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True

    def create_delegation(
        self,
        *,
        plan: ExecutionPlanModel,
        conversation_id: str,
        depth: int,
        request: DelegationCreateRequest,
    ) -> AgentDelegationModel:
        row = AgentDelegationModel(
            workspace_id=plan.workspace_id,
            plan_id=plan.id,
            conversation_id=conversation_id,
            source_step_id=request.source_step_id,
            parent_delegation_id=request.parent_delegation_id,
            delegator_agent_id=request.delegator_agent_id,
            delegate_agent_id=request.delegate_agent_id,
            objective=request.objective.strip(),
            input_json=request.input,
            depth=depth,
            max_depth=request.max_depth,
            timeout_seconds=request.timeout_seconds,
            metadata_json=request.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_delegation(
        self,
        delegation_id: str,
    ) -> AgentDelegationModel | None:
        return self.session.get(AgentDelegationModel, delegation_id)

    def list_delegations(
        self,
        plan_id: str,
        *,
        status: str | None = None,
    ) -> list[AgentDelegationModel]:
        statement = select(AgentDelegationModel).where(
            AgentDelegationModel.plan_id == plan_id
        )
        if status is not None:
            statement = statement.where(
                AgentDelegationModel.status == status
            )
        statement = statement.order_by(
            AgentDelegationModel.created_at.asc()
        )
        return list(self.session.scalars(statement).all())


class AgentCollaborationManager:
    def __init__(
        self,
        *,
        event_bus: EventBus,
        session_factory: SessionContextFactory = session_scope,
    ) -> None:
        self._event_bus = event_bus
        self._session_factory = session_factory
        self._messages_sent = 0
        self._context_writes = 0
        self._delegations_created = 0

    def stats(self) -> dict[str, int]:
        return {
            "messages_sent": self._messages_sent,
            "context_writes": self._context_writes,
            "delegations_created": self._delegations_created,
        }

    async def create_conversation(
        self,
        plan_id: str,
        request: ConversationCreateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            plan = repository.get_plan(plan_id)
            if plan is None:
                raise CollaborationNotFound("Execution Plan не найден.")
            if repository.get_conversation_by_topic(
                plan_id,
                request.topic_key,
            ) is not None:
                raise CollaborationError(
                    "Conversation с таким topic_key уже существует."
                )
            self._validate_agent_for_plan(
                repository,
                plan,
                request.created_by_agent_id,
                required=False,
            )
            row = repository.create_conversation(
                plan=plan,
                request=request,
            )
            result = self._serialize_conversation(row)

        await self._publish(
            "agent.conversation.created",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=plan_id,
        )
        return result

    def ensure_plan_conversation(
        self,
        plan_id: str,
        *,
        topic_key: str = "runtime",
        title: str = "Execution Plan collaboration",
        created_by_agent_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_topic = topic_key.strip().lower()

        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            plan = repository.get_plan(plan_id)
            if plan is None:
                raise CollaborationNotFound("Execution Plan не найден.")
            row = repository.get_conversation_by_topic(
                plan_id,
                normalized_topic,
            )
            if row is None:
                request = ConversationCreateRequest(
                    topic_key=normalized_topic,
                    title=title,
                    created_by_agent_id=created_by_agent_id,
                )
                self._validate_agent_for_plan(
                    repository,
                    plan,
                    created_by_agent_id,
                    required=False,
                )
                row = repository.create_conversation(
                    plan=plan,
                    request=request,
                )
            return self._serialize_conversation(row)

    def list_conversations(
        self,
        plan_id: str,
    ) -> list[dict[str, Any]] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_plan(plan_id) is None:
                return None
            return [
                self._serialize_conversation(row)
                for row in repository.list_conversations(plan_id)
            ]

    def get_conversation(
        self,
        conversation_id: str,
        *,
        message_limit: int = 100,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_conversation(conversation_id)
            if row is None:
                return None
            result = self._serialize_conversation(row)
            result["messages"] = [
                self._serialize_message(message)
                for message in repository.list_messages(
                    conversation_id,
                    limit=message_limit,
                )
            ]
            return result

    async def close_conversation(
        self,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_conversation(conversation_id)
            if row is None:
                return None
            if row.status != ConversationStatus.CLOSED.value:
                row.status = ConversationStatus.CLOSED.value
                row.closed_at = utc_now()
            result = self._serialize_conversation(row)

        await self._publish(
            "agent.conversation.closed",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=result["plan_id"],
        )
        return result

    async def send_message(
        self,
        conversation_id: str,
        request: AgentMessageCreateRequest,
    ) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            conversation = repository.get_conversation(conversation_id)
            if conversation is None:
                raise CollaborationNotFound("Conversation не найдена.")
            if conversation.status != ConversationStatus.OPEN.value:
                raise CollaborationError("Conversation закрыта.")

            plan = repository.get_plan(conversation.plan_id)
            if plan is None:
                raise CollaborationNotFound("Execution Plan не найден.")

            self._validate_agent_for_plan(
                repository,
                plan,
                request.sender_agent_id,
                required=False,
            )
            self._validate_agent_for_plan(
                repository,
                plan,
                request.recipient_agent_id,
                required=False,
            )

            if request.reply_to_message_id:
                parent = repository.get_message(request.reply_to_message_id)
                if parent is None or parent.conversation_id != conversation.id:
                    raise CollaborationError(
                        "reply_to_message_id не принадлежит Conversation."
                    )

            row = repository.create_message(
                conversation=conversation,
                request=request,
            )
            result = self._serialize_message(row)

        self._messages_sent += 1
        await self._publish(
            "agent.message.sent",
            result,
            workspace_id=plan.workspace_id,
            correlation_id=request.correlation_id or conversation.plan_id,
        )
        return result

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 100,
        after_sequence: int | None = None,
    ) -> list[dict[str, Any]] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_conversation(conversation_id) is None:
                return None
            return [
                self._serialize_message(row)
                for row in repository.list_messages(
                    conversation_id,
                    limit=limit,
                    after_sequence=after_sequence,
                )
            ]

    async def mark_message_read(
        self,
        message_id: str,
        *,
        handled: bool = False,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_message(message_id)
            if row is None:
                return None
            now = utc_now()
            row.read_at = row.read_at or now
            row.status = "handled" if handled else "read"
            if handled:
                row.handled_at = now
            result = self._serialize_message(row)

        await self._publish(
            "agent.message.handled" if handled else "agent.message.read",
            result,
            correlation_id=result["correlation_id"] or result["plan_id"],
        )
        return result

    async def upsert_context(
        self,
        plan_id: str,
        key: str,
        request: ContextEntryUpsertRequest,
    ) -> dict[str, Any]:
        normalized_key = key.strip()
        if not normalized_key:
            raise CollaborationError("Context key не может быть пустым.")

        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            plan = repository.get_plan(plan_id)
            if plan is None:
                raise CollaborationNotFound("Execution Plan не найден.")
            self._validate_agent_for_plan(
                repository,
                plan,
                request.writer_agent_id,
                required=False,
            )
            self._validate_context_scope(repository, plan, request)
            row = repository.upsert_context(
                plan=plan,
                key=normalized_key,
                request=request,
            )
            result = self._serialize_context(row)

        self._context_writes += 1
        await self._publish(
            "execution_context.updated",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=plan_id,
        )
        return result

    def context_snapshot(
        self,
        plan_id: str,
        *,
        agent_id: str | None = None,
        step_key: str | None = None,
        delegation_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_plan(plan_id) is None:
                return None

            entries = repository.list_context_entries(plan_id)
            merged: dict[str, Any] = {}
            selected: list[ExecutionContextEntryModel] = []
            scopes = [(ContextScopeType.PLAN.value, "")]

            if agent_id:
                scopes.append((ContextScopeType.AGENT.value, agent_id.lower()))
            if step_key:
                scopes.append((ContextScopeType.STEP.value, step_key.lower()))
            if delegation_id:
                scopes.append(
                    (ContextScopeType.DELEGATION.value, delegation_id.lower())
                )

            for scope_type, scope_id in scopes:
                for row in entries:
                    if row.scope_type == scope_type and row.scope_id == scope_id:
                        merged[row.key] = deepcopy(row.value_json)
                        selected.append(row)

            return {
                "plan_id": plan_id,
                "agent_id": agent_id,
                "step_key": step_key,
                "delegation_id": delegation_id,
                "values": merged,
                "entries": [
                    self._serialize_context(row) for row in selected
                ],
            }

    def list_context(
        self,
        plan_id: str,
        *,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_plan(plan_id) is None:
                return None
            return [
                self._serialize_context(row)
                for row in repository.list_context_entries(
                    plan_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
            ]

    async def delete_context(
        self,
        plan_id: str,
        *,
        scope_type: str,
        scope_id: str,
        key: str,
    ) -> bool:
        if scope_type == ContextScopeType.PLAN.value:
            scope_id = ""

        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_plan(plan_id) is None:
                raise CollaborationNotFound("Execution Plan не найден.")
            removed = repository.delete_context(
                plan_id=plan_id,
                scope_type=scope_type,
                scope_id=scope_id,
                key=key,
            )

        if removed:
            await self._publish(
                "execution_context.deleted",
                {
                    "plan_id": plan_id,
                    "scope_type": scope_type,
                    "scope_id": scope_id,
                    "key": key,
                },
                correlation_id=plan_id,
            )
        return removed

    async def create_delegation(
        self,
        plan_id: str,
        request: DelegationCreateRequest,
    ) -> dict[str, Any]:
        conversation = (
            self.get_conversation(request.conversation_id)
            if request.conversation_id
            else self.ensure_plan_conversation(plan_id)
        )
        if conversation is None or conversation["plan_id"] != plan_id:
            raise CollaborationError(
                "Conversation не принадлежит Execution Plan."
            )

        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            plan = repository.get_plan(plan_id)
            if plan is None:
                raise CollaborationNotFound("Execution Plan не найден.")

            self._validate_agent_for_plan(
                repository,
                plan,
                request.delegate_agent_id,
                required=True,
            )
            self._validate_agent_for_plan(
                repository,
                plan,
                request.delegator_agent_id,
                required=False,
            )

            if request.delegator_agent_id == request.delegate_agent_id:
                raise CollaborationError(
                    "Agent не может делегировать задачу самому себе."
                )

            if request.source_step_id:
                step = repository.get_step(request.source_step_id)
                if step is None or step.plan_id != plan_id:
                    raise CollaborationError(
                        "source_step_id не принадлежит Execution Plan."
                    )

            depth = 0
            if request.parent_delegation_id:
                parent = repository.get_delegation(
                    request.parent_delegation_id
                )
                if parent is None or parent.plan_id != plan_id:
                    raise CollaborationError(
                        "Parent delegation не принадлежит Execution Plan."
                    )
                depth = parent.depth + 1
                if depth > parent.max_depth:
                    raise CollaborationError(
                        "Превышена максимальная глубина делегирования."
                    )
                if request.max_depth > parent.max_depth:
                    raise CollaborationError(
                        "Child delegation не может увеличить max_depth."
                    )

            if depth > request.max_depth:
                raise CollaborationError(
                    "Превышена максимальная глубина делегирования."
                )

            row = repository.create_delegation(
                plan=plan,
                conversation_id=conversation["id"],
                depth=depth,
                request=request,
            )
            result = self._serialize_delegation(row)

        self._delegations_created += 1

        await self.send_message(
            conversation["id"],
            AgentMessageCreateRequest(
                sender_agent_id=request.delegator_agent_id,
                recipient_agent_id=request.delegate_agent_id,
                message_type=AgentMessageType.DELEGATION,
                subject="Delegation request",
                content={
                    "delegation_id": result["id"],
                    "objective": result["objective"],
                    "input": result["input"],
                    "depth": result["depth"],
                    "max_depth": result["max_depth"],
                },
                correlation_id=result["id"],
                priority="high",
            ),
        )
        await self._publish(
            "agent.delegation.requested",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=result["id"],
        )
        return result

    def get_delegation(
        self,
        delegation_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            row = AgentCollaborationRepository(session).get_delegation(
                delegation_id
            )
            return self._serialize_delegation(row) if row else None

    def list_delegations(
        self,
        plan_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            if repository.get_plan(plan_id) is None:
                return None
            return [
                self._serialize_delegation(row)
                for row in repository.list_delegations(
                    plan_id,
                    status=status,
                )
            ]

    async def accept_delegation(
        self,
        delegation_id: str,
        *,
        actor_agent_id: str | None,
        note: str | None,
    ) -> dict[str, Any] | None:
        return await self._decide_delegation(
            delegation_id,
            target=AgentDelegationStatus.ACCEPTED,
            actor_agent_id=actor_agent_id,
            note=note,
        )

    async def reject_delegation(
        self,
        delegation_id: str,
        *,
        actor_agent_id: str | None,
        note: str | None,
    ) -> dict[str, Any] | None:
        return await self._decide_delegation(
            delegation_id,
            target=AgentDelegationStatus.REJECTED,
            actor_agent_id=actor_agent_id,
            note=note,
        )

    async def cancel_delegation(
        self,
        delegation_id: str,
        *,
        actor_agent_id: str | None,
        note: str | None,
    ) -> dict[str, Any] | None:
        return await self._decide_delegation(
            delegation_id,
            target=AgentDelegationStatus.CANCELLED,
            actor_agent_id=actor_agent_id,
            note=note,
        )

    async def begin_delegation(
        self,
        delegation_id: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_delegation(delegation_id)
            if row is None:
                return None
            if row.status not in {
                AgentDelegationStatus.REQUESTED.value,
                AgentDelegationStatus.ACCEPTED.value,
                AgentDelegationStatus.FAILED.value,
            }:
                raise CollaborationError(
                    f"Delegation нельзя запустить из статуса {row.status}."
                )
            row.status = AgentDelegationStatus.RUNNING.value
            row.accepted_at = row.accepted_at or utc_now()
            row.started_at = utc_now()
            row.finished_at = None
            row.error = None
            result = self._serialize_delegation(row)

        await self._publish(
            "agent.delegation.started",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=delegation_id,
        )
        return result

    async def complete_delegation(
        self,
        delegation_id: str,
        result_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_delegation(delegation_id)
            if row is None:
                return None
            if row.status != AgentDelegationStatus.RUNNING.value:
                raise CollaborationError(
                    "Завершить можно только running delegation."
                )
            row.status = AgentDelegationStatus.COMPLETED.value
            row.result_json = result_data
            row.error = None
            row.finished_at = utc_now()
            result = self._serialize_delegation(row)

        await self.upsert_context(
            result["plan_id"],
            f"delegations.{delegation_id}.result",
            ContextEntryUpsertRequest(
                value=result_data,
                scope_type=ContextScopeType.PLAN,
                writer_agent_id=result["delegate_agent_id"],
                metadata={"delegation_id": delegation_id},
            ),
        )
        await self._publish(
            "agent.delegation.completed",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=delegation_id,
        )
        return result

    async def fail_delegation(
        self,
        delegation_id: str,
        error: str,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_delegation(delegation_id)
            if row is None:
                return None
            if row.status != AgentDelegationStatus.RUNNING.value:
                raise CollaborationError(
                    "Ошибка фиксируется только для running delegation."
                )
            row.status = AgentDelegationStatus.FAILED.value
            row.error = error
            row.finished_at = utc_now()
            result = self._serialize_delegation(row)

        await self._publish(
            "agent.delegation.failed",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=delegation_id,
        )
        return result

    def prepare_step_context(
        self,
        *,
        plan_id: str,
        step_key: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        conversation = self.ensure_plan_conversation(plan_id)
        snapshot = self.context_snapshot(
            plan_id,
            agent_id=agent_id,
            step_key=step_key,
        )
        recent = self.list_messages(
            conversation["id"],
            limit=25,
        ) or []
        return {
            "conversation_id": conversation["id"],
            "shared_context": (snapshot or {}).get("values", {}),
            "recent_messages": recent,
        }

    async def record_step_started(
        self,
        *,
        plan_id: str,
        step_id: str,
        step_key: str,
        agent_id: str | None,
        conversation_id: str,
        attempt: int,
    ) -> None:
        await self.send_message(
            conversation_id,
            AgentMessageCreateRequest(
                sender_agent_id=agent_id,
                message_type=AgentMessageType.SYSTEM,
                subject=f"Step started: {step_key}",
                content={
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "step_key": step_key,
                    "attempt": attempt,
                },
                correlation_id=plan_id,
            ),
        )

    async def record_step_completed(
        self,
        *,
        plan_id: str,
        step_id: str,
        step_key: str,
        agent_id: str | None,
        conversation_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        await self.upsert_context(
            plan_id,
            f"steps.{step_key}.output",
            ContextEntryUpsertRequest(
                value=output,
                scope_type=ContextScopeType.PLAN,
                writer_agent_id=agent_id,
                metadata={"step_id": step_id, "step_key": step_key},
            ),
        )

        context_writes = output.get("_context_write", {})
        if isinstance(context_writes, dict):
            for key, value in context_writes.items():
                await self.upsert_context(
                    plan_id,
                    str(key),
                    ContextEntryUpsertRequest(
                        value=value,
                        scope_type=ContextScopeType.PLAN,
                        writer_agent_id=agent_id,
                        metadata={
                            "source": "step_output_directive",
                            "step_id": step_id,
                        },
                    ),
                )

        messages_created = 0
        raw_messages = output.get("_messages", [])
        if isinstance(raw_messages, list):
            for raw in raw_messages:
                if not isinstance(raw, dict):
                    continue
                directive = CollaborationDirectiveMessage.model_validate(raw)
                await self.send_message(
                    conversation_id,
                    AgentMessageCreateRequest(
                        sender_agent_id=agent_id,
                        recipient_agent_id=directive.recipient_agent_id,
                        message_type=directive.message_type,
                        subject=directive.subject,
                        content=directive.content,
                        correlation_id=plan_id,
                        priority=directive.priority,
                    ),
                )
                messages_created += 1

        delegations_created = 0
        raw_delegations = output.get("_delegations", [])
        if isinstance(raw_delegations, list):
            for raw in raw_delegations:
                if not isinstance(raw, dict):
                    continue
                directive = CollaborationDirectiveDelegation.model_validate(raw)
                await self.create_delegation(
                    plan_id,
                    DelegationCreateRequest(
                        delegate_agent_id=directive.delegate_agent_id,
                        delegator_agent_id=agent_id,
                        source_step_id=step_id,
                        conversation_id=conversation_id,
                        objective=directive.objective,
                        input=directive.input,
                        timeout_seconds=directive.timeout_seconds,
                        max_depth=directive.max_depth,
                        metadata=directive.metadata,
                    ),
                )
                delegations_created += 1

        await self.send_message(
            conversation_id,
            AgentMessageCreateRequest(
                sender_agent_id=agent_id,
                message_type=AgentMessageType.SYSTEM,
                subject=f"Step completed: {step_key}",
                content={
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "step_key": step_key,
                    "output": output,
                },
                correlation_id=plan_id,
            ),
        )

        return {
            "messages_created": messages_created,
            "delegations_created": delegations_created,
        }

    async def record_step_failed(
        self,
        *,
        plan_id: str,
        step_id: str,
        step_key: str,
        agent_id: str | None,
        conversation_id: str,
        error: str,
        final: bool,
    ) -> None:
        await self.send_message(
            conversation_id,
            AgentMessageCreateRequest(
                sender_agent_id=agent_id,
                message_type=AgentMessageType.SYSTEM,
                subject=f"Step failed: {step_key}",
                content={
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "step_key": step_key,
                    "error": error,
                    "final": final,
                },
                correlation_id=plan_id,
                priority="high",
            ),
        )

    async def _decide_delegation(
        self,
        delegation_id: str,
        *,
        target: AgentDelegationStatus,
        actor_agent_id: str | None,
        note: str | None,
    ) -> dict[str, Any] | None:
        with self._session_factory() as session:
            repository = AgentCollaborationRepository(session)
            row = repository.get_delegation(delegation_id)
            if row is None:
                return None
            if row.status == target.value:
                return self._serialize_delegation(row)

            allowed_statuses = {
                AgentDelegationStatus.REQUESTED.value,
                AgentDelegationStatus.ACCEPTED.value,
                AgentDelegationStatus.FAILED.value,
            }
            if target == AgentDelegationStatus.CANCELLED:
                allowed_statuses.add(AgentDelegationStatus.RUNNING.value)

            if row.status not in allowed_statuses:
                raise CollaborationError(
                    f"Delegation нельзя изменить из статуса {row.status}."
                )
            if actor_agent_id and actor_agent_id not in {
                row.delegate_agent_id,
                row.delegator_agent_id,
            }:
                raise CollaborationError(
                    "Agent не является участником Delegation."
                )

            row.status = target.value
            metadata = dict(row.metadata_json or {})
            metadata["decision"] = {
                "actor_agent_id": actor_agent_id,
                "note": note,
                "decided_at": utc_now().isoformat(),
            }
            row.metadata_json = metadata

            if target == AgentDelegationStatus.ACCEPTED:
                row.accepted_at = utc_now()
            else:
                row.finished_at = utc_now()
            result = self._serialize_delegation(row)

        await self._publish(
            f"agent.delegation.{target.value}",
            result,
            workspace_id=result["workspace_id"],
            correlation_id=delegation_id,
        )
        return result

    @staticmethod
    def _validate_agent_for_plan(
        repository: AgentCollaborationRepository,
        plan: ExecutionPlanModel,
        agent_id: str | None,
        *,
        required: bool,
    ) -> AgentProfileModel | None:
        if agent_id is None:
            if required:
                raise CollaborationError("Agent обязателен.")
            return None

        agent = repository.get_agent(agent_id)
        if agent is None:
            raise CollaborationNotFound(f"Agent не найден: {agent_id}.")
        if not agent.enabled:
            raise CollaborationError(f"Agent отключён: {agent.agent_key}.")
        if agent.workspace_id not in {None, plan.workspace_id}:
            raise CollaborationError(
                "Agent не принадлежит Workspace Execution Plan."
            )
        return agent

    @staticmethod
    def _validate_context_scope(
        repository: AgentCollaborationRepository,
        plan: ExecutionPlanModel,
        request: ContextEntryUpsertRequest,
    ) -> None:
        scope_type = request.scope_type
        scope_id = request.scope_id

        if scope_type == ContextScopeType.PLAN:
            return
        if not scope_id:
            raise CollaborationError(
                f"scope_id обязателен для {scope_type.value}."
            )
        if scope_type == ContextScopeType.AGENT:
            AgentCollaborationManager._validate_agent_for_plan(
                repository,
                plan,
                scope_id,
                required=True,
            )
        elif scope_type == ContextScopeType.STEP:
            statement = select(ExecutionPlanStepModel).where(
                ExecutionPlanStepModel.plan_id == plan.id,
                ExecutionPlanStepModel.step_key == scope_id,
            )
            if repository.session.scalar(statement) is None:
                raise CollaborationError(
                    "Step scope не принадлежит Execution Plan."
                )
        elif scope_type == ContextScopeType.DELEGATION:
            delegation = repository.get_delegation(scope_id)
            if delegation is None or delegation.plan_id != plan.id:
                raise CollaborationError(
                    "Delegation scope не принадлежит Execution Plan."
                )

    async def _publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        await self._event_bus.publish(
            Event(
                event_type=event_type,
                source="agent_collaboration_manager",
                workspace_id=workspace_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )

    @staticmethod
    def _serialize_conversation(
        row: AgentConversationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "plan_id": row.plan_id,
            "topic_key": row.topic_key,
            "title": row.title,
            "status": row.status,
            "created_by_agent_id": row.created_by_agent_id,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "closed_at": row.closed_at,
        }

    @staticmethod
    def _serialize_message(row: AgentMessageModel) -> dict[str, Any]:
        return {
            "id": row.id,
            "conversation_id": row.conversation_id,
            "plan_id": row.plan_id,
            "sequence": row.sequence,
            "sender_agent_id": row.sender_agent_id,
            "recipient_agent_id": row.recipient_agent_id,
            "message_type": row.message_type,
            "subject": row.subject,
            "content": row.content_json,
            "correlation_id": row.correlation_id,
            "reply_to_message_id": row.reply_to_message_id,
            "priority": row.priority,
            "status": row.status,
            "created_at": row.created_at,
            "read_at": row.read_at,
            "handled_at": row.handled_at,
        }

    @staticmethod
    def _serialize_context(
        row: ExecutionContextEntryModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "plan_id": row.plan_id,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "key": row.key,
            "value": row.value_json,
            "version": row.version,
            "writer_agent_id": row.writer_agent_id,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _serialize_delegation(
        row: AgentDelegationModel,
    ) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "plan_id": row.plan_id,
            "conversation_id": row.conversation_id,
            "source_step_id": row.source_step_id,
            "parent_delegation_id": row.parent_delegation_id,
            "delegator_agent_id": row.delegator_agent_id,
            "delegate_agent_id": row.delegate_agent_id,
            "status": row.status,
            "objective": row.objective,
            "input": row.input_json,
            "result": row.result_json,
            "error": row.error,
            "depth": row.depth,
            "max_depth": row.max_depth,
            "timeout_seconds": row.timeout_seconds,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "accepted_at": row.accepted_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
