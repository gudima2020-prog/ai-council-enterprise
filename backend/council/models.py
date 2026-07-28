from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class CouncilRunModel(Base):
    __tablename__ = "council_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'partial', 'failed', 'cancelled')",
            name="ck_council_runs_status",
        ),
        CheckConstraint(
            "mode IN ('universal', 'crypto', 'code', 'documents')",
            name="ck_council_runs_mode",
        ),
        CheckConstraint(
            "member_count >= 1 AND member_count <= 6",
            name="ck_council_runs_member_count",
        ),
        CheckConstraint(
            "execution_mode IN ('solo', 'council', 'best_of_n', 'review', 'arbitration', 'delegate')",
            name="ck_council_runs_execution_mode",
        ),
        CheckConstraint(
            "successful_member_count >= 0 "
            "AND successful_member_count <= member_count",
            name="ck_council_runs_successful_count",
        ),
        CheckConstraint(
            "confidence IS NULL OR "
            "(confidence >= 0 AND confidence <= 100)",
            name="ck_council_runs_confidence",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    replay_of_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("council_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    estimated_cost_usd: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    cost_estimate_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    cost_approval_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    cost_reservation_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    actual_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    execution_mode: Mapped[str] = mapped_column(
        String(24), default="council", nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    synthesizer_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    synthesizer_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    synthesis_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    synthesis_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    synthesis_requested_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    synthesis_status: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    synthesis_fallback_used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    synthesis_fallback_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    final_answer: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    consensus_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    disagreements_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    recommendations_json: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    confidence: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    successful_member_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    duration_ms: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
    )
    error_message: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )

    members: Mapped[list["CouncilRunMemberModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="CouncilRunMemberModel.ordinal",
    )
    cost_entries: Mapped[list["CouncilCostLedgerModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="CouncilCostLedgerModel.line_key",
    )


class CouncilRunMemberModel(Base):
    __tablename__ = "council_run_members"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "ordinal",
            name="uq_council_run_members_ordinal",
        ),
        CheckConstraint(
            "status IN ('success', 'error')",
            name="ck_council_run_members_status",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_council_run_members_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_council_run_members_output_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_council_run_members_total_tokens",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=lambda: new_id("councilmember"),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("council_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
    )
    fallback_used: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    fallback_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    answer: Mapped[str] = mapped_column(
        Text,
        default="",
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    run: Mapped[CouncilRunModel] = relationship(back_populates="members")


class CouncilPresetModel(Base):
    __tablename__ = "council_presets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_council_presets_workspace_name",
        ),
        CheckConstraint(
            "mode IN ('universal', 'crypto', 'code', 'documents')",
            name="ck_council_presets_mode",
        ),
        CheckConstraint(
            "member_timeout_seconds >= 5 AND member_timeout_seconds <= 600",
            name="ck_council_presets_timeout",
        ),
        CheckConstraint(
            "execution_mode IN ('solo', 'council', 'best_of_n', 'review', 'arbitration', 'delegate')",
            name="ck_council_presets_execution_mode",
        ),
        CheckConstraint(
            "delegation_max_calls >= 1 AND delegation_max_calls <= 8",
            name="ck_council_presets_delegation_calls",
        ),
        CheckConstraint(
            "delegation_max_depth = 1",
            name="ck_council_presets_delegation_depth",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("councilpreset")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_mode: Mapped[str] = mapped_column(
        String(24), default="council", nullable=False
    )
    members_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    synthesizer_provider: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    synthesizer_model: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    reviewer_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewer_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arbiter_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arbiter_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    delegation_max_calls: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    delegation_max_depth: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    member_timeout_seconds: Mapped[int] = mapped_column(
        Integer, default=60, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CouncilCostApprovalModel(Base):
    __tablename__ = "council_cost_approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approved')",
            name="ck_council_cost_approvals_decision",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("councilapproval")
    )
    token: Mapped[str] = mapped_column(String(96), unique=True, nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), default="approved", nullable=False)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class CouncilCostReservationModel(Base):
    __tablename__ = "council_cost_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'expired')",
            name="ck_council_cost_reservations_status",
        ),
        CheckConstraint(
            "estimate_status IN ('known', 'partial', 'unknown')",
            name="ck_council_cost_reservations_estimate_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("councilreserve")
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="reserved", nullable=False, index=True
    )
    estimate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class CouncilCostLedgerModel(Base):
    __tablename__ = "council_cost_ledger"
    __table_args__ = (
        UniqueConstraint("run_id", "line_key", name="uq_council_cost_ledger_run_line"),
        CheckConstraint(
            "kind IN ('member', 'synthesis', 'draft', 'reviewer', 'planner', 'delegate')",
            name="ck_council_cost_ledger_kind",
        ),
        CheckConstraint(
            "cost_source IN ('provider', 'calculated', 'free', 'unknown')",
            name="ck_council_cost_ledger_source",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_council_cost_ledger_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_council_cost_ledger_output_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_council_cost_ledger_total_tokens",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("councilcost")
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("council_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    line_key: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_model: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_reported_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_source: Mapped[str] = mapped_column(String(16), nullable=False)
    pricing_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    run: Mapped[CouncilRunModel] = relationship(back_populates="cost_entries")
