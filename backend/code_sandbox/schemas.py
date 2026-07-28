from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskLevel = Literal["none", "low", "medium", "high", "critical"]
VerificationStatus = Literal["not_run", "passed", "failed"]
SessionStatus = Literal["active", "inspected", "verified", "applied", "closed", "error"]
VerificationProfile = Literal["diff_check", "python_compile", "pytest", "frontend_build"]
AgentVerificationProfile = Literal["diff_check", "python_compile"]


class CodeSandboxCreateRequest(BaseModel):
    repo_path: str = Field(min_length=1, max_length=4096)
    base_ref: str = Field(default="HEAD", min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, max_length=64)


class CodeSandboxVerificationItem(BaseModel):
    profile: VerificationProfile
    status: Literal["passed", "failed", "skipped"]
    exit_code: int | None = None
    duration_ms: float = 0.0
    output: str = ""
    execution_backend: Literal["host", "docker"] = "host"
    runtime_run_id: str | None = None
    image: str | None = None


class CodeSandboxSession(BaseModel):
    id: str
    workspace_id: str | None
    repo_path: str
    worktree_path: str
    base_ref: str
    base_commit: str
    status: SessionStatus
    risk_level: RiskLevel
    approval_required: bool
    verification_status: VerificationStatus
    patch_fingerprint: str | None
    patch_sha256: str | None
    files_changed: int
    insertions: int
    deletions: int
    changed_paths: list[str]
    protected_paths: list[str]
    blocked_paths: list[str]
    verification: list[CodeSandboxVerificationItem]
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None
    closed_at: datetime | None


class CodeSandboxInspectResponse(BaseModel):
    session: CodeSandboxSession
    patch_preview: str
    patch_truncated: bool


class CodeSandboxVerifyRequest(BaseModel):
    profiles: list[VerificationProfile] = Field(default_factory=lambda: ["diff_check"], min_length=1, max_length=4)
    timeout_seconds: int = Field(default=120, ge=5, le=600)

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, value: list[VerificationProfile]) -> list[VerificationProfile]:
        return list(dict.fromkeys(value))


class CodeSandboxApprovalRequest(BaseModel):
    reason: str = Field(default="Human approval for verified patch.", max_length=1000)


class CodeSandboxApproval(BaseModel):
    approval_id: str
    token: str
    fingerprint: str
    expires_at: datetime


class CodeSandboxApplyRequest(BaseModel):
    approval_token: str | None = Field(default=None, max_length=512)


class CodeSandboxStatus(BaseModel):
    status: Literal["ready"] = "ready"
    git_available: bool
    worktree_isolation: bool = True
    os_process_isolation: bool = False
    isolated_runtime_available: bool = True
    supports_patch_fingerprint: bool = True
    supports_risk_classification: bool = True
    supports_protected_paths: bool = True
    supports_blocked_paths: bool = True
    supports_verification_profiles: bool = True
    supports_one_time_approval: bool = True
    supports_safe_apply: bool = True


CodeAgentRunStatus = Literal["created", "running", "completed", "blocked", "failed"]
CodeAgentOperationAction = Literal["write", "replace", "delete"]


class CodeAgentOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: CodeAgentOperationAction
    path: str = Field(min_length=1, max_length=4096)
    content: str | None = Field(default=None, max_length=524_288)
    old_text: str | None = Field(default=None, max_length=131_072)
    new_text: str | None = Field(default=None, max_length=131_072)
    reason: str = Field(default="", max_length=1000)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        if not normalized:
            raise ValueError("path не может быть пустым")
        return normalized


class CodeAgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(default="", max_length=4000)
    operations: list[CodeAgentOperation] = Field(min_length=1, max_length=16)


class CodeAgentRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(default="", max_length=20_000)
    council_run_id: str | None = Field(default=None, max_length=64)
    provider: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=255)
    context_paths: list[str] = Field(default_factory=list, max_length=24)
    writable_paths: list[str] = Field(min_length=1, max_length=16)
    verification_profiles: list[AgentVerificationProfile] = Field(default_factory=lambda: ["diff_check"], min_length=1, max_length=2)
    verification_timeout_seconds: int = Field(default=180, ge=5, le=600)
    auto_verify: bool = True

    @field_validator("context_paths", "writable_paths")
    @classmethod
    def normalize_paths(cls, value: list[str]) -> list[str]:
        normalized = []
        for raw in value:
            path = raw.strip().replace("\\", "/")
            if path and path not in normalized:
                normalized.append(path)
        return normalized

    @field_validator("verification_profiles")
    @classmethod
    def normalize_profiles(cls, value: list[AgentVerificationProfile]) -> list[AgentVerificationProfile]:
        return list(dict.fromkeys(value))

    @field_validator("task")
    @classmethod
    def validate_task(cls, value: str) -> str:
        return value.strip()


