from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CouncilExecutionMode(StrEnum):
    SOLO = "solo"
    COUNCIL = "council"
    BEST_OF_N = "best_of_n"
    REVIEW = "review"
    ARBITRATION = "arbitration"
    DELEGATE = "delegate"


class CouncilRole(StrEnum):
    ANALYST = "analyst"
    CRITIC = "critic"
    STRATEGIST = "strategist"
    RESEARCHER = "researcher"
    RISK = "risk"


class CouncilMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(default="openrouter", min_length=1, max_length=64)
    role: CouncilRole = CouncilRole.ANALYST
    label: str | None = Field(default=None, max_length=120)


class CouncilRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=50000)
    members: list[CouncilMemberRequest] = Field(
        ...,
        min_length=1,
        max_length=6,
    )
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    mode: Literal["universal", "crypto", "code", "documents"] = "universal"
    synthesizer_model: str | None = Field(default=None, max_length=255)
    synthesizer_provider: str | None = Field(default=None, max_length=64)
    reviewer_model: str | None = Field(default=None, max_length=255)
    reviewer_provider: str | None = Field(default=None, max_length=64)
    arbiter_model: str | None = Field(default=None, max_length=255)
    arbiter_provider: str | None = Field(default=None, max_length=64)
    delegation_max_calls: int = Field(default=4, ge=1, le=8)
    delegation_max_depth: int = Field(default=1, ge=1, le=1)
    workspace_id: str | None = Field(default=None, max_length=64)
    correlation_id: str | None = Field(default=None, max_length=255)
    actor_id: str | None = Field(default=None, max_length=255)
    member_timeout_seconds: int = Field(default=60, ge=5, le=600)
    cost_approval_token: str | None = Field(default=None, max_length=96)
    estimated_cost_usd: float | None = Field(default=None, ge=0, exclude=True)
    cost_estimate_status: Literal["known", "partial", "unknown"] | None = Field(
        default=None, exclude=True
    )
    cost_approval_id: str | None = Field(default=None, max_length=64, exclude=True)
    cost_reservation_id: str | None = Field(default=None, max_length=64, exclude=True)

    @model_validator(mode="after")
    def validate_orchestration(self) -> "CouncilRunRequest":
        identities = {
            (member.provider.strip().lower(), member.model.strip())
            for member in self.members
        }
        if len(identities) != len(self.members):
            raise ValueError(
                "Council members must use distinct provider/model pairs."
            )
        if self.execution_mode == CouncilExecutionMode.SOLO:
            if len(self.members) != 1:
                raise ValueError("Solo mode requires exactly one member.")
        elif len(self.members) < 2:
            raise ValueError(
                "This Council execution mode requires at least two members."
            )

        chair = (
            (self.synthesizer_provider or self.members[0].provider).strip().lower(),
            (self.synthesizer_model or self.members[0].model).strip(),
        )
        if self.execution_mode in {CouncilExecutionMode.REVIEW, CouncilExecutionMode.ARBITRATION}:
            if not self.reviewer_model:
                raise ValueError("Reviewer model is required for review/arbitration mode.")
            reviewer = (
                (self.reviewer_provider or self.members[0].provider).strip().lower(),
                self.reviewer_model.strip(),
            )
            if reviewer in identities or reviewer == chair:
                raise ValueError(
                    "Reviewer must be independent from Council members and chair."
                )
        if self.execution_mode == CouncilExecutionMode.ARBITRATION:
            if not self.arbiter_model:
                raise ValueError("Arbiter model is required for arbitration mode.")
            reviewer = (
                (self.reviewer_provider or self.members[0].provider).strip().lower(),
                (self.reviewer_model or "").strip(),
            )
            arbiter = (
                (self.arbiter_provider or self.members[0].provider).strip().lower(),
                self.arbiter_model.strip(),
            )
            if arbiter in identities or arbiter == reviewer:
                raise ValueError(
                    "Arbiter must be independent from Council members and reviewer."
                )
        return self


class CouncilUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class CouncilMemberResult(BaseModel):
    provider: str
    model: str
    requested_model: str | None = None
    role: CouncilRole
    label: str
    status: Literal["success", "error"]
    fallback_used: bool = False
    fallback_model: str | None = None
    answer: str = ""
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None
    usage: CouncilUsage = Field(default_factory=CouncilUsage)
    provider_reported_cost_usd: float | None = Field(default=None, ge=0)


class CouncilSynthesis(BaseModel):
    provider: str
    model: str
    requested_model: str | None = None
    status: Literal["structured", "unstructured", "fallback"]
    fallback_used: bool = False
    fallback_model: str | None = None
    final_answer: str
    consensus: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: int | None = Field(default=None, ge=0, le=100)
    usage: CouncilUsage = Field(default_factory=CouncilUsage)
    provider_reported_cost_usd: float | None = Field(default=None, ge=0)




class CouncilAuxiliaryResult(BaseModel):
    stage: Literal["draft", "reviewer", "planner", "delegate"]
    ordinal: int | None = None
    provider: str
    model: str
    requested_model: str | None = None
    label: str
    status: Literal["success", "error"]
    content: str = ""
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: float | None = None
    usage: CouncilUsage = Field(default_factory=CouncilUsage)
    provider_reported_cost_usd: float | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CouncilOrchestrationTrace(BaseModel):
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    finalizer_stage: Literal[
        "solo", "synthesis", "selection", "revision", "arbiter"
    ] = "synthesis"
    auxiliary_calls: list[CouncilAuxiliaryResult] = Field(default_factory=list)
    delegation_depth: int = 0
    delegation_calls: int = 0

class CouncilRunResponse(BaseModel):
    run_id: str
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    status: Literal["completed", "partial"]
    question: str
    mode: str
    members: list[CouncilMemberResult]
    synthesis: CouncilSynthesis
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    history_saved: bool = False
    replay_of_run_id: str | None = None
    estimated_cost_usd: float | None = None
    cost_estimate_status: Literal["known", "partial", "unknown"] | None = None
    cost_approval_id: str | None = None
    cost_reservation_id: str | None = None
    actual_cost_usd: float | None = None
    actual_cost_status: Literal["known", "partial", "unknown"] | None = None
    actual_total_tokens: int | None = None
    orchestration: CouncilOrchestrationTrace = Field(default_factory=CouncilOrchestrationTrace)


class CouncilRunSummary(BaseModel):
    run_id: str
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    status: Literal["completed", "partial", "failed", "cancelled"]
    question_preview: str
    mode: str
    member_count: int
    successful_member_count: int
    confidence: int | None = None
    duration_ms: float
    replay_of_run_id: str | None = None
    estimated_cost_usd: float | None = None
    cost_estimate_status: str | None = None
    actual_cost_usd: float | None = None
    actual_cost_status: str | None = None
    actual_total_tokens: int | None = None
    started_at: datetime
    finished_at: datetime


class CouncilRunHistoryPage(BaseModel):
    workspace_id: str | None = None
    items: list[CouncilRunSummary]
    total: int
    limit: int
    offset: int
    has_more: bool


class CouncilRunHistoryDetail(BaseModel):
    run_id: str
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    workspace_id: str | None = None
    replay_of_run_id: str | None = None
    estimated_cost_usd: float | None = None
    cost_estimate_status: str | None = None
    cost_approval_id: str | None = None
    cost_reservation_id: str | None = None
    actual_cost_usd: float | None = None
    actual_cost_status: str | None = None
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    actual_total_tokens: int | None = None
    status: Literal["completed", "partial", "failed", "cancelled"]
    question: str
    mode: str
    members: list[CouncilMemberResult]
    synthesis: CouncilSynthesis | None = None
    member_count: int
    successful_member_count: int
    correlation_id: str | None = None
    actor_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: float
    started_at: datetime
    finished_at: datetime
    orchestration: CouncilOrchestrationTrace = Field(default_factory=CouncilOrchestrationTrace)


class CouncilRunDeleteResponse(BaseModel):
    run_id: str
    deleted: bool


class CouncilRetentionPolicy(BaseModel):
    workspace_id: str | None = None
    retention_days: int = Field(default=365, ge=1, le=3650)
    auto_delete_enabled: bool = False
    store_member_answers: bool = True
    store_token_usage: bool = True


class CouncilRetentionPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retention_days: int = Field(default=365, ge=1, le=3650)
    auto_delete_enabled: bool = False
    store_member_answers: bool = True
    store_token_usage: bool = True


class CouncilRetentionPurgeResponse(BaseModel):
    workspace_id: str | None = None
    retention_days: int
    deleted_count: int


class CouncilLiveStartResponse(BaseModel):
    run_id: str
    status: Literal["queued", "running"]
    kind: Literal["run", "replay", "retry_failed"] = "run"
    replay_of_run_id: str | None = None
    events_url: str
    status_url: str
    cancel_url: str


class CouncilLiveStatusResponse(BaseModel):
    run_id: str
    workspace_id: str | None = None
    status: Literal[
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    ]
    kind: Literal["run", "replay", "retry_failed"]
    replay_of_run_id: str | None = None
    event_count: int
    started_at: datetime
    finished_at: datetime | None = None


class CouncilLiveCancelResponse(BaseModel):
    run_id: str
    cancellation_requested: bool
    status: Literal[
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    ]


class CouncilPresetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    mode: Literal["universal", "crypto", "code", "documents"] = "universal"
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    members: list[CouncilMemberRequest] = Field(..., min_length=1, max_length=6)
    synthesizer_provider: str | None = Field(default=None, max_length=64)
    synthesizer_model: str | None = Field(default=None, max_length=255)
    reviewer_provider: str | None = Field(default=None, max_length=64)
    reviewer_model: str | None = Field(default=None, max_length=255)
    arbiter_provider: str | None = Field(default=None, max_length=64)
    arbiter_model: str | None = Field(default=None, max_length=255)
    delegation_max_calls: int = Field(default=4, ge=1, le=8)
    delegation_max_depth: int = Field(default=1, ge=1, le=1)
    member_timeout_seconds: int = Field(default=60, ge=5, le=600)

    @model_validator(mode="after")
    def validate_unique_members(self) -> "CouncilPresetCreate":
        CouncilRunRequest(
            question="preset validation",
            members=self.members,
            execution_mode=self.execution_mode,
            mode=self.mode,
            synthesizer_provider=self.synthesizer_provider,
            synthesizer_model=self.synthesizer_model,
            reviewer_provider=self.reviewer_provider,
            reviewer_model=self.reviewer_model,
            arbiter_provider=self.arbiter_provider,
            arbiter_model=self.arbiter_model,
            delegation_max_calls=self.delegation_max_calls,
            delegation_max_depth=self.delegation_max_depth,
            member_timeout_seconds=self.member_timeout_seconds,
        )
        return self


class CouncilPresetUpdate(CouncilPresetCreate):
    pass


class CouncilPreset(BaseModel):
    id: str
    workspace_id: str | None = None
    name: str
    description: str
    mode: str
    execution_mode: CouncilExecutionMode = CouncilExecutionMode.COUNCIL
    members: list[CouncilMemberRequest]
    synthesizer_provider: str | None = None
    synthesizer_model: str | None = None
    reviewer_provider: str | None = None
    reviewer_model: str | None = None
    arbiter_provider: str | None = None
    arbiter_model: str | None = None
    delegation_max_calls: int = 4
    delegation_max_depth: int = 1
    member_timeout_seconds: int
    created_at: datetime
    updated_at: datetime


class CouncilPresetList(BaseModel):
    workspace_id: str | None = None
    items: list[CouncilPreset]


class CouncilPresetDeleteResponse(BaseModel):
    preset_id: str
    deleted: bool


