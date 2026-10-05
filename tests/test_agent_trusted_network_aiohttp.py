from __future__ import annotations

import asyncio
from dataclasses import replace
import socket
import ssl

import pytest

import backend.orchestration.trusted_network_aiohttp as network_io
from backend.orchestration.trusted_network import (
    TrustedNetworkConnectPlan,
    TrustedNetworkResolutionError,
    TrustedNetworkResponseError,
    TrustedNetworkResponseLimitError,
    TrustedNetworkTransportError,
)


PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2001:4860:4860::8888"


def plan(
    *,
    url: str = "https://github.com/path?private=1",
    hostname: str = "github.com",
    addresses: tuple[str, ...] = (PUBLIC_V4,),
    max_response_bytes: int = 1_024,
    headers: tuple[tuple[str, str], ...] | None = None,
) -> TrustedNetworkConnectPlan:
    return TrustedNetworkConnectPlan(
        url=url,
        hostname=hostname,
        addresses=addresses,
        port=443,
        method="GET",
        headers=(
            (
                (
                    "accept",
                    (
                        "text/plain, text/html, "
                        "application/json;q=0.9, "
                        "application/xml;q=0.8"
                    ),
                ),
                ("accept-encoding", "identity"),
                (
                    "user-agent",
                    "ai-studio-enterprise-trusted-network/1",
                ),
            )
            if headers is None
            else headers
        ),
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.asyncio
async def test_asyncio_resolver_resolves_exact_hostname_once() -> None:
    calls: list[tuple[str, int]] = []

    async def lookup(hostname: str, port: int):
        calls.append((hostname, port))
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (PUBLIC_V4, port),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (PUBLIC_V6, port, 0, 0),
            ),
        ]

    resolver = network_io.AsyncioTrustedNetworkResolver(
        lookup=lookup
    )

    result = await resolver.resolve(
        hostname="github.com",
        port=443,
    )

    assert result == (PUBLIC_V4, PUBLIC_V6)
    assert calls == [("github.com", 443)]


@pytest.mark.asyncio
async def test_asyncio_resolver_error_is_sanitized() -> None:
    async def lookup(hostname: str, port: int):
        raise RuntimeError(
            "private resolver detail for "
            f"{hostname}:{port}"
        )

    resolver = network_io.AsyncioTrustedNetworkResolver(
        lookup=lookup
    )

    with pytest.raises(
        TrustedNetworkResolutionError
    ) as caught:
        await resolver.resolve(
            hostname="github.com",
            port=443,
        )

    assert "private resolver detail" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_asyncio_resolver_deadline_fails_closed() -> None:
    async def lookup(hostname: str, port: int):
        await asyncio.sleep(0.2)
        raise AssertionError("lookup continued after timeout")

    resolver = network_io.AsyncioTrustedNetworkResolver(
        lookup=lookup,
        lookup_timeout_seconds=0.01,
    )
    with pytest.raises(TrustedNetworkResolutionError) as caught:
        await resolver.resolve(hostname="github.com", port=443)
    assert "github.com" not in str(caught.value)


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def iter_chunked(self, size: int):
        assert size > 0
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        raw_headers: tuple[tuple[bytes, bytes], ...] = (),
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.raw_headers = raw_headers
        self.content = FakeContent(
            [b"ok"] if chunks is None else chunks
        )


