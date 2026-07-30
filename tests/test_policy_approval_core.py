from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.policy_approvals import (
    MAX_POLICY_APPROVAL_TTL_SECONDS,
    MIN_POLICY_APPROVAL_TTL_SECONDS,
    PolicyApprovalCore,
    PolicyApprovalExpiredError,
    PolicyApprovalScope,
    PolicyApprovalScopeError,
    PolicyApprovalStateError,
    PolicyApprovalStatus,
    PolicyApprovalTokenError,
)
from backend.runtime_policy import PolicyOperation


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
POLICY_FINGERPRINT = "a" * 64


def make_scope(
    *,
    workspace_id: str | None = "workspace_alpha",
    operation: PolicyOperation = PolicyOperation.MODEL_INFERENCE,
    policy_fingerprint: str = POLICY_FINGERPRINT,
    subject_id: str = "request_001",
    payload: dict[str, object] | None = None,
) -> PolicyApprovalScope:
    return PolicyApprovalScope(
        workspace_id=workspace_id,
        operation=operation,
        policy_version="p2-011.1",
        policy_fingerprint=policy_fingerprint,
        subject_type="gateway_route",
        subject_id=subject_id,
        subject_payload=payload
        or {
            "provider": "svrtr",
            "model": "claude-sonnet",
            "request_fingerprint": "b" * 64,
        },
    )


def make_pending(
    *,
    scope: PolicyApprovalScope | None = None,
    ttl_seconds: int = 900,
):
    return PolicyApprovalCore.request(
        scope=scope or make_scope(),
        reason_codes=(
            "CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",
        ),
        requested_by="operator",
        ttl_seconds=ttl_seconds,
        now=NOW,
        approval_id="policy_approval_test",
    )


def test_scope_fingerprint_is_deterministic_for_json_key_order() -> None:
    first = make_scope(
        payload={
            "provider": "svrtr",
            "model": "claude-sonnet",
        }
    )
    second = make_scope(
        payload={
            "model": "claude-sonnet",
            "provider": "svrtr",
        }
    )

    assert first.fingerprint == second.fingerprint


def test_scope_fingerprint_changes_with_exact_subject() -> None:
    first = make_scope(subject_id="request_001")
    second = make_scope(subject_id="request_002")

    assert first.fingerprint != second.fingerprint


def test_request_creates_pending_record_without_token_material() -> None:
    record = make_pending()

    assert record.status == PolicyApprovalStatus.PENDING
    assert record.token_hash is None
    assert record.expires_at == NOW + timedelta(minutes=15)
    assert "token_hash" not in record.to_public_dict()


def test_reason_codes_are_normalized_and_deduplicated() -> None:
    record = PolicyApprovalCore.request(
        scope=make_scope(),
        reason_codes=(
            " confidential_external_approval_required ",
            "CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",
        ),
        now=NOW,
    )

    assert record.reason_codes == (
        "CONFIDENTIAL_EXTERNAL_APPROVAL_REQUIRED",
    )


def test_approve_returns_one_time_token_but_stores_only_hash() -> None:
    pending = make_pending()
    token = "t" * 43

    grant = PolicyApprovalCore.approve(
        pending,
        decided_by="security_operator",
        note="Approved for this exact provider route.",
        now=NOW + timedelta(minutes=1),
        token=token,
    )

    assert grant.token == token
    assert grant.record.status == PolicyApprovalStatus.APPROVED
    assert grant.record.token_hash == PolicyApprovalCore.hash_token(token)
    assert token not in str(grant.record.to_storage_dict())


def test_approved_token_is_consumed_exactly_once() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW + timedelta(minutes=1),
        token="x" * 43,
    )

    consumed = PolicyApprovalCore.consume(
        grant.record,
        token=grant.token,
        scope=scope,
        now=NOW + timedelta(minutes=2),
    )

    assert consumed.status == PolicyApprovalStatus.CONSUMED
    assert consumed.consumed_at == NOW + timedelta(minutes=2)

    with pytest.raises(PolicyApprovalStateError):
        PolicyApprovalCore.consume(
            consumed,
            token=grant.token,
            scope=scope,
            now=NOW + timedelta(minutes=3),
        )


def test_wrong_token_is_rejected_without_consuming_record() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW,
        token="x" * 43,
    )

    with pytest.raises(PolicyApprovalTokenError):
        PolicyApprovalCore.consume(
            grant.record,
            token="y" * 43,
            scope=scope,
            now=NOW + timedelta(seconds=1),
        )

    assert grant.record.status == PolicyApprovalStatus.APPROVED


