from __future__ import annotations

from collections.abc import Mapping

import pytest

from backend.agent_governance.core import AgentCapability
from backend.agent_governance.enforcement_adapters import (
    TrustedAgentToolBinding,
)
from backend.agent_governance.invocation import (
    TrustedAgentInvocationResolver,
)
from backend.orchestration.trusted_network import (
    TRUSTED_NETWORK_TRANSPORT_SCHEMA_VERSION,
    TrustedNetworkBindingError,
    TrustedNetworkConnectPlan,
    TrustedNetworkFetchTransport,
    TrustedNetworkRedirectError,
    TrustedNetworkResolutionError,
    TrustedNetworkResponseError,
    TrustedNetworkResponseLimitError,
    TrustedNetworkTransportError,
    TrustedNetworkWireResponse,
)
from backend.runtime_policy import PolicyOperation


PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2001:4860:4860::8888"


def network_binding(
    *,
    action_id: str = "network.fetch",
) -> TrustedAgentToolBinding:
    return TrustedAgentToolBinding(
        registry_tool_id="tool_network_fetch",
        registry_tool_key="trusted.network.fetch",
        governed_tool_id="network.fetch",
        action_id=action_id,
        runtime_operation=PolicyOperation.METADATA_VALIDATION,
        capabilities=(AgentCapability.EXTERNAL_NETWORK,),
        workspace_id="workspace1",
        kind="http",
        risk_level="low",
        isolation_mode="restricted",
        allow_network=True,
        allow_filesystem_read=False,
        allow_filesystem_write=False,
    )


def invocation(
    *,
    url: str = "https://github.com/path?private=1",
    binding: TrustedAgentToolBinding | None = None,
):
    trusted_binding = binding or network_binding()
    input_data = {"url": url}
    facts = TrustedAgentInvocationResolver().derive(
        binding=trusted_binding,
        validated_input=input_data,
    )
    return trusted_binding, input_data, facts


class FakeResolver:
    def __init__(
        self,
        answers: Mapping[str, tuple[str, ...] | Exception],
    ) -> None:
        self.answers = dict(answers)
        self.calls: list[tuple[str, int]] = []

    async def resolve(
        self,
        *,
        hostname: str,
        port: int,
    ) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        value = self.answers.get(hostname)
        if isinstance(value, Exception):
            raise value
        if value is None:
            return ()
        return value


class FakeRequester:
    def __init__(
        self,
        responses: list[TrustedNetworkWireResponse | Exception],
    ) -> None:
        self.responses = list(responses)
        self.plans: list[TrustedNetworkConnectPlan] = []

    async def get(
        self,
        plan: TrustedNetworkConnectPlan,
    ) -> TrustedNetworkWireResponse:
        self.plans.append(plan)
        if not self.responses:
            raise AssertionError("Unexpected trusted HTTPS request.")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(
    status: int = 200,
    *,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"ok",
) -> TrustedNetworkWireResponse:
    return TrustedNetworkWireResponse(
        status_code=status,
        headers={} if headers is None else dict(headers),
        body=body,
    )


@pytest.mark.asyncio
async def test_exact_trusted_invocation_builds_pinned_connect_plan() -> None:
    binding, input_data, facts = invocation()
    resolver = FakeResolver(
        {"github.com": (PUBLIC_V4, PUBLIC_V6)}
    )
    requester = FakeRequester(
        [
            response(
                headers={
                    "Content-Type": "text/plain",
                    "Set-Cookie": "session=must-not-surface",
                }
            )
        ]
    )

    result = await TrustedNetworkFetchTransport(
        resolver=resolver,
        requester=requester,
    ).fetch(
        binding=binding,
        invocation_facts=facts,
        validated_input=input_data,
    )

    assert result.status_code == 200
    assert result.body == b"ok"
    assert result.external_domain == "github.com"
    assert result.redirect_count == 0
    assert dict(result.headers) == {
        "content-type": "text/plain"
    }
    assert "session=must-not-surface" not in str(result.headers)
    assert resolver.calls == [("github.com", 443)]

    [plan] = requester.plans
    assert plan.hostname == "github.com"
    assert plan.addresses == (PUBLIC_V4, PUBLIC_V6)
    assert plan.port == 443
    assert plan.method == "GET"
    assert plan.max_response_bytes == 1_048_576
    assert ("accept-encoding", "identity") in plan.headers

    evidence = result.evidence()
    assert evidence["schema_version"] == (
        TRUSTED_NETWORK_TRANSPORT_SCHEMA_VERSION
    )
    assert evidence["external_domain"] == "github.com"
    assert evidence["response_bytes"] == 2
    assert evidence["trusted_invocation_fingerprint"] == (
        facts.fingerprint
    )
    assert len(evidence["fingerprint"]) == 64

    serialized = str(evidence)
    assert "/path" not in serialized
    assert "private=1" not in serialized
    assert "b'ok'" not in serialized


