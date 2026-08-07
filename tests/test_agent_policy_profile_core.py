import pytest

from backend.agent_governance import (
    BUILT_IN_AGENT_PROFILE_IDS,
    AgentCapability,
    AgentFilesystemAccess,
    AgentNetworkAccess,
    AgentPolicyAction,
    AgentPolicyDecision,
    AgentPolicyEvaluator,
    AgentPolicyProfile,
    AgentProfileNotFoundError,
    AgentProfileRegistry,
    AgentToolRequest,
    built_in_agent_profiles,
)
from backend.runtime_policy import DataClassification


def make_profile(**overrides: object) -> AgentPolicyProfile:
    values: dict[str, object] = {
        "profile_id": "test-profile",
        "version": "1.0.0",
        "audit_version": "2026.08.07",
        "allowed_tools": (
            "filesystem.read",
            "filesystem.write",
            "network.fetch",
            "tests.run",
        ),
        "tool_capabilities": {
            "filesystem.read": (AgentCapability.FILESYSTEM_READ,),
            "filesystem.write": (AgentCapability.FILESYSTEM_WRITE,),
            "network.fetch": (AgentCapability.EXTERNAL_NETWORK,),
            "tests.run": (AgentCapability.CODE_EXECUTION,),
        },
        "denied_actions": (
            "git.force_push",
            "git.reset_hard",
        ),
        "mandatory_checks": (
            "tests.targeted",
            "review.standards",
        ),
        "approval_conditions": (
            AgentCapability.FILESYSTEM_WRITE,
            AgentCapability.EXTERNAL_NETWORK,
        ),
        "external_domains": ("api.github.com",),
        "allowed_classifications": (
            DataClassification.PUBLIC,
            DataClassification.INTERNAL,
        ),
        "primary_model": "workspace.default",
        "reviewer_model": "workspace.reviewer",
        "network_access": AgentNetworkAccess.RESTRICTED,
        "filesystem_access": AgentFilesystemAccess.READ_WRITE,
    }
    values.update(overrides)
    return AgentPolicyProfile(**values)