class CodeAgentOperationAudit(BaseModel):
    action: CodeAgentOperationAction
    path: str
    bytes: int = 0
    reason: str = ""


class CodeAgentRun(BaseModel):
    id: str
    session_id: str
    workspace_id: str | None = None
    council_run_id: str | None = None
    status: CodeAgentRunStatus
    provider: str | None = None
    model: str | None = None
    gateway_request_id: str | None = None
    task_sha256: str
    task_preview: str
    context_paths: list[str]
    writable_paths: list[str]
    operations: list[CodeAgentOperationAudit]
    summary: str
    error_message: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    actual_cost_usd: float | None = None
    cost_status: Literal["known", "unknown"] = "unknown"
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class CodeAgentRunResponse(BaseModel):
    run: CodeAgentRun
    sandbox: CodeSandboxSession
    inspect: CodeSandboxInspectResponse | None = None


RuntimeRunStatus = Literal["running", "passed", "failed", "timeout"]
RuntimeVerificationProfile = Literal["diff_check", "python_compile", "pytest", "frontend_build"]


class RuntimeImageStatus(BaseModel):
    profile: str
    image: str
    present: bool
    trusted: bool = False
    image_id: str | None = None


class IsolatedRuntimeStatus(BaseModel):
    backend: Literal["docker"] = "docker"
    enabled: bool
    docker_cli_available: bool
    docker_daemon_available: bool
    daemon_error: str = ""
    network_mode: Literal["none"] = "none"
    root_filesystem_read_only: bool = True
    source_mount_read_only: bool = True
    no_new_privileges: bool = True
    capabilities_dropped: bool = True
    images: list[RuntimeImageStatus] = Field(default_factory=list)


class IsolatedRuntimeRunRequest(BaseModel):
    profiles: list[RuntimeVerificationProfile] = Field(default_factory=lambda: ["python_compile"], min_length=1, max_length=4)
    timeout_seconds: int = Field(default=300, ge=5, le=1200)
    cpu_limit: float = Field(default=1.0, ge=0.1, le=4.0)
    memory_mb: int = Field(default=1024, ge=128, le=4096)
    pids_limit: int = Field(default=128, ge=16, le=512)
    collect_artifacts: bool = True

    @field_validator("profiles")
    @classmethod
    def unique_runtime_profiles(cls, value: list[RuntimeVerificationProfile]) -> list[RuntimeVerificationProfile]:
        return list(dict.fromkeys(value))


class IsolatedRuntimeRun(BaseModel):
    id: str
    session_id: str
    workspace_id: str | None = None
    profile: RuntimeVerificationProfile
    backend: Literal["docker"] = "docker"
    status: RuntimeRunStatus
    image: str
    image_id: str | None = None
    network_mode: Literal["none"] = "none"
    cpu_limit: float
    memory_mb: int
    pids_limit: int
    timeout_seconds: int
    exit_code: int | None = None
    duration_ms: float = 0.0
    timed_out: bool = False
    output: str = ""
    artifact_paths: list[str] = Field(default_factory=list)
    artifact_bytes: int = 0
    artifact_available: bool = False
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime


class IsolatedRuntimeRunResponse(BaseModel):
    session: CodeSandboxSession
    runs: list[IsolatedRuntimeRun]


class CodeAgentStatus(BaseModel):
    status: Literal["ready"] = "ready"
    shell_access: bool = False
    network_access: bool = False
    secret_access: bool = False
    arbitrary_path_access: bool = False
    structured_file_edits: bool = True
    explicit_writable_paths_required: bool = True
    council_handoff_supported: bool = True
    automatic_apply: bool = False
    os_process_isolation: bool = False
    isolated_runtime_available: bool = True
