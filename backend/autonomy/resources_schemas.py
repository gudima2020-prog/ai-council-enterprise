from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionResourceAllocationMode(StrEnum):
    MANUAL = "manual"
    BALANCED = "balanced"
    ADAPTIVE = "adaptive"


class MissionResourceAllocationStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"
    EXCEEDED = "exceeded"
    CANCELLED = "cancelled"


class MissionResourceUsageCategory(StrEnum):
    LLM = "llm"
    TOOL = "tool"
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORK = "network"
    HUMAN = "human"
    OTHER = "other"


class MissionCapacityPlanStatus(StrEnum):
    DRAFT = "draft"
    RECOMMENDED = "recommended"
    APPLIED = "applied"
    REJECTED = "rejected"


class MissionResourcePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    allocation_mode: MissionResourceAllocationMode = MissionResourceAllocationMode.MANUAL
    require_human_approval: bool = True
    auto_allocation_enabled: bool = False
    auto_rebalance_enabled: bool = False
    currency: str = Field(default="USD", min_length=3, max_length=8)
    total_budget_usd: float = Field(default=0.0, ge=0)
    default_cycle_budget_usd: float = Field(default=0.0, ge=0)
    max_cycle_budget_usd: float = Field(default=0.0, ge=0)
    reserve_percent: float = Field(default=10.0, ge=0, le=100)
    max_parallel_cycles: int = Field(default=1, ge=1, le=1000)
    agent_slots: int = Field(default=4, ge=1, le=100000)
    tool_slots: int = Field(default=4, ge=1, le=100000)
    compute_units: float = Field(default=4.0, ge=0)
    planning_horizon_cycles: int = Field(default=10, ge=1, le=10000)
    min_rebalance_improvement_percent: float = Field(default=10.0, ge=0, le=100)
    overrun_tolerance_percent: float = Field(default=10.0, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "MissionResourcePolicyUpsert":
        if self.require_human_approval:
            self.auto_allocation_enabled = False
        if self.auto_allocation_enabled and self.allocation_mode == MissionResourceAllocationMode.MANUAL:
            raise ValueError("Для auto_allocation_enabled нужен режим balanced или adaptive.")
        if self.auto_rebalance_enabled and self.allocation_mode != MissionResourceAllocationMode.ADAPTIVE:
            raise ValueError("auto_rebalance_enabled разрешён только в adaptive режиме.")
        if self.max_cycle_budget_usd and self.default_cycle_budget_usd > self.max_cycle_budget_usd:
            raise ValueError("default_cycle_budget_usd не может превышать max_cycle_budget_usd.")
        if self.total_budget_usd and self.max_cycle_budget_usd > self.total_budget_usd:
            raise ValueError("max_cycle_budget_usd не может превышать total_budget_usd.")
        return self


class MissionResourceAllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str | None = Field(default=None, max_length=64)
    budget_usd: float | None = Field(default=None, ge=0)
    agent_slots: int = Field(default=1, ge=0, le=100000)
    tool_slots: int = Field(default=1, ge=0, le=100000)
    compute_units: float = Field(default=1.0, ge=0)
    requested_by: str = Field(default="user", min_length=1, max_length=255)
    automatic: bool = False
    request_approval: bool = True
    rationale: str = Field(default="", max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionResourceAllocationApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    rationale: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
    reserve_now: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionResourceAllocationCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)


class MissionResourceUsageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MissionResourceUsageCategory
    quantity: float = Field(default=1.0, ge=0)
    unit: str = Field(default="unit", min_length=1, max_length=64)
    cost_usd: float = Field(default=0.0, ge=0)
    source_type: str = Field(default="manual", min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=255)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    actor_id: str = Field(default="system", min_length=1, max_length=255)
    occurred_at: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MissionCapacityPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="system", min_length=1, max_length=255)
    sample_cycles: int | None = Field(default=None, ge=1, le=10000)
    apply: bool = False
    force: bool = False
    rationale: str = Field(default="Adaptive capacity recommendation.", max_length=20000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionCapacityPlanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=20000)
    force: bool = False
