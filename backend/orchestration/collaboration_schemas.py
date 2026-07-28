from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class AgentMessageType(StrEnum):
    MESSAGE = "message"
    REQUEST = "request"
    RESPONSE = "response"
    DELEGATION = "delegation"
    SYSTEM = "system"


class AgentMessagePriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class AgentDelegationStatus(StrEnum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ContextScopeType(StrEnum):
    PLAN = "plan"
    AGENT = "agent"
    STEP = "step"
    DELEGATION = "delegation"


class ConversationCreateRequest(BaseModel):
    topic_key: str = Field(default="runtime", min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=255)
    created_by_agent_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("topic_key")
    @classmethod
    def normalize_topic_key(cls, value: str) -> str:
        return value.strip().lower()


class AgentMessageCreateRequest(BaseModel):
    sender_agent_id: str | None = Field(default=None, max_length=64)
    recipient_agent_id: str | None = Field(default=None, max_length=64)
    message_type: AgentMessageType = AgentMessageType.MESSAGE
    subject: str = Field(default="", max_length=255)
    content: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, max_length=128)
    reply_to_message_id: str | None = Field(default=None, max_length=64)
    priority: AgentMessagePriority = AgentMessagePriority.NORMAL


class ContextEntryUpsertRequest(BaseModel):
    value: Any
    scope_type: ContextScopeType = ContextScopeType.PLAN
    scope_id: str = Field(default="", max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    writer_agent_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("scope_id")
    @classmethod
    def normalize_scope_id(cls, value: str) -> str:
        return value.strip()


class DelegationCreateRequest(BaseModel):
    delegate_agent_id: str = Field(..., min_length=1, max_length=64)
    delegator_agent_id: str | None = Field(default=None, max_length=64)
    source_step_id: str | None = Field(default=None, max_length=64)
    parent_delegation_id: str | None = Field(default=None, max_length=64)
    conversation_id: str | None = Field(default=None, max_length=64)
    objective: str = Field(..., min_length=1, max_length=10000)
    input: dict[str, Any] = Field(default_factory=dict)
    max_depth: int = Field(default=3, ge=0, le=10)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DelegationDecisionRequest(BaseModel):
    actor_agent_id: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=4000)


class DelegationRunRequest(BaseModel):
    wait: bool = True
    wait_timeout_seconds: float | None = Field(default=None, gt=0, le=86400)


class CollaborationDirectiveMessage(BaseModel):
    recipient_agent_id: str | None = Field(default=None, max_length=64)
    message_type: AgentMessageType = AgentMessageType.MESSAGE
    subject: str = Field(default="", max_length=255)
    content: dict[str, Any] = Field(default_factory=dict)
    priority: AgentMessagePriority = AgentMessagePriority.NORMAL


class CollaborationDirectiveDelegation(BaseModel):
    delegate_agent_id: str = Field(..., min_length=1, max_length=64)
    objective: str = Field(..., min_length=1, max_length=10000)
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=300, ge=1, le=86400)
    max_depth: int = Field(default=3, ge=0, le=10)
    metadata: dict[str, Any] = Field(default_factory=dict)