class FakeRequestContext:
    def __init__(
        self,
        response: FakeResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self._response = response
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> bool:
        return False


class FakeSession:
    def __init__(
        self,
        *,
        kwargs,
        response: FakeResponse | None,
        request_error: Exception | None,
    ) -> None:
        self.kwargs = kwargs
        self.response = response
        self.request_error = request_error
        self.get_calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> bool:
        return False

    def get(self, url: str, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeRequestContext(
            response=self.response,
            error=self.request_error,
        )


class FakeConnector:
    def __init__(self, kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def install_fake_aiohttp(
    monkeypatch,
    *,
    response: FakeResponse | None = None,
    request_error: Exception | None = None,
):
    captured: dict[str, object] = {}

    def connector_factory(**kwargs):
        connector = FakeConnector(kwargs)
        captured["connector"] = connector
        return connector

    def session_factory(**kwargs):
        session = FakeSession(
            kwargs=kwargs,
            response=response,
            request_error=request_error,
        )
        captured["session"] = session
        return session

    monkeypatch.setattr(
        network_io.aiohttp,
        "TCPConnector",
        connector_factory,
    )
    monkeypatch.setattr(
        network_io.aiohttp,
        "ClientSession",
        session_factory,
    )
    monkeypatch.setattr(
        network_io.aiohttp,
        "DummyCookieJar",
        lambda: object(),
    )

    return captured


@pytest.mark.asyncio
async def test_requester_pins_addresses_and_disables_ambient_network_features(
    monkeypatch,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            raw_headers=(
                (b"Content-Type", b"text/plain"),
                (b"Set-Cookie", b"session=must-not-surface"),
            ),
            chunks=[b"hello", b" world"],
        ),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()
    result = await requester.get(
        plan(addresses=(PUBLIC_V4, PUBLIC_V6))
    )

    assert result.status_code == 200
    assert result.body == b"hello world"
    assert result.headers == {
        "content-type": "text/plain"
    }

    connector = captured["connector"]
    session = captured["session"]

    assert isinstance(connector, FakeConnector)
    assert isinstance(session, FakeSession)

    connector_kwargs = connector.kwargs
    assert connector_kwargs["use_dns_cache"] is False
    assert connector_kwargs["ttl_dns_cache"] == 0
    assert connector_kwargs["family"] == socket.AF_UNSPEC
    assert connector_kwargs["limit"] == 1
    assert connector_kwargs["limit_per_host"] == 1
    assert connector_kwargs["force_close"] is True

    tls = connector_kwargs["ssl"]
    assert isinstance(tls, ssl.SSLContext)
    assert tls.check_hostname is True
    assert tls.verify_mode == ssl.CERT_REQUIRED
    assert tls.minimum_version >= ssl.TLSVersion.TLSv1_2

    pinned = connector_kwargs["resolver"]
    resolved = await pinned.resolve(
        "github.com",
        443,
        family=socket.AF_UNSPEC,
    )
    assert [item["host"] for item in resolved] == [
        PUBLIC_V4,
        PUBLIC_V6,
    ]

    with pytest.raises(OSError):
        await pinned.resolve(
            "docs.python.org",
            443,
            family=socket.AF_UNSPEC,
        )

    assert session.kwargs["trust_env"] is False
    assert session.kwargs["auto_decompress"] is False
    assert session.kwargs["timeout"].total == 60.0
    assert session.kwargs["max_line_size"] == 8_190
    assert session.kwargs["max_field_size"] == 8_190
    assert session.kwargs["max_headers"] == 64

    [(url, request_kwargs)] = session.get_calls
    assert url == "https://github.com/path?private=1"
    assert request_kwargs["allow_redirects"] is False
    assert request_kwargs["proxy"] is None
    assert request_kwargs["auth"] is None
    assert request_kwargs["headers"]["accept-encoding"] == (
        "identity"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname,url,expected_url",
    [
        (
            "github.com",
            "https://github.com./path?private=1",
            "https://github.com/path?private=1",
        ),
        (
            "xn--bcher-kva.de",
            "https://bücher.de/path?private=1",
            "https://xn--bcher-kva.de/path?private=1",
        ),
        (
            "example.123",
            "https://example.123:443/path?private=1",
            "https://example.123:443/path?private=1",
        ),
    ],
)
async def test_requester_preserves_c4a_host_contract_via_canonical_wire_url(
    monkeypatch,
    hostname: str,
    url: str,
    expected_url: str,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            raw_headers=((b"Content-Type", b"text/plain"),),
        ),
    )

    result = await network_io.AiohttpTrustedHttpsRequester().get(
        plan(hostname=hostname, url=url)
    )

    assert result.status_code == 200
    session = captured["session"]
    connector = captured["connector"]
    assert isinstance(session, FakeSession)
    assert isinstance(connector, FakeConnector)

    [(request_url, _)] = session.get_calls
    assert request_url == expected_url

    pinned = connector.kwargs["resolver"]
    resolved = await pinned.resolve(
        hostname,
        443,
        family=socket.AF_UNSPEC,
    )
    assert [item["host"] for item in resolved] == [PUBLIC_V4]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        (
            ("accept", "*/*"),
            ("accept-encoding", "identity"),
            (
                "user-agent",
                "ai-studio-enterprise-trusted-network/1",
            ),
        ),
        (
            (
                "accept",
                (
                    "text/plain, text/html, "
                    "application/json;q=0.9, "
                    "application/xml;q=0.8"
                ),
            ),
            ("accept-encoding", "identity"),
            ("user-agent", "changed-agent"),
        ),
    ],
)
async def test_requester_rejects_changed_fixed_request_header_values(
    monkeypatch,
    headers: tuple[tuple[str, str], ...],
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(),
    )

    with pytest.raises(TrustedNetworkTransportError):
        await network_io.AiohttpTrustedHttpsRequester().get(
            plan(headers=headers)
        )

    assert captured == {}


