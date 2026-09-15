from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlsplit

from backend.agent_governance.approval import fingerprint_agent_tool_input
from backend.agent_governance.core import AgentCapability
from backend.agent_governance.enforcement_adapters import (
    TrustedAgentToolBinding,
)


AGENT_TRUSTED_INVOCATION_SCHEMA_VERSION = (
    "p3-002.2b-c3a.trusted-invocation"
)

_FIXED_ACTION_RESOLVER_ID = "binding.fixed-action"
_FIXED_ACTION_RESOLVER_VERSION = "1"
_NETWORK_FETCH_RESOLVER_ID = "network.fetch.https-url"
_NETWORK_FETCH_RESOLVER_VERSION = "1"

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_DNS_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class AgentTrustedInvocationError(ValueError):
    pass


class AgentTrustedDestinationError(AgentTrustedInvocationError):
    pass


class AgentTrustedDestinationUnavailableError(
    AgentTrustedInvocationError
):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        _canonical(value).encode("utf-8")
    ).hexdigest()


def _identifier(
    value: Any,
    field_name: str,
    *,
    max_length: int = 128,
) -> str:
    if not isinstance(value, str):
        raise AgentTrustedInvocationError(
            f"{field_name} must be a string."
        )
    normalized = value.strip().lower()
    if (
        not normalized
        or len(normalized) > max_length
        or not _IDENTIFIER.fullmatch(normalized)
    ):
        raise AgentTrustedInvocationError(f"Invalid {field_name}.")
    return normalized


