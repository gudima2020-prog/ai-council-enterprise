from __future__ import annotations

from enum import StrEnum


class ExecutionPlanStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class ExecutionStepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ExecutionStepType(StrEnum):
    AGENT = "agent"
    TOOL = "tool"
    TASK = "task"
    APPROVAL = "approval"
    DECISION = "decision"
    CHECKPOINT = "checkpoint"
