from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MissionScheduleMode(StrEnum):
    MANUAL = "manual"
    FIXED = "fixed"
    ADAPTIVE = "adaptive"


class MissionOverdueAction(StrEnum):
    OBSERVE = "observe"
    PAUSE = "pause"
    ESCALATE = "escalate"


class MissionScheduleDecision(StrEnum):
    KEEP = "keep"
    ACCELERATE = "accelerate"
    SLOW_DOWN = "slow_down"
    PAUSE = "pause"


class MissionSchedulePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    schedule_mode: MissionScheduleMode = MissionScheduleMode.MANUAL
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    base_interval_seconds: int = Field(default=3600, ge=30, le=2_592_000)
    min_interval_seconds: int = Field(default=300, ge=30, le=2_592_000)
    max_interval_seconds: int = Field(default=86_400, ge=30, le=2_592_000)
    adaptive_enabled: bool = False
    require_human_approval: bool = True
    auto_apply_enabled: bool = False
    enforce_manual_cycles: bool = False
    allowed_weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    deadline_warning_seconds: int = Field(default=86_400, ge=0, le=31_536_000)
    overdue_action: MissionOverdueAction = MissionOverdueAction.OBSERVE
    target_cycles_per_day: float = Field(default=1.0, ge=0.0, le=96.0)
    adaptation_sample_cycles: int = Field(default=10, ge=1, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        normalized = sorted(set(value))
        if any(day < 0 or day > 6 for day in normalized):
            raise ValueError("allowed_weekdays must contain values from 0 to 6")
        return normalized

    @field_validator("quiet_hours_start", "quiet_hours_end")
    @classmethod
    def validate_hhmm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("time must use HH:MM format")
        hour, minute = (int(parts[0]), int(parts[1]))
        if hour not in range(24) or minute not in range(60):
            raise ValueError("time must use HH:MM format")
        return f"{hour:02d}:{minute:02d}"

    @model_validator(mode="after")
    def validate_ranges(self) -> "MissionSchedulePolicyUpsert":
        if self.min_interval_seconds > self.base_interval_seconds:
            raise ValueError("min_interval_seconds cannot exceed base_interval_seconds")
        if self.base_interval_seconds > self.max_interval_seconds:
            raise ValueError("base_interval_seconds cannot exceed max_interval_seconds")
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours require both start and end")
        return self


class MissionScheduleWindowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    start_time: str = "00:00"
    end_time: str = "23:59"
    starts_at: str | None = None
    ends_at: str | None = None
    priority: int = Field(default=50, ge=0, le=100)
    max_cycles: int = Field(default=0, ge=0, le=100_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int]) -> list[int]:
        normalized = sorted(set(value))
        if any(day < 0 or day > 6 for day in normalized):
            raise ValueError("weekdays must contain values from 0 to 6")
        return normalized

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_hhmm(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("time must use HH:MM format")
        hour, minute = (int(parts[0]), int(parts[1]))
        if hour not in range(24) or minute not in range(60):
            raise ValueError("time must use HH:MM format")
        return f"{hour:02d}:{minute:02d}"


class MissionScheduleWindowUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    enabled: bool | None = None
    weekdays: list[int] | None = None
    start_time: str | None = None
    end_time: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    max_cycles: int | None = Field(default=None, ge=0, le=100_000)
    metadata: dict[str, Any] | None = None

    @field_validator("weekdays")
    @classmethod
    def validate_weekdays(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        normalized = sorted(set(value))
        if any(day < 0 or day > 6 for day in normalized):
            raise ValueError("weekdays must contain values from 0 to 6")
        return normalized

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_hhmm(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("time must use HH:MM format")
        hour, minute = (int(parts[0]), int(parts[1]))
        if hour not in range(24) or minute not in range(60):
            raise ValueError("time must use HH:MM format")
        return f"{hour:02d}:{minute:02d}"


class MissionScheduleEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    apply: bool = False
    force: bool = False
    automatic: bool = False
    rationale: str = Field(default="Schedule evaluation requested.", max_length=20_000)
    context: dict[str, Any] = Field(default_factory=dict)


class MissionDeadlineScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class MissionDeadlineResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20_000)
