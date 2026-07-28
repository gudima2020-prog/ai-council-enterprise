from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MissionMemoryCategory(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    ARTIFACT = "artifact"
    LESSON = "lesson"
    CONSTRAINT = "constraint"
    SUMMARY = "summary"


class MissionEvidenceType(StrEnum):
    ARTIFACT = "artifact"
    METRIC = "metric"
    EXECUTION_RESULT = "execution_result"
    DOCUMENT = "document"
    EXTERNAL_REFERENCE = "external_reference"
    HUMAN_ATTESTATION = "human_attestation"
    TEST_RESULT = "test_result"


class MissionEvidenceStatus(StrEnum):
    SUBMITTED = "submitted"
    EVALUATED = "evaluated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class MissionGoalConfirmationDecision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    REOPENED = "reopened"


class MissionMemoryUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str | None = Field(default=None, max_length=64)
    category: MissionMemoryCategory = MissionMemoryCategory.FACT
    content: dict[str, Any] = Field(default_factory=dict)
    source_type: str = Field(default="manual", min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=255)
    importance_score: int = Field(default=50, ge=0, le=100)
    confidence_score: int = Field(default=50, ge=0, le=100)
    expires_at: datetime | None = None
    created_by: str = Field(default="user", min_length=1, max_length=255)


class MissionEvidencePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    required_evidence_count: int = Field(default=1, ge=1, le=100)
    min_individual_score: float = Field(default=70.0, ge=0, le=100)
    min_average_score: float = Field(default=75.0, ge=0, le=100)
    require_distinct_sources: bool = False
    min_distinct_sources: int = Field(default=1, ge=1, le=100)
    require_human_review: bool = True
    auto_confirm_enabled: bool = False
    allowed_evidence_types: list[MissionEvidenceType] = Field(
        default_factory=lambda: list(MissionEvidenceType),
        max_length=50,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy(self) -> "MissionEvidencePolicyUpsert":
        if self.min_distinct_sources > self.required_evidence_count:
            raise ValueError(
                "min_distinct_sources не может превышать required_evidence_count."
            )
        if self.require_human_review:
            self.auto_confirm_enabled = False
        return self


class MissionEvidenceSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cycle_id: str | None = Field(default=None, max_length=64)
    evidence_type: MissionEvidenceType
    source_type: str = Field(default="manual", min_length=1, max_length=64)
    source_ref: str | None = Field(default=None, max_length=255)
    submitted_by: str = Field(default="user", min_length=1, max_length=255)
    content: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    auto_evaluate: bool = True
    auto_confirm: bool = True


class MissionEvidenceEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_confirm: bool = True
    evaluator_ref: str = Field(
        default="builtin.evidence-evaluator",
        min_length=1,
        max_length=255,
    )


class MissionEvidenceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(accept|reject)$")
    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
    auto_confirm: bool = True


class MissionGoalEvidenceEvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_confirm: bool = True


class MissionGoalConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MissionGoalReopenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(default="user", min_length=1, max_length=255)
    reason: str = Field(..., min_length=1, max_length=4000)
    progress_percent: float = Field(default=0.0, ge=0, lt=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
