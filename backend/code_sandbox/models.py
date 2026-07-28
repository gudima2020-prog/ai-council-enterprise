from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class CodeSandboxSessionModel(Base):
    __tablename__ = "code_sandbox_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inspected', 'verified', 'applied', 'closed', 'error')",
            name="ck_code_sandbox_sessions_status",
        ),
        CheckConstraint(
            "risk_level IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_code_sandbox_sessions_risk_level",
        ),
        CheckConstraint(
            "verification_status IN ('not_run', 'passed', 'failed')",
            name="ck_code_sandbox_sessions_verification_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    repo_path: Mapped[str] = mapped_column(Text, nullable=False)
    worktree_path: Mapped[str] = mapped_column(Text, nullable=False)
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_commit: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="none", nullable=False, index=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), default="not_run", nullable=False)
    patch_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    patch_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patch_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    insertions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    protected_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    blocked_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    verification_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approvals: Mapped[list["CodeSandboxApprovalModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["CodeSandboxAgentRunModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    runtime_runs: Mapped[list["CodeSandboxRuntimeRunModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class CodeSandboxApprovalModel(Base):
    __tablename__ = "code_sandbox_approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'used', 'expired', 'revoked')",
            name="ck_code_sandbox_approvals_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[CodeSandboxSessionModel] = relationship(back_populates="approvals")


class CodeSandboxAgentRunModel(Base):
    __tablename__ = "code_sandbox_agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'running', 'completed', 'blocked', 'failed')",
            name="ck_code_sandbox_agent_runs_status",
        ),
        CheckConstraint(
            "cost_status IN ('known', 'unknown')",
            name="ck_code_sandbox_agent_runs_cost_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    council_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("council_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False, index=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gateway_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    task_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    context_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    writable_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    operations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[CodeSandboxSessionModel] = relationship(back_populates="agent_runs")


class CodeSandboxRuntimeRunModel(Base):
    __tablename__ = "code_sandbox_runtime_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'passed', 'failed', 'timeout')",
            name="ck_code_sandbox_runtime_runs_status",
        ),
        CheckConstraint(
            "backend IN ('docker')",
            name="ck_code_sandbox_runtime_runs_backend",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("code_sandbox_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    profile: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    backend: Mapped[str] = mapped_column(String(16), default="docker", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False, index=True)
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    image_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    network_mode: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    cpu_limit: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    memory_mb: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    pids_limit: Mapped[int] = mapped_column(Integer, default=128, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    output: Mapped[str] = mapped_column(Text, default="", nullable=False)
    artifact_paths_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    artifact_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[CodeSandboxSessionModel] = relationship(back_populates="runtime_runs")
