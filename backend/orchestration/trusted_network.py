from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import re
from types import MappingProxyType
from typing import Any, Mapping, Protocol
from urllib.parse import urljoin

from backend.agent_governance.core import AgentCapability
from backend.agent_governance.enforcement_adapters import (
    TrustedAgentToolBinding,
)
from backend.agent_governance.invocation import (
    AgentTrustedInvocationError,
    TrustedAgentInvocationFacts,
    TrustedAgentInvocationResolver,
)


TRUSTED_NETWORK_TRANSPORT_SCHEMA_VERSION = (
    "p3-002.2b-c4a.trusted-network-transport"
)

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9a-z]+$")
_SAFE_RESULT_HEADERS = frozenset({"content-type"})

_FIXED_REQUEST_HEADERS = (
    (
        "accept",
        (
            "text/plain, text/html, application/json;q=0.9, "
            "application/xml;q=0.8"
        ),
    ),
    ("accept-encoding", "identity"),
    ("user-agent", "ai-studio-enterprise-trusted-network/1"),
)


class TrustedNetworkTransportError(RuntimeError):
    """Base error for the trusted network transport boundary."""


class TrustedNetworkBindingError(TrustedNetworkTransportError):
    """Invocation facts no longer match the trusted boundary."""


class TrustedNetworkResolutionError(TrustedNetworkTransportError):
    """DNS resolution failed or returned a non-public destination."""


class TrustedNetworkRedirectError(TrustedNetworkTransportError):
    """A redirect attempted to escape the approved destination."""


class TrustedNetworkResponseError(TrustedNetworkTransportError):
    """The wire response does not satisfy the transport contract."""


class TrustedNetworkResponseLimitError(TrustedNetworkResponseError):
    """The bounded response limit was exceeded."""


@dataclass(frozen=True)
class TrustedNetworkConnectPlan:
    """Transient HTTPS plan for a platform-owned requester.

    The requester must connect only to ``addresses`` while preserving
    ``hostname`` for HTTP Host and TLS SNI/certificate verification.
    It must not perform a second DNS lookup, use environment proxies,
    or auto-follow redirects.
    """

    url: str
    hostname: str
    addresses: tuple[str, ...]
    port: int
    method: str
    headers: tuple[tuple[str, str], ...]
    max_response_bytes: int


@dataclass(frozen=True)
class TrustedNetworkWireResponse:
    """One response from a non-redirect-following HTTPS requester."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class TrustedNetworkFetchResult:
    """Fetch result with a content-free evidence projection."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes
    external_domain: str
    redirect_count: int
    trusted_invocation_fingerprint: str

    def evidence(self) -> dict[str, Any]:
        payload = {
            "schema_version": (
                TRUSTED_NETWORK_TRANSPORT_SCHEMA_VERSION
            ),
            "external_domain": self.external_domain,
            "status_code": self.status_code,
            "response_bytes": len(self.body),
            "redirect_count": self.redirect_count,
            "trusted_invocation_fingerprint": (
                self.trusted_invocation_fingerprint
            ),
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            **payload,
            "fingerprint": fingerprint,
        }


class TrustedNetworkResolver(Protocol):
    async def resolve(
        self,
        *,
        hostname: str,
        port: int,
    ) -> tuple[str, ...]:
        """Resolve one exact hostname without connecting."""


class TrustedHttpsRequester(Protocol):
    async def get(
        self,
        plan: TrustedNetworkConnectPlan,
    ) -> TrustedNetworkWireResponse:
        """Execute one HTTPS GET using only the supplied plan."""