@pytest.mark.asyncio
async def test_session_creation_failure_closes_constructed_connector(
    monkeypatch,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(),
    )

    def fail_session(**kwargs):
        raise RuntimeError("session-construction-failure")

    monkeypatch.setattr(
        network_io.aiohttp,
        "ClientSession",
        fail_session,
    )

    with pytest.raises(TrustedNetworkTransportError):
        await network_io.AiohttpTrustedHttpsRequester().get(plan())

    connector = captured["connector"]
    assert isinstance(connector, FakeConnector)
    assert connector.closed is True


@pytest.mark.asyncio
async def test_requester_rejects_private_pinned_address_before_client(
    monkeypatch,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(TrustedNetworkResolutionError):
        await requester.get(
            plan(addresses=("127.0.0.1",))
        )

    assert captured == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname,url",
    [
        ("127.0.0.1", "https://127.0.0.1/"),
        ("8.8.8.8", "https://8.8.8.8/"),
        ("::1", "https://[::1]/"),
        ("2001:4860:4860::8888", "https://[2001:4860:4860::8888]/"),
        ("127.1", "https://127.1/"),
        ("2130706433", "https://2130706433/"),
        ("0x7f000001", "https://0x7f000001/"),
        ("0177.0.0.1", "https://0177.0.0.1/"),
    ],
)
async def test_ip_literal_or_numeric_url_host_cannot_bypass_pinned_resolver(
    monkeypatch, hostname: str, url: str,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch, response=FakeResponse(),
    )
    candidate = replace(plan(), hostname=hostname, url=url)
    with pytest.raises(TrustedNetworkTransportError):
        await network_io.AiohttpTrustedHttpsRequester().get(candidate)
    assert captured == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostname,url",
    [
        ("0x7f.0.0.1", "https://0x7f.0.0.1/"),
        ("0x7f.1", "https://0x7f.1/"),
        ("0x7f.example", "https://0x7f.example/"),
    ],
)
async def test_hex_like_dns_hostname_remains_bound_to_pinned_resolver(
    monkeypatch, hostname: str, url: str,
) -> None:
    # aiohttp 3.14.3 treats these as hostnames, not IP literals, so
    # the configured resolver remains authoritative.  C4b must not
    # narrow C3/C4a grammar merely because the OS getaddrinfo() API
    # would interpret some of these spellings as legacy IPv4 aliases.
    assert network_io.aiohttp.helpers.is_ip_address(hostname) is False

    captured = install_fake_aiohttp(
        monkeypatch, response=FakeResponse(),
    )

    result = await network_io.AiohttpTrustedHttpsRequester().get(
        plan(hostname=hostname, url=url)
    )

    assert result.status_code == 200
    connector = captured["connector"]
    assert isinstance(connector, FakeConnector)
    pinned = connector.kwargs["resolver"]

    resolved = await pinned.resolve(
        hostname, 443, family=socket.AF_UNSPEC,
    )
    assert {item["host"] for item in resolved} == {PUBLIC_V4}

    with pytest.raises(OSError):
        await pinned.resolve(
            "127.0.0.1", 443, family=socket.AF_UNSPEC,
        )


@pytest.mark.asyncio
async def test_malformed_pinned_ip_does_not_expose_inner_exception(
    monkeypatch,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch, response=FakeResponse(),
    )
    candidate = plan(
        addresses=("https://github.com/private?token=must-not-leak",),
    )
    with pytest.raises(TrustedNetworkResolutionError) as caught:
        await network_io.AiohttpTrustedHttpsRequester().get(candidate)
    assert str(caught.value) == "Pinned trusted network address is invalid."
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert captured == {}