def _digest(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise AgentTrustedInvocationError(
            f"{field_name} must be a SHA-256 digest."
        )
    return value.lower()


def _text(
    value: Any,
    field_name: str,
    *,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise AgentTrustedInvocationError(
            f"{field_name} must be a string."
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(character) < 32 for character in normalized)
    ):
        raise AgentTrustedInvocationError(f"Invalid {field_name}.")
    return normalized


def _normalize_dns_hostname(value: str) -> str:
    raw = _text(
        value,
        "external_domain",
        max_length=253,
    ).lower()
    if raw.endswith("."):
        raw = raw[:-1]

    try:
        normalized = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise AgentTrustedDestinationError(
            "External destination hostname is not valid IDNA."
        ) from exc

    if not normalized or not _DNS_HOSTNAME.fullmatch(normalized):
        raise AgentTrustedDestinationError(
            "External destination must be an exact DNS hostname."
        )

    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise AgentTrustedDestinationError(
            "IP-literal destinations are not accepted."
        )

    labels = normalized.split(".")
    if labels and all(label.isdigit() for label in labels):
        raise AgentTrustedDestinationError(
            "Numeric host aliases are not accepted."
        )
    return normalized


def _network_fetch_destination(
    validated_input: dict[str, Any],
) -> str:
    raw_url = validated_input.get("url")
    if (
        not isinstance(raw_url, str)
        or not raw_url
        or len(raw_url) > 4096
        or raw_url != raw_url.strip()
        or any(ord(character) < 32 for character in raw_url)
        or "\\" in raw_url
    ):
        raise AgentTrustedDestinationError(
            "network.fetch requires one bounded canonical URL string."
        )

    try:
        parsed = urlsplit(raw_url)
    except ValueError as exc:
        raise AgentTrustedDestinationError(
            "network.fetch URL is malformed."
        ) from exc

    if parsed.scheme.lower() != "https":
        raise AgentTrustedDestinationError(
            "network.fetch trusted destination accepts HTTPS only."
        )
    if not parsed.netloc or parsed.hostname is None:
        raise AgentTrustedDestinationError(
            "network.fetch requires an absolute hostname."
        )
    if parsed.username is not None or parsed.password is not None:
        raise AgentTrustedDestinationError(
            "network.fetch URL credentials are not permitted."
        )
    if parsed.fragment:
        raise AgentTrustedDestinationError(
            "network.fetch URL fragments are not permitted."
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise AgentTrustedDestinationError(
            "network.fetch URL port is invalid."
        ) from exc

    if port not in {None, 443}:
        raise AgentTrustedDestinationError(
            "network.fetch permits only HTTPS port 443."
        )

    return _normalize_dns_hostname(parsed.hostname)


@dataclass(frozen=True)
class TrustedAgentInvocationFacts:
    """Content-free security facts from trusted binding + validated input.

    This contract does not authorize execution and does not provide a network
    transport. A future transport must enforce the same destination on connect
    and redirects; DNS/address pinning is outside this pure derivation layer.
    """

    registry_tool_id: str
    governed_tool_id: str
    action_id: str
    binding_fingerprint: str
    input_fingerprint: str
    external_domain: str | None
    resolver_id: str
    resolver_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registry_tool_id",
            _text(
                self.registry_tool_id,
                "registry_tool_id",
                max_length=255,
            ),
        )
        object.__setattr__(
            self,
            "governed_tool_id",
            _identifier(
                self.governed_tool_id,
                "governed_tool_id",
            ),
        )
        object.__setattr__(
            self,
            "action_id",
            _identifier(self.action_id, "action_id"),
        )
        object.__setattr__(
            self,
            "binding_fingerprint",
            _digest(
                self.binding_fingerprint,
                "binding_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "input_fingerprint",
            _digest(
                self.input_fingerprint,
                "input_fingerprint",
            ),
        )
        object.__setattr__(
            self,
            "external_domain",
            None
            if self.external_domain is None
            else _normalize_dns_hostname(self.external_domain),
        )
        object.__setattr__(
            self,
            "resolver_id",
            _identifier(self.resolver_id, "resolver_id"),
        )
        object.__setattr__(
            self,
            "resolver_version",
            _text(
                self.resolver_version,
                "resolver_version",
                max_length=64,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_TRUSTED_INVOCATION_SCHEMA_VERSION,
            "registry_tool_id": self.registry_tool_id,
            "governed_tool_id": self.governed_tool_id,
            "action_id": self.action_id,
            "binding_fingerprint": self.binding_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "external_domain": self.external_domain,
            "resolver_id": self.resolver_id,
            "resolver_version": self.resolver_version,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(self.to_dict())


class TrustedAgentInvocationResolver:
    """Code-owned derivation; caller/model fields are never authority.

    Only network.fetch has an audited destination rule in C3a. Every other
    network-capable governed tool remains fail-closed until its own resolver
    and transport enforcement contract are implemented.
    """

    def derive(
        self,
        *,
        binding: TrustedAgentToolBinding,
        validated_input: dict[str, Any],
    ) -> TrustedAgentInvocationFacts:
        if not isinstance(binding, TrustedAgentToolBinding):
            raise AgentTrustedInvocationError(
                "TrustedAgentToolBinding is required."
            )
        if not isinstance(validated_input, dict):
            raise AgentTrustedInvocationError(
                "validated_input must be the canonical tool input object."
            )

        input_fingerprint = fingerprint_agent_tool_input(
            validated_input
        )
        uses_network = (
            AgentCapability.EXTERNAL_NETWORK
            in binding.capabilities
        )

        if uses_network:
            if binding.governed_tool_id != "network.fetch":
                raise AgentTrustedDestinationUnavailableError(
                    "No audited destination resolver exists for governed "
                    f"tool {binding.governed_tool_id}."
                )
            external_domain = _network_fetch_destination(
                validated_input
            )
            resolver_id = _NETWORK_FETCH_RESOLVER_ID
            resolver_version = _NETWORK_FETCH_RESOLVER_VERSION
        else:
            external_domain = None
            resolver_id = _FIXED_ACTION_RESOLVER_ID
            resolver_version = _FIXED_ACTION_RESOLVER_VERSION

        return TrustedAgentInvocationFacts(
            registry_tool_id=binding.registry_tool_id,
            governed_tool_id=binding.governed_tool_id,
            action_id=binding.action_id,
            binding_fingerprint=binding.fingerprint,
            input_fingerprint=input_fingerprint,
            external_domain=external_domain,
            resolver_id=resolver_id,
            resolver_version=resolver_version,
        )


__all__ = [
    "AGENT_TRUSTED_INVOCATION_SCHEMA_VERSION",
    "AgentTrustedDestinationError",
    "AgentTrustedDestinationUnavailableError",
    "AgentTrustedInvocationError",
    "TrustedAgentInvocationFacts",
    "TrustedAgentInvocationResolver",
]