class TrustedNetworkFetchTransport:
    """C4a trusted HTTPS state machine.

    This class is deliberately not wired into ToolExecutionRuntime.
    It binds canonical C3 invocation facts to DNS resolution and a
    single-hop requester contract. Redirects are processed manually
    and may never change the exact approved hostname.
    """

    def __init__(
        self,
        *,
        resolver: TrustedNetworkResolver,
        requester: TrustedHttpsRequester,
        max_redirects: int = 3,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        if (
            not isinstance(max_redirects, int)
            or isinstance(max_redirects, bool)
            or not 0 <= max_redirects <= 10
        ):
            raise ValueError(
                "max_redirects must be an integer from 0 to 10."
            )
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or not 1 <= max_response_bytes <= 67_108_864
        ):
            raise ValueError(
                "max_response_bytes must be an integer "
                "from 1 to 67108864."
            )

        self._resolver = resolver
        self._requester = requester
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes
        self._invocation_resolver = (
            TrustedAgentInvocationResolver()
        )

    async def fetch(
        self,
        *,
        binding: TrustedAgentToolBinding,
        invocation_facts: TrustedAgentInvocationFacts,
        validated_input: dict[str, Any],
    ) -> TrustedNetworkFetchResult:
        expected = self._verify_boundary(
            binding=binding,
            invocation_facts=invocation_facts,
            validated_input=validated_input,
        )
        approved_host = expected.external_domain
        if approved_host is None:
            raise TrustedNetworkBindingError(
                "Trusted network invocation has no approved hostname."
            )

        raw_url = validated_input.get("url")
        if not isinstance(raw_url, str):
            raise TrustedNetworkBindingError(
                "Trusted network input no longer contains "
                "the canonical URL."
            )

        current_url = raw_url
        visited = {current_url}
        redirect_count = 0

        while True:
            hop_host = self._derive_redirect_host(
                binding=binding,
                url=current_url,
            )
            if hop_host != approved_host:
                raise TrustedNetworkRedirectError(
                    "Redirect changed the exact approved hostname."
                )

            addresses = await self._resolve_public_addresses(
                hop_host
            )
            plan = TrustedNetworkConnectPlan(
                url=current_url,
                hostname=hop_host,
                addresses=addresses,
                port=443,
                method="GET",
                headers=_FIXED_REQUEST_HEADERS,
                max_response_bytes=self._max_response_bytes,
            )

            try:
                raw_response = await self._requester.get(plan)
            except TrustedNetworkTransportError:
                raise
            except Exception as exc:
                raise TrustedNetworkTransportError(
                    "Trusted HTTPS requester failed closed."
                ) from exc

            response = self._validate_response(raw_response)
            location = None
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")

            if not location:
                terminal = self._validate_terminal_response(
                    response
                )
                return TrustedNetworkFetchResult(
                    status_code=terminal.status_code,
                    headers=terminal.headers,
                    body=terminal.body,
                    external_domain=approved_host,
                    redirect_count=redirect_count,
                    trusted_invocation_fingerprint=(
                        invocation_facts.fingerprint
                    ),
                )

            if redirect_count >= self._max_redirects:
                raise TrustedNetworkRedirectError(
                    "Trusted HTTPS redirect limit exceeded."
                )

            next_url = self._redirect_target(
                current_url=current_url,
                location=location,
            )
            next_host = self._derive_redirect_host(
                binding=binding,
                url=next_url,
            )
            if next_host != approved_host:
                raise TrustedNetworkRedirectError(
                    "Redirect changed the exact approved hostname."
                )
            if next_url in visited:
                raise TrustedNetworkRedirectError(
                    "Trusted HTTPS redirect loop detected."
                )

            visited.add(next_url)
            current_url = next_url
            redirect_count += 1

    def _verify_boundary(
        self,
        *,
        binding: TrustedAgentToolBinding,
        invocation_facts: TrustedAgentInvocationFacts,
        validated_input: dict[str, Any],
    ) -> TrustedAgentInvocationFacts:
        if not isinstance(binding, TrustedAgentToolBinding):
            raise TrustedNetworkBindingError(
                "TrustedAgentToolBinding is required."
            )
        if not isinstance(
            invocation_facts,
            TrustedAgentInvocationFacts,
        ):
            raise TrustedNetworkBindingError(
                "TrustedAgentInvocationFacts is required."
            )
        if not isinstance(validated_input, dict):
            raise TrustedNetworkBindingError(
                "Canonical validated network input is required."
            )

        if (
            binding.governed_tool_id != "network.fetch"
            or binding.action_id != "network.fetch"
            or tuple(binding.capabilities)
            != (AgentCapability.EXTERNAL_NETWORK,)
            or not binding.allow_network
        ):
            raise TrustedNetworkBindingError(
                "Trusted transport accepts only canonical "
                "network.fetch."
            )

        try:
            expected = self._invocation_resolver.derive(
                binding=binding,
                validated_input=validated_input,
            )
        except AgentTrustedInvocationError as exc:
            raise TrustedNetworkBindingError(
                "Trusted invocation could not be re-derived."
            ) from exc

        if expected.fingerprint != invocation_facts.fingerprint:
            raise TrustedNetworkBindingError(
                "Trusted invocation fingerprint no longer "
                "matches input."
            )

        return expected

    def _derive_redirect_host(
        self,
        *,
        binding: TrustedAgentToolBinding,
        url: str,
    ) -> str:
        try:
            facts = self._invocation_resolver.derive(
                binding=binding,
                validated_input={"url": url},
            )
        except AgentTrustedInvocationError as exc:
            raise TrustedNetworkRedirectError(
                "Redirect target violates the trusted "
                "HTTPS URL contract."
            ) from exc

        if facts.external_domain is None:
            raise TrustedNetworkRedirectError(
                "Redirect target has no trusted hostname."
            )
        return facts.external_domain

    async def _resolve_public_addresses(
        self,
        hostname: str,
    ) -> tuple[str, ...]:
        try:
            raw_addresses = await self._resolver.resolve(
                hostname=hostname,
                port=443,
            )
        except TrustedNetworkTransportError:
            raise
        except Exception as exc:
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution failed closed."
            ) from exc

        if (
            not isinstance(raw_addresses, tuple)
            or not raw_addresses
            or any(
                not isinstance(item, str)
                for item in raw_addresses
            )
        ):
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution returned no "
                "usable addresses."
            )

        canonical: list[str] = []
        for raw in raw_addresses:
            try:
                address = ipaddress.ip_address(raw.strip())
            except ValueError as exc:
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned an "
                    "invalid address."
                ) from exc

            if (
                not address.is_global
                or address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_unspecified
                or address.is_reserved
                or getattr(address, "is_site_local", False)
            ):
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned a "
                    "non-global address."
                )

            value = address.compressed
            if value not in canonical:
                canonical.append(value)

        if not canonical:
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution returned no "
                "usable addresses."
            )
        return tuple(canonical)

    def _validate_response(
        self,
        response: TrustedNetworkWireResponse,
    ) -> TrustedNetworkWireResponse:
        if not isinstance(
            response,
            TrustedNetworkWireResponse,
        ):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS requester returned an "
                "invalid response."
            )

        if (
            not isinstance(response.status_code, int)
            or isinstance(response.status_code, bool)
            or not 100 <= response.status_code <= 599
        ):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS response status is invalid."
            )

        headers = self._normalize_response_headers(
            response.headers
        )

        content_encoding = headers.get("content-encoding")
        if (
            content_encoding is not None
            and content_encoding.lower() != "identity"
        ):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS response content encoding "
                "must be identity."
            )

        if not isinstance(response.body, bytes):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS response body must be bytes."
            )
        if len(response.body) > self._max_response_bytes:
            raise TrustedNetworkResponseLimitError(
                "Trusted HTTPS response exceeded the "
                "bounded body limit."
            )

        return TrustedNetworkWireResponse(
            status_code=response.status_code,
            headers=MappingProxyType(headers),
            body=response.body,
        )

    @staticmethod
    def _validate_terminal_response(
        response: TrustedNetworkWireResponse,
    ) -> TrustedNetworkWireResponse:
        content_type = response.headers.get("content-type")
        if response.body:
            if content_type is None:
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS non-empty response requires "
                    "an explicit content type."
                )

            media_type = (
                content_type.split(";", 1)[0]
                .strip()
                .lower()
            )
            if not (
                media_type.startswith("text/")
                or media_type
                in {
                    "application/json",
                    "application/xml",
                    "application/xhtml+xml",
                }
                or media_type.endswith("+json")
                or media_type.endswith("+xml")
            ):
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response is not text-oriented."
                )

        safe_headers = {
            name: value
            for name, value in response.headers.items()
            if name in _SAFE_RESULT_HEADERS
        }
        return TrustedNetworkWireResponse(
            status_code=response.status_code,
            headers=MappingProxyType(safe_headers),
            body=response.body,
        )

    @staticmethod
    def _normalize_response_headers(
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        if not isinstance(headers, Mapping):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS response headers must "
                "be a mapping."
            )

        normalized: dict[str, str] = {}
        for raw_name, raw_value in headers.items():
            if (
                not isinstance(raw_name, str)
                or not isinstance(raw_value, str)
            ):
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response headers must "
                    "be strings."
                )

            name = raw_name.strip().lower()
            value = raw_value.strip()

            if (
                not name
                or not _HEADER_NAME.fullmatch(name)
            ):
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response contains an "
                    "invalid header name."
                )

            if any(
                ord(character) < 32
                or ord(character) == 127
                for character in value
            ):
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response contains an "
                    "invalid header value."
                )

            normalized[name] = value

        return normalized

    @staticmethod
    def _redirect_target(
        *,
        current_url: str,
        location: str,
    ) -> str:
        if (
            not isinstance(location, str)
            or not location
            or len(location) > 4096
            or location != location.strip()
            or any(
                ord(character) < 32
                or ord(character) == 127
                for character in location
            )
            or "\\" in location
        ):
            raise TrustedNetworkRedirectError(
                "Trusted HTTPS redirect Location is invalid."
            )

        try:
            target = urljoin(current_url, location)
        except ValueError as exc:
            raise TrustedNetworkRedirectError(
                "Trusted HTTPS redirect Location is invalid."
            ) from exc

        if not target or len(target) > 4096:
            raise TrustedNetworkRedirectError(
                "Trusted HTTPS redirect target is invalid."
            )

        return target


__all__ = [
    "TRUSTED_NETWORK_TRANSPORT_SCHEMA_VERSION",
    "TrustedHttpsRequester",
    "TrustedNetworkBindingError",
    "TrustedNetworkConnectPlan",
    "TrustedNetworkFetchResult",
    "TrustedNetworkFetchTransport",
    "TrustedNetworkRedirectError",
    "TrustedNetworkResolutionError",
    "TrustedNetworkResolver",
    "TrustedNetworkResponseError",
    "TrustedNetworkResponseLimitError",
    "TrustedNetworkTransportError",
    "TrustedNetworkWireResponse",
]
