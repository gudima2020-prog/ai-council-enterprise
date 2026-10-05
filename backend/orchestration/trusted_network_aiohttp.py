from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from backend.orchestration.trusted_network import (
    TrustedNetworkConnectPlan,
    TrustedNetworkResolutionError,
    TrustedNetworkResponseError,
    TrustedNetworkResponseLimitError,
    TrustedNetworkTransportError,
    TrustedNetworkWireResponse,
)


TRUSTED_NETWORK_AIOHTTP_SCHEMA_VERSION = (
    "p3-002.2b-c4b1.aiohttp-trusted-https"
)

_MAX_HEADER_COUNT = 64
_MAX_HEADER_BYTES = 32_768
_MAX_HEADER_NAME_BYTES = 256
_MAX_HEADER_VALUE_BYTES = 8_192
_MAX_BODY_BYTES = 67_108_864
_STREAM_CHUNK_BYTES = 65_536

_SECURITY_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "content-encoding",
        "location",
    }
)
_DNS_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)

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
_ALLOWED_REQUEST_HEADERS = frozenset(
    name for name, _ in _FIXED_REQUEST_HEADERS
)

DnsLookup = Callable[
    [str, int],
    Awaitable[Sequence[tuple[Any, ...]]],
]


def _public_ip(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if not isinstance(raw, str) or not raw or "%" in raw:
        raise TrustedNetworkResolutionError(
            "Pinned trusted network address is invalid."
        )
    address = None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        pass
    if address is None:
        raise TrustedNetworkResolutionError(
            "Pinned trusted network address is invalid."
        )

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
            "Pinned trusted network address is not global."
        )
    return address


class AsyncioTrustedNetworkResolver:
    """Resolve the approved hostname once before C4a public-IP filtering."""

    def __init__(
        self,
        *,
        lookup: DnsLookup | None = None,
        lookup_timeout_seconds: float = 10.0,
    ) -> None:
        if not 0 < float(lookup_timeout_seconds) <= 30:
            raise ValueError(
                "lookup_timeout_seconds must be in (0, 30]."
            )
        self._lookup = lookup
        self._lookup_timeout_seconds = float(lookup_timeout_seconds)

    async def resolve(
        self,
        *,
        hostname: str,
        port: int,
    ) -> tuple[str, ...]:
        if (
            not isinstance(hostname, str)
            or not hostname
            or hostname != hostname.strip()
            or port != 443
        ):
            raise TrustedNetworkResolutionError(
                "Trusted DNS request is invalid."
            )

        try:
            async with asyncio.timeout(self._lookup_timeout_seconds):
                if self._lookup is None:
                    loop = asyncio.get_running_loop()
                    raw_results = await loop.getaddrinfo(
                        hostname,
                        port,
                        family=socket.AF_UNSPEC,
                        type=socket.SOCK_STREAM,
                        proto=socket.IPPROTO_TCP,
                    )
                else:
                    raw_results = await self._lookup(hostname, port)
        except asyncio.CancelledError:
            raise
        except Exception:
            raw_results = None

        if raw_results is None:
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution failed closed."
            )
        if not isinstance(raw_results, Sequence) or not raw_results:
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution returned no usable addresses."
            )

        addresses: list[str] = []
        for item in raw_results:
            if not isinstance(item, tuple) or len(item) < 5:
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned malformed data."
                )

            family, socktype, proto, _, sockaddr = item[:5]
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            if socktype != socket.SOCK_STREAM:
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned an invalid socket type."
                )
            if proto not in {0, socket.IPPROTO_TCP}:
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned an invalid protocol."
                )
            if (
                not isinstance(sockaddr, tuple)
                or not sockaddr
                or not isinstance(sockaddr[0], str)
            ):
                raise TrustedNetworkResolutionError(
                    "Trusted DNS resolution returned a malformed address."
                )

            value = sockaddr[0]
            if value not in addresses:
                addresses.append(value)

        if not addresses:
            raise TrustedNetworkResolutionError(
                "Trusted DNS resolution returned no usable addresses."
            )

        return tuple(addresses)


class _PinnedAiohttpResolver(AbstractResolver):
    """aiohttp resolver that can return only C4a-approved IP addresses."""

    def __init__(
        self,
        *,
        hostname: str,
        addresses: tuple[str, ...],
    ) -> None:
        self._hostname = hostname
        self._addresses = tuple(
            _public_ip(item).compressed
            for item in addresses
        )

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        if host != self._hostname or port != 443:
            raise OSError(
                "Pinned resolver scope mismatch."
            )

        results: list[ResolveResult] = []
        for raw in self._addresses:
            address = ipaddress.ip_address(raw)
            address_family = (
                socket.AF_INET
                if address.version == 4
                else socket.AF_INET6
            )
            if family not in {
                socket.AF_UNSPEC,
                address_family,
            }:
                continue

            results.append(
                {
                    "hostname": self._hostname,
                    "host": address.compressed,
                    "port": 443,
                    "family": address_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": 0,
                }
            )

        if not results:
            raise OSError(
                "Pinned resolver has no address for requested family."
            )
        return results

    async def close(self) -> None:
        return None