class CouncilBudgetPolicy(BaseModel):
    workspace_id: str | None = None
    monthly_budget_usd: float = Field(default=0.0, ge=0)
    per_run_soft_limit_usd: float = Field(default=0.0, ge=0)
    per_run_hard_limit_usd: float = Field(default=0.0, ge=0)
    approval_threshold_usd: float = Field(default=0.0, ge=0)
    unknown_cost_policy: Literal["allow", "require_approval", "block"] = "require_approval"
    expected_member_output_tokens: int = Field(default=1200, ge=100, le=32000)
    expected_synthesis_output_tokens: int = Field(default=1500, ge=100, le=32000)
    monthly_run_limit: int = Field(default=0, ge=0, le=1_000_000)
    monthly_token_limit: int = Field(default=0, ge=0, le=10_000_000_000)
    per_minute_run_limit: int = Field(default=0, ge=0, le=10_000)
    routing_mode: Literal["off", "advisory"] = "advisory"
    routing_min_savings_percent: float = Field(default=15.0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_limits(self) -> "CouncilBudgetPolicy":
        if self.per_run_hard_limit_usd and self.per_run_soft_limit_usd > self.per_run_hard_limit_usd:
            raise ValueError("per_run_soft_limit_usd cannot exceed per_run_hard_limit_usd.")
        return self


class CouncilBudgetPolicyUpdate(CouncilBudgetPolicy):
    workspace_id: None = None


class CouncilCostLine(BaseModel):
    kind: Literal["member", "synthesis", "draft", "reviewer", "planner", "delegate"]
    provider: str
    model: str
    label: str
    billing: str
    input_tokens_estimate: int
    output_tokens_estimate: int
    estimated_cost_usd: float | None = None
    pricing_known: bool


class CouncilCostEstimate(BaseModel):
    workspace_id: str | None = None
    currency: Literal["USD"] = "USD"
    estimate_status: Literal["known", "partial", "unknown"]
    estimated_cost_usd: float | None = None
    known_cost_usd: float = 0.0
    unknown_models: list[str] = Field(default_factory=list)
    monthly_estimated_spend_usd: float = 0.0
    monthly_actual_spend_usd: float = 0.0
    monthly_reserved_spend_usd: float = 0.0
    monthly_run_count: int = 0
    monthly_reserved_run_count: int = 0
    monthly_total_tokens: int = 0
    monthly_reserved_tokens: int = 0
    projected_monthly_spend_usd: float | None = None
    projected_monthly_run_count: int = 0
    projected_monthly_tokens: int = 0
    decision: Literal["allow", "approval_required", "blocked"]
    approval_required: bool
    reasons: list[str] = Field(default_factory=list)
    fingerprint: str
    lines: list[CouncilCostLine] = Field(default_factory=list)


class CouncilCostApproval(BaseModel):
    approval_id: str
    token: str
    workspace_id: str | None = None
    fingerprint: str
    estimated_cost_usd: float | None = None
    estimate_status: str
    expires_at: datetime


class CouncilCostUsage(BaseModel):
    workspace_id: str | None = None
    month: str
    actual_spend_usd: float = 0.0
    committed_spend_usd: float = 0.0
    reserved_spend_usd: float = 0.0
    completed_runs: int = 0
    reserved_runs: int = 0
    actual_total_tokens: int = 0
    reserved_tokens: int = 0
    monthly_budget_usd: float = 0.0
    monthly_run_limit: int = 0
    monthly_token_limit: int = 0
    budget_utilization_percent: float | None = None
    run_utilization_percent: float | None = None
    token_utilization_percent: float | None = None


class CouncilRoutingChange(BaseModel):
    kind: Literal["member", "synthesis", "draft", "reviewer", "planner", "delegate"]
    member_index: int | None = None
    provider: str
    from_model: str
    to_model: str
    from_estimated_cost_usd: float | None = None
    to_estimated_cost_usd: float
    estimated_savings_usd: float | None = None
    reason: str


class CouncilRoutingPlan(BaseModel):
    workspace_id: str | None = None
    available: bool
    original_estimate: CouncilCostEstimate
    routed_estimate: CouncilCostEstimate | None = None
    recommended_members: list[CouncilMemberRequest] = Field(default_factory=list)
    synthesizer_provider: str | None = None
    synthesizer_model: str | None = None
    reviewer_provider: str | None = None
    reviewer_model: str | None = None
    arbiter_provider: str | None = None
    arbiter_model: str | None = None
    changes: list[CouncilRoutingChange] = Field(default_factory=list)
    estimated_savings_usd: float | None = None
    reason: str = ""
