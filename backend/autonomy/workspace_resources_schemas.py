from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkspaceResourceAllocationMode(StrEnum):
    MANUAL = "manual"
    WEIGHTED = "weighted"
    ADAPTIVE = "adaptive"


class WorkspaceResourceReservationStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"
    EXCEEDED = "exceeded"
    CANCELLED = "cancelled"


class WorkspaceResourceConflictStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"
    CANCELLED = "cancelled"


class WorkspaceResourceConflictAction(StrEnum):
    DEFER = "defer"
    PREEMPT = "preempt"
    REBALANCE = "rebalance"
    INCREASE_CAPACITY = "increase_capacity"
    FORCE = "force"
    WAIVE = "waive"


class WorkspaceResourceRebalanceStatus(StrEnum):
    DRAFT = "draft"
    RECOMMENDED = "recommended"
    APPLIED = "applied"
    REJECTED = "rejected"


class WorkspaceResourcePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allocation_mode: WorkspaceResourceAllocationMode = (
        WorkspaceResourceAllocationMode.MANUAL
    )
    require_human_approval: bool = True
    auto_rebalance_enabled: bool = False
    enforce_cycle_admission: bool = False
    currency: str = Field(default="USD", min_length=3, max_length=8)
    total_budget_usd: float = Field(default=0.0, ge=0)
    default_cycle_budget_usd: float = Field(default=0.0, ge=0)
    max_cycle_budget_usd: float = Field(default=0.0, ge=0)
    reserve_percent: float = Field(default=10.0, ge=0, le=100)
    max_parallel_cycles: int = Field(default=4, ge=1, le=10000)
    agent_slots: int = Field(default=16, ge=1, le=100000)
    tool_slots: int = Field(default=16, ge=1, le=100000)
    compute_units: float = Field(default=16.0, ge=0)
    min_mission_guarantee_percent: float = Field(default=0.0, ge=0, le=100)
    overcommit_tolerance_percent: float = Field(default=0.0, ge=0, le=100)
    rebalance_interval_seconds: int = Field(default=300, ge=30, le=2_592_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "WorkspaceResourcePolicyUpsert":
        if self.require_human_approval:
            self.auto_rebalance_enabled = False
        if (
            self.auto_rebalance_enabled
            and self.allocation_mode == WorkspaceResourceAllocationMode.MANUAL
        ):
            raise ValueError(
                "auto_rebalance_enabled requires weighted or adaptive mode"
            )
        if (
            self.max_cycle_budget_usd
            and self.default_cycle_budget_usd > self.max_cycle_budget_usd
        ):
            raise ValueError(
                "default_cycle_budget_usd cannot exceed max_cycle_budget_usd"
            )
        if (
            self.total_budget_usd
            and self.max_cycle_budget_usd > self.total_budget_usd
        ):
            raise ValueError(
                "max_cycle_budget_usd cannot exceed total_budget_usd"
            )
        return self


class WorkspaceResourceReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_usd: float | None = Field(default=None, ge=0)
    agent_slots: int | None = Field(default=None, ge=0, le=100000)
    tool_slots: int | None = Field(default=None, ge=0, le=100000)
    compute_units: float | None = Field(default=None, ge=0)
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    request_approval: bool = True
    force: bool = False
    reason: str = Field(default="Workspace resource reservation requested.", max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceResourceReservationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceResourceConflictResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: WorkspaceResourceConflictAction
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
    total_budget_usd: float | None = Field(default=None, ge=0)
    max_parallel_cycles: int | None = Field(default=None, ge=1, le=10000)
    agent_slots: int | None = Field(default=None, ge=1, le=100000)
    tool_slots: int | None = Field(default=None, ge=1, le=100000)
    compute_units: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceResourceRebalanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    apply: bool = False
    force: bool = False
    reason: str = Field(default="Workspace resource rebalance requested.", min_length=1, max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceResourceRebalanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
