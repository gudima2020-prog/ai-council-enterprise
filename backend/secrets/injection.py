from __future__ import annotations

from copy import deepcopy
from typing import Any

from backend.secrets.schemas import SecretLeaseCreate, SecretLeaseRevokeRequest, SecretResolveContext
from backend.secrets.service import SecretManagerService


REDACTED_SECRET = "[REDACTED_SECRET]"


class SecretAccessorError(RuntimeError):
    pass


class SecretLeaseAccessor:
    """Lazy, lease-backed secret accessor for trusted runtime components.

    Secret values are never placed into a task/tool payload or persisted in the
    database. Values live only in this short-lived object and are redacted from
    handler outputs or exceptions before those values can be persisted.
    """

    def __init__(
        self,
        *,
        manager: SecretManagerService,
        bindings: dict[str, str],
        context: SecretResolveContext,
        ttl_seconds: int = 60,
    ) -> None:
        self._manager = manager
        self._bindings = {
            str(alias).strip(): str(reference).strip()
            for alias, reference in bindings.items()
            if str(alias).strip() and str(reference).strip()
        }
        self._context = context
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._values: dict[str, str] = {}
        self._lease_ids: dict[str, str] = {}
        self._lease_tokens: dict[str, str] = {}
        self._closed = False

    @property
    def aliases(self) -> list[str]:
        return sorted(self._bindings)

    @property
    def closed(self) -> bool:
        return self._closed

    def __repr__(self) -> str:
        return (
            f"SecretLeaseAccessor(aliases={self.aliases!r}, "
            f"resolved={sorted(self._values)!r}, closed={self._closed})"
        )

    async def get(self, alias: str) -> str:
        if self._closed:
            raise SecretAccessorError("Secret accessor is closed.")
        key = str(alias).strip()
        if key in self._values:
            return self._values[key]
        reference = self._bindings.get(key)
        if reference is None:
            raise SecretAccessorError(f"Unknown secret alias: {key}.")

        lease = await self._manager.issue_lease(
            SecretLeaseCreate(
                reference=reference,
                workspace_id=self._context.workspace_id,
                actor_id=self._context.actor_id,
                consumer_type=self._context.consumer_type,
                consumer_key=self._context.consumer_key,
                purpose=self._context.purpose,
                source=self._context.source,
                auth_method=self._context.auth_method,
                ttl_seconds=self._ttl_seconds,
                max_uses=1,
                created_by=self._context.actor_id,
                correlation_id=self._context.correlation_id,
                metadata={
                    "ephemeral": True,
                    "alias": key,
                    **dict(self._context.metadata),
                },
            )
        )
        token = str(lease["lease_token"])
        value = await self._manager.resolve_lease(token, self._context)
        self._values[key] = value
        self._lease_ids[key] = str(lease["id"])
        self._lease_tokens[key] = token
        return value

    def redact_text(self, value: str) -> str:
        result = str(value)
        sensitive_values = [*self._values.values(), *self._lease_tokens.values()]
        for secret_value in sorted(
            {item for item in sensitive_values if item},
            key=len,
            reverse=True,
        ):
            result = result.replace(secret_value, REDACTED_SECRET)
        return result

    def redact_object(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {
                key: self.redact_object(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.redact_object(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact_object(item) for item in value]
        return deepcopy(value)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for alias, lease_id in list(self._lease_ids.items()):
            try:
                await self._manager.revoke_lease(
                    lease_id,
                    SecretLeaseRevokeRequest(
                        actor_id=self._context.actor_id,
                        reason=f"Ephemeral secret accessor closed ({alias}).",
                    ),
                    expected_workspace_id=self._context.workspace_id,
                )
            except Exception:
                # A single-use lease is normally already consumed. Closing the
                # accessor must never mask the real tool/gateway result.
                pass
        self._values.clear()
        self._lease_tokens.clear()


async def build_secret_accessor(
    *,
    manager: SecretManagerService,
    bindings: dict[str, str],
    context: SecretResolveContext,
    ttl_seconds: int = 60,
) -> SecretLeaseAccessor:
    accessor = SecretLeaseAccessor(
        manager=manager,
        bindings=bindings,
        context=context,
        ttl_seconds=ttl_seconds,
    )
    return accessor