@pytest.mark.asyncio
async def test_binding_action_must_be_exact_network_fetch() -> None:
    binding, input_data, facts = invocation(
        binding=network_binding(action_id="network.fetch.other"),
    )
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester([response()]),
    )

    with pytest.raises(TrustedNetworkBindingError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
async def test_changed_input_invalidates_trusted_invocation() -> None:
    binding, _, facts = invocation(
        url="https://github.com/approved"
    )
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester([response()]),
    )

    with pytest.raises(TrustedNetworkBindingError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input={"url": "https://github.com/changed"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "224.0.0.1",
        "255.255.255.255",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ],
)
async def test_non_global_dns_answers_fail_closed(address: str) -> None:
    binding, input_data, facts = invocation()
    requester = FakeRequester([response()])
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (address,)}),
        requester=requester,
    )

    with pytest.raises(TrustedNetworkResolutionError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert requester.plans == []


@pytest.mark.asyncio
async def test_mixed_public_private_dns_fails_entire_resolution() -> None:
    binding, input_data, facts = invocation()
    requester = FakeRequester([response()])
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver(
            {"github.com": (PUBLIC_V4, "127.0.0.1")}
        ),
        requester=requester,
    )

    with pytest.raises(TrustedNetworkResolutionError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert requester.plans == []


@pytest.mark.asyncio
async def test_invalid_or_empty_dns_answer_fails_closed() -> None:
    binding, input_data, facts = invocation()

    for answers in [(), ("not-an-ip",)]:
        requester = FakeRequester([response()])
        transport = TrustedNetworkFetchTransport(
            resolver=FakeResolver({"github.com": answers}),
            requester=requester,
        )

        with pytest.raises(TrustedNetworkResolutionError):
            await transport.fetch(
                binding=binding,
                invocation_facts=facts,
                validated_input=input_data,
            )

        assert requester.plans == []


@pytest.mark.asyncio
async def test_dns_exception_is_sanitized() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver(
            {"github.com": RuntimeError("secret-dns-detail")}
        ),
        requester=FakeRequester([response()]),
    )

    with pytest.raises(TrustedNetworkResolutionError) as caught:
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert "secret-dns-detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_same_host_relative_redirect_is_rechecked() -> None:
    binding, input_data, facts = invocation(
        url="https://github.com/start"
    )
    resolver = FakeResolver({"github.com": (PUBLIC_V4,)})
    requester = FakeRequester(
        [
            response(
                302,
                headers={"Location": "/next?secret=2"},
            ),
            response(
                200,
                headers={"Content-Type": "text/plain"},
                body=b"done",
            ),
        ]
    )

    result = await TrustedNetworkFetchTransport(
        resolver=resolver,
        requester=requester,
    ).fetch(
        binding=binding,
        invocation_facts=facts,
        validated_input=input_data,
    )

    assert result.redirect_count == 1
    assert result.body == b"done"
    assert resolver.calls == [
        ("github.com", 443),
        ("github.com", 443),
    ]
    assert len(requester.plans) == 2
    assert requester.plans[1].url == (
        "https://github.com/next?secret=2"
    )
    assert "secret=2" not in str(result.evidence())


@pytest.mark.asyncio
async def test_cross_domain_redirect_denied_before_second_dns() -> None:
    binding, input_data, facts = invocation(
        url="https://github.com/start"
    )
    resolver = FakeResolver(
        {
            "github.com": (PUBLIC_V4,),
            "docs.python.org": (PUBLIC_V4,),
        }
    )
    requester = FakeRequester(
        [
            response(
                302,
                headers={
                    "Location": "https://docs.python.org/3/"
                },
            ),
        ]
    )

    with pytest.raises(TrustedNetworkRedirectError):
        await TrustedNetworkFetchTransport(
            resolver=resolver,
            requester=requester,
        ).fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert resolver.calls == [("github.com", 443)]
    assert len(requester.plans) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "https://127.0.0.1/private",
        "http://github.com/insecure",
        "https://user:secret@github.com/path",
        "https://github.com:8443/path",
        "https://github.com/path#fragment",
        r"https://github.com\@evil.example/path",
    ],
)
async def test_unsafe_redirect_targets_are_denied(
    location: str,
) -> None:
    binding, input_data, facts = invocation(
        url="https://github.com/start"
    )
    requester = FakeRequester(
        [response(302, headers={"Location": location})]
    )

    with pytest.raises(TrustedNetworkRedirectError):
        await TrustedNetworkFetchTransport(
            resolver=FakeResolver(
                {"github.com": (PUBLIC_V4,)}
            ),
            requester=requester,
        ).fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert len(requester.plans) == 1


