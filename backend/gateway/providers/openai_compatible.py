from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Mapping

from openai import OpenAI

from backend.gateway.errors import normalize_provider_error
from backend.gateway.providers.base import ProviderAdapter, ProviderStreamCancelled
from backend.gateway.schemas import GatewayRequest, GatewayResponse, GatewayUsage


class OpenAICompatibleAdapter(ProviderAdapter):
    """Generic adapter for OpenAI Chat Completions compatible providers.

    The adapter intentionally keeps provider-specific behaviour in configuration:
    provider name, base URL and optional static headers. Credentials are injected
    by the existing Secret Manager / factory layer and are never persisted here.
    """

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name.strip()
        self.base_url = base_url.rstrip("/")
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            default_headers=dict(default_headers or {}),
        )

    @staticmethod
    def _usage(response_usage) -> tuple[GatewayUsage, float | None]:
        if response_usage is None:
            return GatewayUsage(), None
        reported_cost = getattr(response_usage, "cost", None)
        return (
            GatewayUsage(
                input_tokens=getattr(response_usage, "prompt_tokens", None),
                output_tokens=getattr(response_usage, "completion_tokens", None),
                total_tokens=getattr(response_usage, "total_tokens", None),
            ),
            float(reported_cost) if reported_cost is not None else None,
        )

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        started = perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": item.role, "content": item.content}
                    for item in request.messages
                ],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout_seconds,
            )
            if not response.choices:
                raise RuntimeError("Provider returned no choices.")
            content = response.choices[0].message.content or ""
            usage, reported_cost = self._usage(getattr(response, "usage", None))
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content=content,
                status="success",
                usage=usage,
                cost=reported_cost,
                latency_ms=round((perf_counter() - started) * 1000, 2),
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "adapter": "openai_compatible",
                },
            )
        except Exception as exc:
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content="",
                status="error",
                latency_ms=round((perf_counter() - started) * 1000, 2),
                error=normalize_provider_error(exc, provider=self.name),
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "adapter": "openai_compatible",
                },
            )

    def complete_stream(
        self,
        request: GatewayRequest,
        on_delta: Callable[[str], None],
        is_cancelled: Callable[[], bool] | None = None,
    ) -> GatewayResponse:
        started = perf_counter()
        stream = None
        content_parts: list[str] = []
        usage = GatewayUsage()
        reported_cost = None
        try:
            stream = self._client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": item.role, "content": item.content}
                    for item in request.messages
                ],
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout=request.timeout_seconds,
                stream=True,
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if is_cancelled is not None and is_cancelled():
                    raise ProviderStreamCancelled("Provider stream was cancelled.")
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage, chunk_cost = self._usage(chunk_usage)
                    if chunk_cost is not None:
                        reported_cost = chunk_cost
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0].delta, "content", None)
                if not delta:
                    continue
                content_parts.append(delta)
                on_delta(delta)
            content = "".join(content_parts)
            if not content:
                raise RuntimeError("Provider returned no streamed content.")
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content=content,
                status="success",
                usage=usage,
                cost=reported_cost,
                latency_ms=round((perf_counter() - started) * 1000, 2),
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "streamed": True,
                    "adapter": "openai_compatible",
                },
            )
        except ProviderStreamCancelled:
            raise
        except Exception as exc:
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content="",
                status="error",
                latency_ms=round((perf_counter() - started) * 1000, 2),
                error=normalize_provider_error(exc, provider=self.name),
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "streamed": True,
                    "adapter": "openai_compatible",
                },
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