def test_workspace_mismatch_is_rejected() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW,
        token="x" * 43,
    )

    with pytest.raises(
        PolicyApprovalScopeError,
        match="another Workspace",
    ):
        PolicyApprovalCore.consume(
            grant.record,
            token=grant.token,
            scope=make_scope(workspace_id="workspace_beta"),
            now=NOW + timedelta(seconds=1),
        )


def test_policy_decision_change_invalidates_approval() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW,
        token="x" * 43,
    )

    with pytest.raises(
        PolicyApprovalScopeError,
        match="fingerprint changed",
    ):
        PolicyApprovalCore.consume(
            grant.record,
            token=grant.token,
            scope=make_scope(policy_fingerprint="c" * 64),
            now=NOW + timedelta(seconds=1),
        )


def test_subject_payload_change_invalidates_approval() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW,
        token="x" * 43,
    )
    changed = make_scope(
        payload={
            "provider": "svrtr",
            "model": "different-model",
            "request_fingerprint": "b" * 64,
        }
    )

    with pytest.raises(
        PolicyApprovalScopeError,
        match="subject scope changed",
    ):
        PolicyApprovalCore.consume(
            grant.record,
            token=grant.token,
            scope=changed,
            now=NOW + timedelta(seconds=1),
        )


def test_pending_request_expires_fail_closed() -> None:
    pending = make_pending(ttl_seconds=60)

    expired = PolicyApprovalCore.expire(
        pending,
        now=NOW + timedelta(seconds=60),
    )

    assert expired.status == PolicyApprovalStatus.EXPIRED
    assert expired.expired_at == NOW + timedelta(seconds=60)


def test_expired_request_cannot_be_approved() -> None:
    pending = make_pending(ttl_seconds=60)

    with pytest.raises(PolicyApprovalExpiredError) as exc_info:
        PolicyApprovalCore.approve(
            pending,
            decided_by="security_operator",
            now=NOW + timedelta(seconds=60),
        )

    assert exc_info.value.record is not None
    assert (
        exc_info.value.record.status
        == PolicyApprovalStatus.EXPIRED
    )


def test_approved_grant_also_expires_fail_closed() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope, ttl_seconds=60),
        decided_by="security_operator",
        now=NOW + timedelta(seconds=30),
        token="x" * 43,
    )

    with pytest.raises(PolicyApprovalExpiredError):
        PolicyApprovalCore.consume(
            grant.record,
            token=grant.token,
            scope=scope,
            now=NOW + timedelta(seconds=60),
        )


def test_deny_is_terminal_and_cannot_be_reapproved() -> None:
    denied = PolicyApprovalCore.deny(
        make_pending(),
        decided_by="security_operator",
        note="Provider route is not approved.",
        now=NOW + timedelta(seconds=1),
    )

    assert denied.status == PolicyApprovalStatus.DENIED
    assert denied.terminal is True

    with pytest.raises(PolicyApprovalStateError):
        PolicyApprovalCore.approve(
            denied,
            decided_by="another_operator",
            now=NOW + timedelta(seconds=2),
        )


def test_revoke_invalidates_an_approved_grant() -> None:
    scope = make_scope()
    grant = PolicyApprovalCore.approve(
        make_pending(scope=scope),
        decided_by="security_operator",
        now=NOW,
        token="x" * 43,
    )
    revoked = PolicyApprovalCore.revoke(
        grant.record,
        revoked_by="security_operator",
        note="Risk context changed.",
        now=NOW + timedelta(seconds=1),
    )

    assert revoked.status == PolicyApprovalStatus.REVOKED

    with pytest.raises(PolicyApprovalStateError):
        PolicyApprovalCore.consume(
            revoked,
            token=grant.token,
            scope=scope,
            now=NOW + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    "ttl_seconds",
    [
        MIN_POLICY_APPROVAL_TTL_SECONDS - 1,
        MAX_POLICY_APPROVAL_TTL_SECONDS + 1,
    ],
)
def test_ttl_outside_bounds_is_rejected(
    ttl_seconds: int,
) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        make_pending(ttl_seconds=ttl_seconds)


def test_invalid_policy_fingerprint_is_rejected() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        make_scope(policy_fingerprint="not-a-sha256")


def test_non_json_scope_payload_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="JSON-serializable",
    ):
        make_scope(payload={"invalid": object()})


def test_naive_datetimes_are_normalized_to_utc() -> None:
    naive = datetime(2026, 7, 30, 12, 0)
    record = PolicyApprovalCore.request(
        scope=make_scope(),
        reason_codes=("APPROVAL_REQUIRED",),
        now=naive,
    )

    assert record.requested_at.tzinfo == timezone.utc
    assert record.expires_at.tzinfo == timezone.utc