@pytest.mark.asyncio
async def test_requester_rejects_ambient_or_sensitive_request_headers(
    monkeypatch,
) -> None:
    captured = install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(TrustedNetworkTransportError):
        await requester.get(
            plan(
                headers=(
                    (
                        "accept",
                        (
                            "text/plain, text/html, "
                            "application/json;q=0.9, "
                            "application/xml;q=0.8"
                        ),
                    ),
                    ("accept-encoding", "identity"),
                    (
                        "user-agent",
                        "ai-studio-enterprise-trusted-network/1",
                    ),
                    ("authorization", "Bearer secret"),
                )
            )
        )

    assert captured == {}


@pytest.mark.asyncio
async def test_duplicate_security_response_header_fails_closed(
    monkeypatch,
) -> None:
    install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            raw_headers=(
                (b"Content-Type", b"text/plain"),
                (b"content-type", b"application/json"),
            ),
        ),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(TrustedNetworkResponseError):
        await requester.get(plan())


@pytest.mark.asyncio
async def test_response_header_count_is_bounded(
    monkeypatch,
) -> None:
    raw_headers = tuple(
        (f"X-Test-{index}".encode(), b"x")
        for index in range(65)
    )
    install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            raw_headers=raw_headers,
        ),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(TrustedNetworkResponseError):
        await requester.get(plan())


@pytest.mark.asyncio
async def test_response_body_limit_is_enforced_while_streaming(
    monkeypatch,
) -> None:
    install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            chunks=[b"123", b"45"],
        ),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(TrustedNetworkResponseLimitError):
        await requester.get(
            plan(max_response_bytes=4)
        )


@pytest.mark.asyncio
async def test_request_failure_is_sanitized_without_exception_cause(
    monkeypatch,
) -> None:
    install_fake_aiohttp(
        monkeypatch,
        request_error=RuntimeError(
            "https://github.com/private?token=must-not-leak"
        ),
    )

    requester = network_io.AiohttpTrustedHttpsRequester()

    with pytest.raises(
        TrustedNetworkTransportError
    ) as caught:
        await requester.get(plan())

    text = str(caught.value)
    assert "private" not in text
    assert "must-not-leak" not in text
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_header_decoding_failure_has_no_inner_exception_context(
    monkeypatch,
) -> None:
    install_fake_aiohttp(
        monkeypatch,
        response=FakeResponse(
            raw_headers=((b"\xffContent-Type", b"untrusted"),),
        ),
    )
    with pytest.raises(TrustedNetworkResponseError) as caught:
        await network_io.AiohttpTrustedHttpsRequester().get(plan())
    assert str(caught.value) == (
        "Trusted HTTPS response header encoding is invalid."
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ("ssl", "connector", "timeout", "session"))
async def test_https_initialization_failure_is_sanitized_without_inner_context(
    monkeypatch, stage: str,
) -> None:
    install_fake_aiohttp(monkeypatch, response=FakeResponse())
    def fail(*args, **kwargs):
        raise RuntimeError(
            "https://github.com/private?token=must-not-leak"
        )

    if stage == "ssl":
        monkeypatch.setattr(network_io.ssl, "create_default_context", fail)
    elif stage == "connector":
        monkeypatch.setattr(network_io.aiohttp, "TCPConnector", fail)
    elif stage == "timeout":
        monkeypatch.setattr(network_io.aiohttp, "ClientTimeout", fail)
    else:
        monkeypatch.setattr(network_io.aiohttp, "ClientSession", fail)

    with pytest.raises(TrustedNetworkTransportError) as caught:
        await network_io.AiohttpTrustedHttpsRequester().get(plan())
    assert str(caught.value) == "Trusted HTTPS request failed closed."
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_requester_timeout_bounds_are_fail_closed() -> None:
    with pytest.raises(ValueError):
        network_io.AiohttpTrustedHttpsRequester(
            connect_timeout_seconds=0,
        )

    with pytest.raises(ValueError):
        network_io.AiohttpTrustedHttpsRequester(
            read_timeout_seconds=301,
        )

    with pytest.raises(ValueError):
        network_io.AiohttpTrustedHttpsRequester(
            total_timeout_seconds=301,
        )

    with pytest.raises(ValueError):
        network_io.AsyncioTrustedNetworkResolver(
            lookup_timeout_seconds=0,
        )
