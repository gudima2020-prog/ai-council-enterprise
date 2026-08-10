from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from backend.agent_governance.core import (
    AgentCapability,
    AgentFilesystemAccess,
    AgentNetworkAccess,
    AgentPolicyProfile,
)
from backend.runtime_policy import DataClassification


_PROFILE_VERSION = "1.0.0"
_AUDIT_VERSION = "2026.08.07"
_PRIMARY_MODEL = "workspace.default"
_REVIEWER_MODEL = "workspace.reviewer"

_TOOL_CAPABILITIES: dict[str, tuple[AgentCapability, ...]] = {
    "build.run": (
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.CODE_EXECUTION,
    ),
    "documents.analyze": (AgentCapability.FILESYSTEM_READ,),
    "documents.preview": (AgentCapability.FILESYSTEM_READ,),
    "documents.read": (AgentCapability.FILESYSTEM_READ,),
    "filesystem.read": (AgentCapability.FILESYSTEM_READ,),
    "filesystem.write": (AgentCapability.FILESYSTEM_WRITE,),
    "gateway.preflight": (),
    "git.commit": (
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.REPOSITORY_COMMIT,
    ),
    "git.diff": (AgentCapability.FILESYSTEM_READ,),
    "git.push": (
        AgentCapability.REMOTE_MUTATION,
        AgentCapability.EXTERNAL_NETWORK,
    ),
    "git.read": (AgentCapability.FILESYSTEM_READ,),
    "git.stage": (
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.REPOSITORY_STAGE,
    ),
    "github.actions.read": (AgentCapability.EXTERNAL_NETWORK,),
    "github.actions.write": (
        AgentCapability.REMOTE_MUTATION,
        AgentCapability.EXTERNAL_NETWORK,
    ),
    "github.pr.read": (AgentCapability.EXTERNAL_NETWORK,),
    "github.pr.write": (
        AgentCapability.REMOTE_MUTATION,
        AgentCapability.EXTERNAL_NETWORK,
    ),
    "models.infer": (),
    "network.fetch": (AgentCapability.EXTERNAL_NETWORK,),
    "production.deploy": (
        AgentCapability.REMOTE_MUTATION,
        AgentCapability.EXTERNAL_NETWORK,
        AgentCapability.PRODUCTION_CHANGE,
    ),
    "production.inspect": (AgentCapability.EXTERNAL_NETWORK,),
    "release.publish": (
        AgentCapability.REMOTE_MUTATION,
        AgentCapability.EXTERNAL_NETWORK,
    ),
    "repository.search": (AgentCapability.FILESYSTEM_READ,),
    "runtime.execute": (
        AgentCapability.FILESYSTEM_WRITE,
        AgentCapability.CODE_EXECUTION,
    ),
    "sources.read": (AgentCapability.FILESYSTEM_READ,),
    "tests.read": (AgentCapability.FILESYSTEM_READ,),
    "tests.run": (AgentCapability.CODE_EXECUTION,),
}


def agent_tool_capability_catalog() -> Mapping[str, tuple[AgentCapability, ...]]:
    return MappingProxyType(dict(_TOOL_CAPABILITIES))


def _profile(
    *,
    profile_id: str,
    allowed_tools: tuple[str, ...],
    denied_actions: tuple[str, ...],
    mandatory_checks: tuple[str, ...],
    approval_conditions: tuple[AgentCapability, ...],
    external_domains: tuple[str, ...],
    allowed_classifications: tuple[DataClassification, ...],
    network_access: AgentNetworkAccess,
    filesystem_access: AgentFilesystemAccess,
) -> AgentPolicyProfile:
    return AgentPolicyProfile(
        profile_id=profile_id,
        version=_PROFILE_VERSION,
        audit_version=_AUDIT_VERSION,
        allowed_tools=allowed_tools,
        tool_capabilities={
            tool_id: _TOOL_CAPABILITIES[tool_id]
            for tool_id in allowed_tools
        },
        denied_actions=denied_actions,
        mandatory_checks=mandatory_checks,
        approval_conditions=approval_conditions,
        external_domains=external_domains,
        allowed_classifications=allowed_classifications,
        primary_model=_PRIMARY_MODEL,
        reviewer_model=_REVIEWER_MODEL,
        network_access=network_access,
        filesystem_access=filesystem_access,
    )