class AiohttpTrustedHttpsRequester:
    """Concrete HTTPS requester for one already-approved C4a connect plan."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 30.0,
        total_timeout_seconds: float = 60.0,
    ) -> None:
        if not 0 < float(connect_timeout_seconds) <= 120:
            raise ValueError(
                "connect_timeout_seconds must be in (0, 120]."
            )
        if not 0 < float(read_timeout_seconds) <= 300:
            raise ValueError(
                "read_timeout_seconds must be in (0, 300]."
            )
        self._connect_timeout_seconds = float(
            connect_timeout_seconds
        )
        self._read_timeout_seconds = float(
            read_timeout_seconds
        )
        if not 0 < float(total_timeout_seconds) <= 300:
            raise ValueError(
                "total_timeout_seconds must be in (0, 300]."
            )
        self._total_timeout_seconds = float(total_timeout_seconds)

    async def get(
        self,
        plan: TrustedNetworkConnectPlan,
    ) -> TrustedNetworkWireResponse:
        request_url, headers, addresses = self._validate_plan(plan)
        try:
            resolver = _PinnedAiohttpResolver(
                hostname=plan.hostname,
                addresses=addresses,
            )

            ssl_context = ssl.create_default_context()
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

            timeout = aiohttp.ClientTimeout(
                total=self._total_timeout_seconds,
                connect=self._connect_timeout_seconds,
                sock_connect=self._connect_timeout_seconds,
                sock_read=self._read_timeout_seconds,
            )
            connector = aiohttp.TCPConnector(
                resolver=resolver,
                use_dns_cache=False,
                ttl_dns_cache=0,
                family=socket.AF_UNSPEC,
                ssl=ssl_context,
                limit=1,
                limit_per_host=1,
                force_close=True,
            )

            try:
                session = aiohttp.ClientSession(
                    connector=connector,
                    connector_owner=True,
                    trust_env=False,
                    auto_decompress=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    timeout=timeout,
                    max_line_size=8_190,
                    max_field_size=8_190,
                    max_headers=_MAX_HEADER_COUNT,
                )
            except BaseException:
                await connector.close()
                raise

            async with session:
                async with session.get(
                    request_url,
                    headers=headers,
                    allow_redirects=False,
                    proxy=None,
                    auth=None,
                ) as response:
                    response_headers = (
                        self._bounded_security_headers(
                            response.raw_headers
                        )
                    )
                    body = await self._read_bounded_body(
                        response.content,
                        plan.max_response_bytes,
                    )
                    return TrustedNetworkWireResponse(
                        status_code=int(response.status),
                        headers=response_headers,
                        body=body,
                    )
        except asyncio.CancelledError:
            raise
        except TrustedNetworkTransportError:
            raise
        except Exception:
            pass

        raise TrustedNetworkTransportError(
            "Trusted HTTPS request failed closed."
        )

    @staticmethod
    def _validate_plan(
        plan: TrustedNetworkConnectPlan,
    ) -> tuple[str, dict[str, str], tuple[str, ...]]:
        if not isinstance(plan, TrustedNetworkConnectPlan):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS connect plan is required."
            )
        if plan.method != "GET" or plan.port != 443:
            raise TrustedNetworkTransportError(
                "Trusted HTTPS plan accepts only GET on port 443."
            )
        if (
            not isinstance(plan.hostname, str)
            or not plan.hostname
            or plan.hostname != plan.hostname.strip().lower()
            or _DNS_HOSTNAME.fullmatch(plan.hostname) is None
            or aiohttp.helpers.is_ip_address(plan.hostname)
        ):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS requires a canonical DNS hostname."
            )

        if (
            not isinstance(plan.url, str)
            or not plan.url
            or len(plan.url) > 4096
            or plan.url != plan.url.strip()
            or any(ord(character) < 32 for character in plan.url)
            or "\\" in plan.url
        ):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS URL is invalid."
            )

        parsed = None
        parsed_port = None
        parsed_hostname = None
        try:
            parsed = urlsplit(plan.url)
            parsed_port = parsed.port
            if parsed.hostname is not None:
                parsed_hostname = parsed.hostname.lower()
                if parsed_hostname.endswith("."):
                    parsed_hostname = parsed_hostname[:-1]
                parsed_hostname = (
                    parsed_hostname.encode("idna").decode("ascii").lower()
                )
        except (TypeError, ValueError, UnicodeError):
            parsed = None

        if parsed is None or parsed_hostname is None:
            raise TrustedNetworkTransportError(
                "Trusted HTTPS URL is invalid."
            )

        if (
            parsed.scheme.lower() != "https"
            or parsed_hostname != plan.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed_port not in {None, 443}
        ):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS URL no longer matches the connect plan."
            )

        canonical_netloc = plan.hostname
        if parsed_port == 443:
            canonical_netloc = f"{canonical_netloc}:443"
        request_url = urlunsplit(
            (
                "https",
                canonical_netloc,
                parsed.path,
                parsed.query,
                "",
            )
        )

        if (
            not isinstance(plan.addresses, tuple)
            or not plan.addresses
        ):
            raise TrustedNetworkResolutionError(
                "Trusted HTTPS plan has no pinned addresses."
            )
        addresses: list[str] = []
        for raw in plan.addresses:
            address = _public_ip(raw)
            canonical = address.compressed
            if canonical not in addresses:
                addresses.append(canonical)

        if (
            not isinstance(plan.headers, tuple)
            or not plan.headers
        ):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS request headers are invalid."
            )

        headers: dict[str, str] = {}
        for item in plan.headers:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not all(
                    isinstance(value, str)
                    for value in item
                )
            ):
                raise TrustedNetworkTransportError(
                    "Trusted HTTPS request header is invalid."
                )
            name, value = item
            if (
                name != name.strip().lower()
                or name not in _ALLOWED_REQUEST_HEADERS
                or name in headers
            ):
                raise TrustedNetworkTransportError(
                    "Trusted HTTPS request header boundary changed."
                )
            if (
                value != value.strip()
                or any(
                    ord(character) < 32
                    or ord(character) == 127
                    for character in value
                )
            ):
                raise TrustedNetworkTransportError(
                    "Trusted HTTPS request header value is invalid."
                )
            headers[name] = value

        if set(headers) != _ALLOWED_REQUEST_HEADERS:
            raise TrustedNetworkTransportError(
                "Trusted HTTPS fixed request headers are incomplete."
            )
        if headers != dict(_FIXED_REQUEST_HEADERS):
            raise TrustedNetworkTransportError(
                "Trusted HTTPS fixed request header values changed."
            )

        if (
            not isinstance(plan.max_response_bytes, int)
            or isinstance(plan.max_response_bytes, bool)
            or not 1 <= plan.max_response_bytes <= _MAX_BODY_BYTES
        ):
            raise TrustedNetworkResponseLimitError(
                "Trusted HTTPS response limit is invalid."
            )

        return request_url, headers, tuple(addresses)

    @staticmethod
    def _bounded_security_headers(
        raw_headers: Sequence[tuple[bytes, bytes]],
    ) -> dict[str, str]:
        if not isinstance(raw_headers, Sequence):
            raise TrustedNetworkResponseError(
                "Trusted HTTPS raw headers are invalid."
            )
        if len(raw_headers) > _MAX_HEADER_COUNT:
            raise TrustedNetworkResponseError(
                "Trusted HTTPS response has too many headers."
            )

        total_bytes = 0
        selected: dict[str, str] = {}

        for item in raw_headers:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], bytes)
                or not isinstance(item[1], bytes)
            ):
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS raw header is invalid."
                )

            raw_name, raw_value = item
            total_bytes += len(raw_name) + len(raw_value)
            if total_bytes > _MAX_HEADER_BYTES:
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response headers exceed the limit."
                )
            if not 1 <= len(raw_name) <= _MAX_HEADER_NAME_BYTES:
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS header name exceeds the limit."
                )
            if len(raw_value) > _MAX_HEADER_VALUE_BYTES:
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS header value exceeds the limit."
                )

            name = None
            try:
                name = raw_name.decode("ascii").lower()
                value = raw_value.decode("latin-1")
            except UnicodeDecodeError:
                pass

            if name is None:
                raise TrustedNetworkResponseError(
                    "Trusted HTTPS response header encoding is invalid."
                )

            if name in _SECURITY_RESPONSE_HEADERS:
                if name in selected:
                    raise TrustedNetworkResponseError(
                        "Trusted HTTPS response contains an ambiguous "
                        "security header."
                    )
                selected[name] = value

        return selected

    @staticmethod
    async def _read_bounded_body(
        content: Any,
        max_response_bytes: int,
    ) -> bytes:
        body = bytearray()

        try:
            iterator = content.iter_chunked(_STREAM_CHUNK_BYTES)
            async for chunk in iterator:
                if not isinstance(
                    chunk,
                    (bytes, bytearray, memoryview),
                ):
                    raise TrustedNetworkResponseError(
                        "Trusted HTTPS body chunk is invalid."
                    )
                if len(body) + len(chunk) > max_response_bytes:
                    raise TrustedNetworkResponseLimitError(
                        "Trusted HTTPS response exceeded the "
                        "streaming body limit."
                    )
                body.extend(chunk)
        except asyncio.CancelledError:
            raise
        except TrustedNetworkTransportError:
            raise
        except Exception:
            body = None

        if body is None:
            raise TrustedNetworkTransportError(
                "Trusted HTTPS body read failed closed."
            )
        return bytes(body)


__all__ = [
    "TRUSTED_NETWORK_AIOHTTP_SCHEMA_VERSION",
    "AiohttpTrustedHttpsRequester",
    "AsyncioTrustedNetworkResolver",
]