@pytest.mark.asyncio
async def test_redirect_loop_is_denied() -> None:
    binding, input_data, facts = invocation(
        url="https://github.com/a"
    )
    requester = FakeRequester(
        [
            response(302, headers={"Location": "/b"}),
            response(302, headers={"Location": "/a"}),
        ]
    )

    with pytest.raises(TrustedNetworkRedirectError):
        await TrustedNetworkFetchTransport(
            resolver=FakeResolver(
                {"github.com": (PUBLIC_V4,)}
            ),
            requester=requester,
        ).fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert len(requester.plans) == 2


@pytest.mark.asyncio
async def test_redirect_limit_is_denied() -> None:
    binding, input_data, facts = invocation(
        url="https://github.com/a"
    )
    requester = FakeRequester(
        [response(302, headers={"Location": "/b"})]
    )

    with pytest.raises(TrustedNetworkRedirectError):
        await TrustedNetworkFetchTransport(
            resolver=FakeResolver(
                {"github.com": (PUBLIC_V4,)}
            ),
            requester=requester,
            max_redirects=0,
        ).fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encoding",
    ["gzip", "br", "deflate"],
)
async def test_non_identity_content_encoding_is_denied(
    encoding: str,
) -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester(
            [
                response(
                    headers={
                        "Content-Type": "text/plain",
                        "Content-Encoding": encoding,
                    }
                )
            ]
        ),
    )

    with pytest.raises(TrustedNetworkResponseError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
async def test_nonempty_response_requires_content_type() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester([response(body=b"text")]),
    )

    with pytest.raises(TrustedNetworkResponseError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
async def test_binary_media_type_is_denied() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester(
            [
                response(
                    headers={
                        "Content-Type": "application/octet-stream"
                    },
                    body=b"binary",
                )
            ]
        ),
    )

    with pytest.raises(TrustedNetworkResponseError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
async def test_structured_json_media_type_is_allowed() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester(
            [
                response(
                    headers={
                        "Content-Type": (
                            "application/problem+json; charset=utf-8"
                        )
                    },
                    body=b'{"ok":true}',
                )
            ]
        ),
    )

    result = await transport.fetch(
        binding=binding,
        invocation_facts=facts,
        validated_input=input_data,
    )

    assert result.body == b'{"ok":true}'
    assert dict(result.headers) == {
        "content-type": (
            "application/problem+json; charset=utf-8"
        )
    }


@pytest.mark.asyncio
async def test_oversized_response_body_is_denied() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester(
            [response(body=b"12345")]
        ),
        max_response_bytes=4,
    )

    with pytest.raises(TrustedNetworkResponseLimitError):
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )


@pytest.mark.asyncio
async def test_requester_exception_is_sanitized() -> None:
    binding, input_data, facts = invocation()
    transport = TrustedNetworkFetchTransport(
        resolver=FakeResolver({"github.com": (PUBLIC_V4,)}),
        requester=FakeRequester(
            [RuntimeError("secret-wire-detail")]
        ),
    )

    with pytest.raises(TrustedNetworkTransportError) as caught:
        await transport.fetch(
            binding=binding,
            invocation_facts=facts,
            validated_input=input_data,
        )

    assert "secret-wire-detail" not in str(caught.value)


def test_constructor_bounds_are_fail_closed() -> None:
    resolver = FakeResolver({"github.com": (PUBLIC_V4,)})
    requester = FakeRequester([response()])

    with pytest.raises(ValueError):
        TrustedNetworkFetchTransport(
            resolver=resolver,
            requester=requester,
            max_redirects=11,
        )

    with pytest.raises(ValueError):
        TrustedNetworkFetchTransport(
            resolver=resolver,
            requester=requester,
            max_response_bytes=0,
        )