_BUILT_IN_AGENT_PROFILES = (
    _profile(
        profile_id="document-analysis",
        allowed_tools=(
            "documents.analyze",
            "documents.preview",
            "documents.read",
            "gateway.preflight",
            "models.infer",
        ),
        denied_actions=(
            "documents.external_export",
            "filesystem.write",
            "secrets.export",
        ),
        mandatory_checks=(
            "documents.explicit_sources",
            "policy.runtime",
            "review.independent",
        ),
        approval_conditions=(),
        external_domains=(),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
            DataClassification.CONFIDENTIAL,
            DataClassification.RESTRICTED,
        ),
        network_access=AgentNetworkAccess.DENIED,
        filesystem_access=AgentFilesystemAccess.READ_ONLY,
    ),
    _profile(
        profile_id="production-operator",
        allowed_tools=(
            "build.run",
            "filesystem.read",
            "filesystem.write",
            "github.actions.read",
            "github.actions.write",
            "production.deploy",
            "production.inspect",
            "runtime.execute",
        ),
        denied_actions=(
            "approval.bypass",
            "git.force_push",
            "production.unreviewed_change",
            "secrets.export",
        ),
        mandatory_checks=(
            "approval.exact_scope",
            "evidence.append_only",
            "policy.runtime",
            "production.rollback_plan",
            "review.independent",
        ),
        approval_conditions=(
            AgentCapability.FILESYSTEM_WRITE,
            AgentCapability.REMOTE_MUTATION,
            AgentCapability.EXTERNAL_NETWORK,
            AgentCapability.PRODUCTION_CHANGE,
        ),
        external_domains=("api.github.com", "github.com"),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
            DataClassification.CONFIDENTIAL,
            DataClassification.RESTRICTED,
        ),
        network_access=AgentNetworkAccess.RESTRICTED,
        filesystem_access=AgentFilesystemAccess.READ_WRITE,
    ),
    _profile(
        profile_id="release-manager",
        allowed_tools=(
            "build.run",
            "filesystem.read",
            "git.commit",
            "git.diff",
            "git.push",
            "git.read",
            "git.stage",
            "github.pr.read",
            "github.pr.write",
            "release.publish",
            "tests.run",
        ),
        denied_actions=(
            "git.force_push",
            "git.reset_hard",
            "release.unverified",
            "tests.unverified_claim",
        ),
        mandatory_checks=(
            "build.frontend",
            "review.simplification",
            "review.spec",
            "review.standards",
            "tests.full_backend",
            "tests.targeted",
        ),
        approval_conditions=(
            AgentCapability.REPOSITORY_STAGE,
            AgentCapability.REPOSITORY_COMMIT,
            AgentCapability.REMOTE_MUTATION,
            AgentCapability.EXTERNAL_NETWORK,
        ),
        external_domains=("api.github.com", "github.com"),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ),
        network_access=AgentNetworkAccess.RESTRICTED,
        filesystem_access=AgentFilesystemAccess.READ_WRITE,
    ),
    _profile(
        profile_id="repository-review",
        allowed_tools=(
            "filesystem.read",
            "git.diff",
            "git.read",
            "repository.search",
            "tests.read",
        ),
        denied_actions=(
            "filesystem.write",
            "git.commit",
            "git.push",
            "git.stage",
            "repository.mutate",
        ),
        mandatory_checks=(
            "review.simplification",
            "review.spec",
            "review.standards",
        ),
        approval_conditions=(),
        external_domains=(),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
            DataClassification.CONFIDENTIAL,
        ),
        network_access=AgentNetworkAccess.DENIED,
        filesystem_access=AgentFilesystemAccess.READ_ONLY,
    ),
    _profile(
        profile_id="research-readonly",
        allowed_tools=(
            "filesystem.read",
            "network.fetch",
            "repository.search",
            "sources.read",
        ),
        denied_actions=(
            "filesystem.write",
            "git.commit",
            "git.push",
            "repository.mutate",
            "secrets.export",
        ),
        mandatory_checks=(
            "research.citations",
            "research.source_attribution",
            "review.spec",
        ),
        approval_conditions=(AgentCapability.EXTERNAL_NETWORK,),
        external_domains=(
            "arxiv.org",
            "docs.python.org",
            "github.com",
        ),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ),
        network_access=AgentNetworkAccess.RESTRICTED,
        filesystem_access=AgentFilesystemAccess.READ_ONLY,
    ),
    _profile(
        profile_id="safe-development",
        allowed_tools=(
            "build.run",
            "filesystem.read",
            "filesystem.write",
            "git.diff",
            "git.read",
            "git.stage",
            "repository.search",
            "runtime.execute",
            "tests.run",
        ),
        denied_actions=(
            "git.branch.main_write",
            "git.force_push",
            "git.reset_hard",
            "secrets.export",
            "tests.unverified_claim",
        ),
        mandatory_checks=(
            "review.simplification",
            "review.spec",
            "review.standards",
            "tests.full_backend",
            "tests.targeted",
        ),
        approval_conditions=(
            AgentCapability.REPOSITORY_STAGE,
            AgentCapability.REPOSITORY_COMMIT,
            AgentCapability.REMOTE_MUTATION,
            AgentCapability.EXTERNAL_NETWORK,
        ),
        external_domains=(
            "api.github.com",
            "docs.python.org",
            "github.com",
        ),
        allowed_classifications=(
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
            DataClassification.CONFIDENTIAL,
        ),
        network_access=AgentNetworkAccess.RESTRICTED,
        filesystem_access=AgentFilesystemAccess.READ_WRITE,
    ),
)


BUILT_IN_AGENT_PROFILE_IDS = tuple(
    profile.profile_id
    for profile in _BUILT_IN_AGENT_PROFILES
)


def built_in_agent_profiles() -> tuple[AgentPolicyProfile, ...]:
    return _BUILT_IN_AGENT_PROFILES