def test_profile_normalizes_manifest_and_has_stable_fingerprint() -> None:
    profile = make_profile(
        profile_id=" Test-Profile ",
        allowed_tools=("tests.run", "filesystem.read", "tests.run"),
        tool_capabilities={
            "tests.run": (AgentCapability.CODE_EXECUTION,),
            "filesystem.read": (AgentCapability.FILESYSTEM_READ,),
        },
        denied_actions=("git.reset_hard", "git.reset_hard"),
        mandatory_checks=("review.standards", "tests.targeted"),
        external_domains=("API.GITHUB.COM",),
        allowed_classifications=(
            DataClassification.INTERNAL,
            DataClassification.PUBLIC,
        ),
    )
    same = make_profile(
        allowed_tools=("filesystem.read", "tests.run"),
        tool_capabilities={
            "filesystem.read": (AgentCapability.FILESYSTEM_READ,),
            "tests.run": (AgentCapability.CODE_EXECUTION,),
        },
        denied_actions=("git.reset_hard",),
        mandatory_checks=("tests.targeted", "review.standards"),
    )

    assert profile.profile_id == "test-profile"
    assert profile.allowed_tools == ("filesystem.read", "tests.run")
    assert profile.to_dict()["tool_capabilities"] == {
        "filesystem.read": ["filesystem_read"],
        "tests.run": ["code_execution"],
    }
    assert profile.external_domains == ("api.github.com",)
    assert profile.fingerprint == same.fingerprint
    assert len(profile.fingerprint) == 64
    assert profile.to_dict()["allowed_classifications"] == [
        "public",
        "internal",
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"allowed_tools": ()}, "allowed_tools"),
        ({"mandatory_checks": ()}, "mandatory_checks"),
        ({"allowed_classifications": ()}, "allowed_classifications"),
        (
            {
                "network_access": AgentNetworkAccess.DENIED,
                "external_domains": ("api.github.com",),
            },
            "external_domains",
        ),
        (
            {
                "network_access": AgentNetworkAccess.RESTRICTED,
                "external_domains": ("*",),
            },
            "wildcard",
        ),
        (
            {
                "network_access": AgentNetworkAccess.ALLOWED,
                "external_domains": ("*",),
            },
            "wildcard",
        ),
        (
            {"reviewer_model": "workspace.default"},
            "reviewer_model",
        ),
    ],
)
def test_profile_rejects_incomplete_or_incoherent_manifest(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_profile(**overrides)


def test_profile_rejects_scalar_where_manifest_requires_array() -> None:
    with pytest.raises(ValueError, match="allowed_tools must be an array"):
        make_profile(allowed_tools="tests.run")

    with pytest.raises(
        ValueError,
        match="allowed_classifications must be an array",
    ):
        make_profile(allowed_classifications="public")


def test_profile_requires_exact_capability_binding_for_every_tool() -> None:
    with pytest.raises(ValueError, match="missing allowed tools"):
        make_profile(tool_capabilities={})

    bindings = dict(make_profile().tool_capabilities)
    bindings["shell.execute"] = ()
    with pytest.raises(ValueError, match="unknown tools"):
        make_profile(tool_capabilities=bindings)


def test_evaluator_allows_only_as_a_profile_level_decision() -> None:
    profile = make_profile(approval_conditions=())
    request = AgentToolRequest(
        tool_id="tests.run",
        action_id="tests.targeted",
        data_classification=DataClassification.INTERNAL,
    )

    decision = AgentPolicyEvaluator().evaluate(profile, request)

    assert decision.action == AgentPolicyAction.ALLOW
    assert decision.allowed_by_profile is True
    assert decision.canonical_policy_required is True
    assert decision.reason_codes == ("PROFILE_CONSTRAINTS_SATISFIED",)
    assert decision.mandatory_checks == profile.mandatory_checks
    assert decision.to_dict()["request"] == request.to_dict()
    assert decision.to_dict()["canonical_policy_required"] is True
    assert len(decision.fingerprint) == 64

    with pytest.raises(TypeError, match="canonical_policy_required"):
        AgentPolicyDecision(
            profile_id=profile.profile_id,
            profile_fingerprint=profile.fingerprint,
            action=AgentPolicyAction.ALLOW,
            reason_codes=("PROFILE_CONSTRAINTS_SATISFIED",),
            mandatory_checks=profile.mandatory_checks,
            triggered_approval_conditions=(),
            effective_capabilities=decision.effective_capabilities,
            request=request,
            canonical_policy_required=False,
        )


def test_evaluator_denies_unknown_tool_and_forbidden_action_before_approval() -> None:
    profile = make_profile()
    request = AgentToolRequest(
        tool_id="shell.execute",
        action_id="git.force_push",
        data_classification=DataClassification.INTERNAL,
        capabilities=(AgentCapability.FILESYSTEM_WRITE,),
    )

    decision = AgentPolicyEvaluator().evaluate(profile, request)

    assert decision.action == AgentPolicyAction.DENY
    assert decision.allowed_by_profile is False
    assert decision.reason_codes == (
        "PROFILE_TOOL_NOT_ALLOWED",
        "PROFILE_ACTION_DENIED",
    )
    assert decision.triggered_approval_conditions == (
        AgentCapability.FILESYSTEM_WRITE,
    )


def test_evaluator_denies_disallowed_classification() -> None:
    decision = AgentPolicyEvaluator().evaluate(
        make_profile(),
        AgentToolRequest(
            tool_id="filesystem.read",
            action_id="repository.inspect",
            data_classification=DataClassification.CONFIDENTIAL,
            capabilities=(AgentCapability.FILESYSTEM_READ,),
        ),
    )

    assert decision.action == AgentPolicyAction.DENY
    assert decision.reason_codes == ("PROFILE_CLASSIFICATION_DENIED",)


@pytest.mark.parametrize(
    ("filesystem_access", "capability", "reason_code"),
    [
        (
            AgentFilesystemAccess.NONE,
            AgentCapability.FILESYSTEM_READ,
            "PROFILE_FILESYSTEM_DENIED",
        ),
        (
            AgentFilesystemAccess.READ_ONLY,
            AgentCapability.FILESYSTEM_WRITE,
            "PROFILE_FILESYSTEM_WRITE_DENIED",
        ),
    ],
)
def test_evaluator_enforces_filesystem_boundary(
    filesystem_access: AgentFilesystemAccess,
    capability: AgentCapability,
    reason_code: str,
) -> None:
    decision = AgentPolicyEvaluator().evaluate(
        make_profile(filesystem_access=filesystem_access),
        AgentToolRequest(
            tool_id="filesystem.read",
            action_id="repository.inspect",
            data_classification=DataClassification.INTERNAL,
            capabilities=(capability,),
        ),
    )

    assert decision.action == AgentPolicyAction.DENY
    assert decision.reason_codes == (reason_code,)


def test_evaluator_requires_exact_external_domain_and_human_approval() -> None:
    evaluator = AgentPolicyEvaluator()
    profile = make_profile()

    denied = evaluator.evaluate(
        profile,
        AgentToolRequest(
            tool_id="network.fetch",
            action_id="research.fetch",
            data_classification=DataClassification.PUBLIC,
            capabilities=(AgentCapability.EXTERNAL_NETWORK,),
            external_domain="example.com",
        ),
    )
    approval = evaluator.evaluate(
        profile,
        AgentToolRequest(
            tool_id="network.fetch",
            action_id="research.fetch",
            data_classification=DataClassification.PUBLIC,
            capabilities=(AgentCapability.EXTERNAL_NETWORK,),
            external_domain="API.GITHUB.COM",
        ),
    )

    assert denied.action == AgentPolicyAction.DENY
    assert denied.reason_codes == ("PROFILE_DOMAIN_NOT_ALLOWED",)
    assert approval.action == AgentPolicyAction.REQUIRE_APPROVAL
    assert approval.reason_codes == (
        "PROFILE_APPROVAL_EXTERNAL_NETWORK",
    )
    assert approval.triggered_approval_conditions == (
        AgentCapability.EXTERNAL_NETWORK,
    )

    subdomain = evaluator.evaluate(
        profile,
        AgentToolRequest(
            tool_id="network.fetch",
            action_id="research.fetch",
            data_classification=DataClassification.PUBLIC,
            capabilities=(AgentCapability.EXTERNAL_NETWORK,),
            external_domain="raw.api.github.com",
        ),
    )
    assert subdomain.action == AgentPolicyAction.DENY
    assert subdomain.reason_codes == ("PROFILE_DOMAIN_NOT_ALLOWED",)


def test_tool_request_rejects_network_capability_without_exact_domain() -> None:
    with pytest.raises(ValueError, match="external_domain"):
        AgentToolRequest(
            tool_id="network.fetch",
            action_id="research.fetch",
            data_classification=DataClassification.PUBLIC,
            capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        )


def test_external_domain_implies_network_capability() -> None:
    request = AgentToolRequest(
        tool_id="network.fetch",
        action_id="research.fetch",
        data_classification=DataClassification.PUBLIC,
        external_domain="api.github.com",
    )

    assert request.capabilities == (AgentCapability.EXTERNAL_NETWORK,)


@pytest.mark.parametrize(
    ("profile_id", "tool_id", "action_id", "expected_action"),
    [
        (
            "release-manager",
            "git.commit",
            "git.commit",
            AgentPolicyAction.REQUIRE_APPROVAL,
        ),
        (
            "release-manager",
            "git.push",
            "git.push",
            AgentPolicyAction.DENY,
        ),
        (
            "production-operator",
            "production.deploy",
            "production.deploy",
            AgentPolicyAction.DENY,
        ),
        (
            "production-operator",
            "filesystem.write",
            "filesystem.write",
            AgentPolicyAction.REQUIRE_APPROVAL,
        ),
        (
            "production-operator",
            "build.run",
            "build.run",
            AgentPolicyAction.REQUIRE_APPROVAL,
        ),
        (
            "production-operator",
            "runtime.execute",
            "runtime.execute",
            AgentPolicyAction.REQUIRE_APPROVAL,
        ),
        (
            "research-readonly",
            "network.fetch",
            "research.fetch",
            AgentPolicyAction.DENY,
        ),
    ],
)
def test_built_in_dangerous_tool_cannot_bypass_capability_binding(
    profile_id: str,
    tool_id: str,
    action_id: str,
    expected_action: AgentPolicyAction,
) -> None:
    registry = AgentProfileRegistry(built_in_agent_profiles())

    decision = registry.evaluate(
        profile_id,
        AgentToolRequest(
            tool_id=tool_id,
            action_id=action_id,
            data_classification=DataClassification.INTERNAL,
        ),
    )

    assert decision.action == expected_action
    assert decision.action != AgentPolicyAction.ALLOW
    assert decision.effective_capabilities


def test_built_in_registry_contains_six_auditable_profiles() -> None:
    profiles = built_in_agent_profiles()
    registry = AgentProfileRegistry(profiles)

    assert tuple(profile.profile_id for profile in profiles) == (
        BUILT_IN_AGENT_PROFILE_IDS
    )
    assert BUILT_IN_AGENT_PROFILE_IDS == (
        "document-analysis",
        "production-operator",
        "release-manager",
        "repository-review",
        "research-readonly",
        "safe-development",
    )
    assert registry.list_ids() == BUILT_IN_AGENT_PROFILE_IDS
    assert all(profile.mandatory_checks for profile in profiles)
    assert all(profile.primary_model for profile in profiles)
    assert all(profile.reviewer_model for profile in profiles)
    assert len({profile.fingerprint for profile in profiles}) == 6


def test_registry_fails_closed_for_unknown_or_duplicate_profile() -> None:
    registry = AgentProfileRegistry((make_profile(),))

    with pytest.raises(AgentProfileNotFoundError, match="missing-profile"):
        registry.get("missing-profile")

    with pytest.raises(ValueError, match="Duplicate"):
        AgentProfileRegistry((make_profile(), make_profile()))
