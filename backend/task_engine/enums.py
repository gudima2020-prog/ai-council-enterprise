from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    PLANNING = "planning"
    WAITING = "waiting"
    RUNNING = "running"
    POST_PROCESSING = "post_processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class TaskType(StrEnum):
    CHAT = "chat"
    PLUGIN = "plugin"
    WORKFLOW = "workflow"
    TRADER = "trader"
    WORKER = "worker"
    SYSTEM = "system"
    IMPORT = "import"
    EXPORT = "export"
    PIPELINE = "pipeline"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"
    CRITICAL = "critical"
