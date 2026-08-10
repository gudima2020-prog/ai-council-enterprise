from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.agent_governance.core import (
    AgentCapability,
    AgentFilesystemAccess,
    AgentNetworkAccess,
    AgentPolicyProfile,
)
from backend.runtime_policy import DataClassification


class AgentPolicyProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., min_length=1, max_length=64)
    version: str = Field(..., min_length=1, max_length=64)
    audit_version: str = Field(..., min_length=1, max_length=64)
    allowed_tools: list[str] = Field(..., min_length=1, max_length=128)
    tool_capabilities: dict[str, list[AgentCapability]]
    denied_actions: list[str] = Field(default_factory=list, max_length=128)
    mandatory_checks: list[str] = Field(..., min_length=1, max_length=128)
    approval_conditions: list[AgentCapability] = Field(
        default_factory=list,
        max_length=16,
    )
    external_domains: list[str] = Field(default_factory=list, max_length=128)
    allowed_classifications: list[DataClassification] = Field(
        ...,
        min_length=1,
        max_length=8,
    )
    primary_model: str = Field(..., min_length=1, max_length=255)
    reviewer_model: str = Field(..., min_length=1, max_length=255)
    network_access: AgentNetworkAccess = AgentNetworkAccess.DENIED
    filesystem_access: AgentFilesystemAccess = (
        AgentFilesystemAccess.READ_ONLY
    )

    def to_domain(self) -> AgentPolicyProfile:
        return AgentPolicyProfile(
            profile_id=self.profile_id,
            version=self.version,
            audit_version=self.audit_version,
            allowed_tools=tuple(self.allowed_tools),
            tool_capabilities={
                tool_id: tuple(capabilities)
                for tool_id, capabilities in self.tool_capabilities.items()
            },
            denied_actions=tuple(self.denied_actions),
            mandatory_checks=tuple(self.mandatory_checks),
            approval_conditions=tuple(self.approval_conditions),
            external_domains=tuple(self.external_domains),
            allowed_classifications=tuple(self.allowed_classifications),
            primary_model=self.primary_model,
            reviewer_model=self.reviewer_model,
            network_access=self.network_access,
            filesystem_access=self.filesystem_access,
        )


class AgentPolicyProfileSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., min_length=1, max_length=64)
    version: str = Field(..., min_length=1, max_length=64)
    expected_fingerprint: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
