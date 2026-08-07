import pytest

from backend.agent_governance import (
    AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION,
    AgentContextCheckpoint,
    AgentContextCheckpointIntegrityError,
)


PROFILE_FINGERPRINT = "a" * 64
COMMIT_SHA = "b" * 40


def make_checkpoint(**overrides: object) -> AgentContextCheckpoint:
    values: dict[str, object] = {
        "objective": "Implement P3-002.1a policy contracts.",
        "confirmed_facts": (
            "P3-001 is released as v0.17.0.",
            "Alembic head is 20260806_0057.",
        ),
        "changed_files": (
            "backend/agent_governance/core.py",
            "tests/test_agent_policy_profile_core.py",
        ),
        "test_results": ("12 targeted tests passed.",),
        "open_questions": (),
        "blocked_actions": ("commit", "push"),
        "branch": "feature/p3-002",
        "current_commit": COMMIT_SHA,
        "migration_head": "20260806_0057",
        "next_safe_action": "Run the independent permission review.",
        "workspace_id": "workspace-1",
        "profile_id": "safe-development",
        "profile_fingerprint": PROFILE_FINGERPRINT,
    }
    values.update(overrides)
    return AgentContextCheckpoint(**values)


def test_checkpoint_round_trip_is_content_bounded_and_deterministic() -> None:
    checkpoint = make_checkpoint(
        changed_files=(
            "backend\\agent_governance\\core.py",
            "tests/test_agent_policy_profile_core.py",
            "backend/agent_governance/core.py",
        ),
        current_commit=COMMIT_SHA.upper(),
    )

    assert checkpoint.changed_files == (
        "backend/agent_governance/core.py",
        "tests/test_agent_policy_profile_core.py",
    )
    assert checkpoint.current_commit == COMMIT_SHA
    assert len(checkpoint.fingerprint) == 64

    restored = AgentContextCheckpoint.from_dict(checkpoint.to_dict())

    assert restored == checkpoint
    assert restored.fingerprint == checkpoint.fingerprint
    assert restored.to_dict()["schema_version"] == (
        AGENT_CONTEXT_CHECKPOINT_SCHEMA_VERSION
    )


def test_checkpoint_envelope_detects_tampering() -> None:
    checkpoint = make_checkpoint()
    envelope = checkpoint.to_envelope()

    restored = AgentContextCheckpoint.from_envelope(envelope)
    assert restored == checkpoint

    envelope["next_safe_action"] = "Push without approval."
    with pytest.raises(
        AgentContextCheckpointIntegrityError,
        match="fingerprint",
    ):
        AgentContextCheckpoint.from_envelope(envelope)

    envelope = checkpoint.to_envelope()
    envelope["fingerprint"] = "invalid"
    with pytest.raises(
        AgentContextCheckpointIntegrityError,
        match="fingerprint",
    ):
        AgentContextCheckpoint.from_envelope(envelope)


@pytest.mark.parametrize(
    "changed_file",
    [
        "../secrets.txt",
        "/etc/passwd",
        "C:/Users/operator/.env",
        "C:secret.txt",
        "C:Users/operator/.env",
        "backend/../../.env",
        "backend/file.txt:secret-stream",
        "backend/file.py\x00ignored",
    ],
)
def test_checkpoint_rejects_non_repository_relative_paths(
    changed_file: str,
) -> None:
    with pytest.raises(ValueError, match="changed_files"):
        make_checkpoint(changed_files=(changed_file,))


def test_checkpoint_requires_profile_identity_and_fingerprint_together() -> None:
    with pytest.raises(ValueError, match="profile_id"):
        make_checkpoint(profile_id=None)

    with pytest.raises(ValueError, match="profile_fingerprint"):
        make_checkpoint(profile_fingerprint=None)


def test_checkpoint_rejects_scalar_lists_and_unsafe_identity_text() -> None:
    with pytest.raises(ValueError, match="changed_files must be an array"):
        make_checkpoint(changed_files="backend/file.py")

    with pytest.raises(ValueError, match="confirmed_facts must be an array"):
        make_checkpoint(confirmed_facts="one fact")

    with pytest.raises(ValueError, match="branch"):
        make_checkpoint(branch="feature branch")

    with pytest.raises(ValueError, match="branch"):
        make_checkpoint(branch="feature/.hidden")

    with pytest.raises(ValueError, match="branch"):
        make_checkpoint(branch="feature/foo.lock/bar")

    with pytest.raises(ValueError, match="workspace_id"):
        make_checkpoint(workspace_id="workspace-1\nworkspace-2")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("objective", "", "objective"),
        ("next_safe_action", "", "next_safe_action"),
        ("current_commit", "not-a-commit", "current_commit"),
        ("migration_head", "head with spaces", "migration_head"),
        ("profile_fingerprint", "abc", "profile_fingerprint"),
    ],
)
def test_checkpoint_rejects_invalid_identity_fields(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_checkpoint(**{field: value})


def test_checkpoint_rejects_unknown_or_wrong_schema_fields() -> None:
    payload = make_checkpoint().to_dict()
    payload["raw_document_content"] = "must not be accepted"
    with pytest.raises(ValueError, match="Unsupported checkpoint fields"):
        AgentContextCheckpoint.from_dict(payload)

    payload = make_checkpoint().to_dict()
    payload["schema_version"] = "future-version"
    with pytest.raises(ValueError, match="schema_version"):
        AgentContextCheckpoint.from_dict(payload)
