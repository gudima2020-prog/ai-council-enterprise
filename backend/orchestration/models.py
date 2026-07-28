from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base
from backend.orchestration.enums import (
    ExecutionPlanStatus,
    ExecutionStepStatus,
    ExecutionStepType,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ExecutionPlanModel(Base):
    __tablename__ = "execution_plans"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'draft', 'validated', 'ready', 'running', "
            "'completed', 'failed', 'cancelled', 'superseded'"
            ")",
            name="ck_execution_plans_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("plan"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    objective: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    strategy: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=ExecutionPlanStatus.DRAFT.value,
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    max_parallel_steps: Mapped[int] = mapped_column(
        Integer,
        default=4,
        nullable=False,
    )
    planner: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    validation_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    steps: Mapped[list["ExecutionPlanStepModel"]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by=(
            "ExecutionPlanStepModel.sequence, "
            "ExecutionPlanStepModel.step_key"
        ),
    )


class ExecutionPlanStepModel(Base):
    __tablename__ = "execution_plan_steps"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "step_key",
            name="uq_execution_plan_steps_key",
        ),
        CheckConstraint(
            "step_type IN ("
            "'agent', 'tool', 'task', 'approval', "
            "'decision', 'checkpoint'"
            ")",
            name="ck_execution_plan_steps_type",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'ready', 'running', 'completed', "
            "'failed', 'skipped', 'cancelled'"
            ")",
            name="ck_execution_plan_steps_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("planstep"),
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    step_type: Mapped[str] = mapped_column(
        String(32),
        default=ExecutionStepType.AGENT.value,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=ExecutionStepStatus.PENDING.value,
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    agent_role: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    capability: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    tool_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    depends_on_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    condition_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        nullable=False,
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    assigned_agent_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    assignment_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    plan: Mapped[ExecutionPlanModel] = relationship(
        back_populates="steps"
    )
    runs: Mapped[list["ExecutionStepRunModel"]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="ExecutionStepRunModel.attempt",
    )


class AgentProfileModel(Base):
    __tablename__ = "agent_profiles"
    __table_args__ = (
        UniqueConstraint("agent_key", name="uq_agent_profiles_agent_key"),
        CheckConstraint(
            "status IN ('available', 'busy', 'offline', 'maintenance')",
            name="ck_agent_profiles_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("agent"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    roles_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    tools_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="available",
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    executor_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    model_slug: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    capabilities: Mapped[list["AgentCapabilityModel"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        order_by="AgentCapabilityModel.name",
    )


class AgentCapabilityModel(Base):
    __tablename__ = "agent_capabilities"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "name",
            name="uq_agent_capabilities_agent_name",
        ),
        CheckConstraint(
            "proficiency >= 0 AND proficiency <= 100",
            name="ck_agent_capabilities_proficiency",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("agentcap"),
    )
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    proficiency: Mapped[int] = mapped_column(
        Integer,
        default=50,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    agent: Mapped[AgentProfileModel] = relationship(
        back_populates="capabilities"
    )



class ExecutionStepRunModel(Base):
    __tablename__ = "execution_step_runs"
    __table_args__ = (
        UniqueConstraint(
            "step_id",
            "attempt",
            name="uq_execution_step_runs_attempt",
        ),
        CheckConstraint(
            "status IN ("
            "'running', 'completed', 'failed', 'cancelled'"
            ")",
            name="ck_execution_step_runs_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("steprun"),
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plan_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=ExecutionStepStatus.RUNNING.value,
        nullable=False,
        index=True,
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    executor_ref: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    step: Mapped[ExecutionPlanStepModel] = relationship(
        back_populates="runs"
    )



class ToolDefinitionModel(Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "tool_key",
            name="uq_tool_definitions_workspace_key",
        ),
        CheckConstraint(
            "kind IN ('builtin', 'plugin', 'http', 'subprocess')",
            name="ck_tool_definitions_kind",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_tool_definitions_risk",
        ),
        CheckConstraint(
            "isolation_mode IN ('restricted', 'trusted')",
            name="ck_tool_definitions_isolation",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("tool"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    tool_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        default="builtin",
        nullable=False,
        index=True,
    )
    handler_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    risk_level: Mapped[str] = mapped_column(
        String(16),
        default="low",
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    requires_explicit_allow: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    isolation_mode: Mapped[str] = mapped_column(
        String(32),
        default="restricted",
        nullable=False,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=60,
        nullable=False,
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    max_input_bytes: Mapped[int] = mapped_column(
        Integer,
        default=262144,
        nullable=False,
    )
    max_output_bytes: Mapped[int] = mapped_column(
        Integer,
        default=1048576,
        nullable=False,
    )
    allow_network: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    allow_filesystem_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    allow_filesystem_write: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    input_schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    output_schema_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ToolPermissionModel(Base):
    __tablename__ = "tool_permissions"
    __table_args__ = (
        CheckConstraint(
            "effect IN ('allow', 'deny')",
            name="ck_tool_permissions_effect",
        ),
        CheckConstraint(
            "action IN ('execute')",
            name="ck_tool_permissions_action",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("toolperm"),
    )
    tool_id: Mapped[str] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    effect: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(32),
        default="execute",
        nullable=False,
    )
    constraints_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class ToolInvocationModel(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'running', 'completed', 'failed', 'denied', "
            "'timed_out', 'cancelled'"
            ")",
            name="ck_tool_invocations_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("toolcall"),
    )
    tool_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tool_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plan_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="running",
        nullable=False,
        index=True,
    )
    isolation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    output_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    policy_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class AgentConversationModel(Base):
    __tablename__ = "agent_conversations"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "topic_key",
            name="uq_agent_conversations_plan_topic",
        ),
        CheckConstraint(
            "status IN ('open', 'closed')",
            name="ck_agent_conversations_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("conversation"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic_key: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="open",
        nullable=False,
        index=True,
    )
    created_by_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentMessageModel(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_agent_messages_conversation_sequence",
        ),
        CheckConstraint(
            "message_type IN ("
            "'message', 'request', 'response', 'delegation', 'system'"
            ")",
            name="ck_agent_messages_type",
        ),
        CheckConstraint(
            "status IN ('sent', 'read', 'handled')",
            name="ck_agent_messages_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'critical')",
            name="ck_agent_messages_priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("agentmsg"),
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    sender_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    recipient_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    message_type: Mapped[str] = mapped_column(
        String(32),
        default="message",
        nullable=False,
        index=True,
    )
    subject: Mapped[str] = mapped_column(
        String(255),
        default="",
        nullable=False,
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    reply_to_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    priority: Mapped[str] = mapped_column(
        String(16),
        default="normal",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="sent",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class AgentDelegationModel(Base):
    __tablename__ = "agent_delegations"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'requested', 'accepted', 'running', 'completed', "
            "'failed', 'rejected', 'cancelled'"
            ")",
            name="ck_agent_delegations_status",
        ),
        CheckConstraint(
            "depth >= 0 AND max_depth >= 0 AND depth <= max_depth",
            name="ck_agent_delegations_depth",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("delegation"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_step_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plan_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_delegation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_delegations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    delegator_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    delegate_agent_id: Mapped[str] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="requested",
        nullable=False,
        index=True,
    )
    objective: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    depth: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    max_depth: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=300,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ExecutionContextEntryModel(Base):
    __tablename__ = "execution_context_entries"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "scope_type",
            "scope_id",
            "key",
            name="uq_execution_context_scope_key",
        ),
        CheckConstraint(
            "scope_type IN ('plan', 'agent', 'step', 'delegation')",
            name="ck_execution_context_scope_type",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_execution_context_version",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("ctx"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_type: Mapped[str] = mapped_column(
        String(16),
        default="plan",
        nullable=False,
        index=True,
    )
    scope_id: Mapped[str] = mapped_column(
        String(128),
        default="",
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    value_json: Mapped[Any] = mapped_column(
        JSON,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    writer_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionPlannerRunModel(Base):
    __tablename__ = "execution_planner_runs"
    __table_args__ = (
        CheckConstraint(
            "run_type IN ('generate', 'replan')",
            name="ck_execution_planner_runs_type",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_execution_planner_runs_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("plannerrun"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    result_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    run_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    planner_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    response_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    failure_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )



class ExecutionPlanReviewModel(Base):
    __tablename__ = "execution_plan_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_execution_plan_reviews_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('pass', 'revise', 'reject')",
            name="ck_execution_plan_reviews_decision",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("planreview"),
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reviewer_ref: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    decision: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
    )
    review_round: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    threshold: Mapped[int] = mapped_column(
        Integer,
        default=80,
        nullable=False,
    )
    score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    dimension_scores_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    issues_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    suggested_fixes_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    applied_fixes_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    response_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class ExecutionSupervisorPolicyModel(Base):
    __tablename__ = "execution_supervisor_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_execution_supervisor_policies_scope_key",
        ),
        CheckConstraint(
            "failure_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_failure_action",
        ),
        CheckConstraint(
            "stall_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_stall_action",
        ),
        CheckConstraint(
            "long_running_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_long_action",
        ),
        CheckConstraint(
            "retry_action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_policies_retry_action",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("supervisorpolicy"),
    )
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    automatic_actions_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    check_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )
    stall_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        default=900,
        nullable=False,
    )
    max_step_runtime_seconds: Mapped[int] = mapped_column(
        Integer,
        default=600,
        nullable=False,
    )
    retry_warning_threshold: Mapped[int] = mapped_column(
        Integer,
        default=2,
        nullable=False,
    )
    failure_action: Mapped[str] = mapped_column(
        String(32),
        default="observe",
        nullable=False,
    )
    stall_action: Mapped[str] = mapped_column(
        String(32),
        default="observe",
        nullable=False,
    )
    long_running_action: Mapped[str] = mapped_column(
        String(32),
        default="observe",
        nullable=False,
    )
    retry_action: Mapped[str] = mapped_column(
        String(32),
        default="observe",
        nullable=False,
    )
    auto_start_replanned: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionSupervisorIncidentModel(Base):
    __tablename__ = "execution_supervisor_incidents"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            name="uq_execution_supervisor_incidents_dedupe_key",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'high', 'critical')",
            name="ck_execution_supervisor_incidents_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name="ck_execution_supervisor_incidents_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("supervisorincident"),
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plan_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_supervisor_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    incident_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        nullable=False,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionSupervisorActionModel(Base):
    __tablename__ = "execution_supervisor_actions"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            name="uq_execution_supervisor_actions_dedupe_key",
        ),
        CheckConstraint(
            "action IN ('observe', 'notify', 'cancel', 'replan')",
            name="ck_execution_supervisor_actions_action",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'skipped')",
            name="ck_execution_supervisor_actions_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("supervisoraction"),
    )
    incident_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_supervisor_incidents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plan_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_supervisor_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    automatic: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionSLOPolicyModel(Base):
    __tablename__ = "execution_slo_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            name="uq_execution_slo_policies_scope_key",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("slopolicy"),
    )
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    window_minutes: Mapped[int] = mapped_column(
        Integer,
        default=60,
        nullable=False,
    )
    evaluation_interval_seconds: Mapped[int] = mapped_column(
        Integer,
        default=60,
        nullable=False,
    )
    min_sample_size: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )
    target_plan_success_rate_pct: Mapped[float] = mapped_column(
        Float,
        default=95.0,
        nullable=False,
    )
    target_step_success_rate_pct: Mapped[float] = mapped_column(
        Float,
        default=97.0,
        nullable=False,
    )
    max_p95_plan_duration_ms: Mapped[float] = mapped_column(
        Float,
        default=600000.0,
        nullable=False,
    )
    max_p95_step_duration_ms: Mapped[float] = mapped_column(
        Float,
        default=300000.0,
        nullable=False,
    )
    max_tool_failure_rate_pct: Mapped[float] = mapped_column(
        Float,
        default=5.0,
        nullable=False,
    )
    max_open_critical_incidents: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionMetricSnapshotModel(Base):
    __tablename__ = "execution_metric_snapshots"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("metricsnapshot"),
    )
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    window_ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )


class ExecutionSLOBreachModel(Base):
    __tablename__ = "execution_slo_breaches"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            name="uq_execution_slo_breaches_dedupe_key",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_execution_slo_breaches_status",
        ),
        CheckConstraint(
            "severity IN ('warning', 'high', 'critical')",
            name="ck_execution_slo_breaches_severity",
        ),
        CheckConstraint(
            "comparison IN ('minimum', 'maximum')",
            name="ck_execution_slo_breaches_comparison",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("slobreach"),
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_slo_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="open",
        nullable=False,
        index=True,
    )
    severity: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    comparison: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    window_ended_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionWorkerModel(Base):
    __tablename__ = "execution_workers"
    __table_args__ = (
        UniqueConstraint(
            "worker_key",
            name="uq_execution_workers_worker_key",
        ),
        CheckConstraint(
            "status IN ('active', 'draining', 'offline', 'unhealthy')",
            name="ck_execution_workers_status",
        ),
        CheckConstraint(
            "max_concurrency >= 1",
            name="ck_execution_workers_max_concurrency",
        ),
        CheckConstraint(
            "heartbeat_ttl_seconds >= 5",
            name="ck_execution_workers_heartbeat_ttl",
        ),
        CheckConstraint(
            "default_lease_seconds >= 5",
            name="ck_execution_workers_default_lease",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("worker"),
    )
    worker_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    instance_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    hostname: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    process_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        nullable=False,
        index=True,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )
    queues_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=lambda: ["default"],
        nullable=False,
    )
    capabilities_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    max_concurrency: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    active_leases: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    heartbeat_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=45,
        nullable=False,
    )
    default_lease_seconds: Mapped[int] = mapped_column(
        Integer,
        default=90,
        nullable=False,
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    draining_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionWorkItemModel(Base):
    __tablename__ = "execution_work_items"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uq_execution_work_items_idempotency_key",
        ),
        CheckConstraint(
            "work_type IN ('execution_plan')",
            name="ck_execution_work_items_type",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'leased', 'completed', 'failed', 'cancelled'"
            ")",
            name="ck_execution_work_items_status",
        ),
        CheckConstraint(
            "priority >= -1000 AND priority <= 1000",
            name="ck_execution_work_items_priority",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_execution_work_items_max_attempts",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_execution_work_items_attempt_count",
        ),
        CheckConstraint(
            "fencing_token >= 0",
            name="ck_execution_work_items_fencing_token",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("work"),
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_plans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    work_type: Mapped[str] = mapped_column(
        String(32),
        default="execution_plan",
        nullable=False,
        index=True,
    )
    queue_name: Mapped[str] = mapped_column(
        String(128),
        default="default",
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        index=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    required_capabilities_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=3,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    last_lease_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    fencing_token: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class ExecutionLeaseModel(Base):
    __tablename__ = "execution_leases"
    __table_args__ = (
        UniqueConstraint(
            "lease_token",
            name="uq_execution_leases_token",
        ),
        UniqueConstraint(
            "work_item_id",
            "fencing_token",
            name="uq_execution_leases_work_fence",
        ),
        CheckConstraint(
            "status IN ("
            "'active', 'renewed', 'completed', 'failed', "
            "'released', 'expired', 'lost'"
            ")",
            name="ck_execution_leases_status",
        ),
        CheckConstraint(
            "fencing_token >= 1",
            name="ck_execution_leases_fencing_token",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("lease"),
    )
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("execution_work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lease_token: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    fencing_token: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        nullable=False,
        index=True,
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    release_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class ExecutionRemoteSessionModel(Base):
    __tablename__ = "execution_remote_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'closed', 'expired', 'revoked')",
            name="ck_execution_remote_sessions_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("rsession"),
    )
    worker_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    instance_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    protocol_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    features_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="active",
        nullable=False,
        index=True,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    close_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )


class ExecutionEventEnvelopeModel(Base):
    __tablename__ = "execution_event_envelopes"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            name="uq_execution_event_envelopes_event_id",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_execution_event_envelopes_idempotency_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'acknowledged', 'dead', 'cancelled')",
            name="ck_execution_event_envelopes_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_execution_event_envelopes_attempt_count",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_execution_event_envelopes_max_attempts",
        ),
        CheckConstraint(
            "priority >= -1000 AND priority <= 1000",
            name="ck_execution_event_envelopes_priority",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("envelope"),
    )
    event_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    topic: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    source_node: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    target_worker_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_workers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        index=True,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    headers_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    lease_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_remote_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lease_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    ack_result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )


class ExecutionEventReceiptModel(Base):
    __tablename__ = "execution_event_receipts"
    __table_args__ = (
        UniqueConstraint(
            "envelope_id",
            "consumer_key",
            "delivery_attempt",
            name="uq_execution_event_receipts_delivery",
        ),
        CheckConstraint(
            "status IN ('processed', 'failed')",
            name="ck_execution_event_receipts_status",
        ),
        CheckConstraint(
            "delivery_attempt >= 1",
            name="ck_execution_event_receipts_attempt",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("receipt"),
    )
    envelope_id: Mapped[str] = mapped_column(
        ForeignKey("execution_event_envelopes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_remote_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    consumer_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    delivery_attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
