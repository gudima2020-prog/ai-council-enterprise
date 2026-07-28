from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from openai import OpenAI

from backend.gateway.errors import normalize_provider_error
from backend.gateway.providers.base import (
    ProviderAdapter,
    ProviderStreamCancelled,
)
from backend.gateway.schemas import (
    GatewayRequest,
    GatewayResponse,
    GatewayUsage,
)


class OpenRouterAdapter(ProviderAdapter):
    name = "openrouter"

    def __init__(self, *, api_key: str) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://github.com/gudima2020-prog/ai-council-enterprise",
                "X-Title": "AI Studio Enterprise",
            },
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
            usage = response.usage
            reported_cost = getattr(usage, "cost", None)

            latency_ms = round((perf_counter() - started) * 1000, 2)

            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content=content,
                status="success",
                usage=GatewayUsage(
                    input_tokens=getattr(usage, "prompt_tokens", None),
                    output_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                ),
                cost=(float(reported_cost) if reported_cost is not None else None),
                latency_ms=latency_ms,
                metadata={"mode": request.mode, "source": request.source},
            )
        except Exception as exc:
            latency_ms = round((perf_counter() - started) * 1000, 2)
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content="",
                status="error",
                latency_ms=latency_ms,
                error=normalize_provider_error(exc, provider=self.name),
                metadata={"mode": request.mode, "source": request.source},
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
        input_tokens = output_tokens = total_tokens = None
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
                    raise ProviderStreamCancelled(
                        "Provider stream was cancelled."
                    )

                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_tokens", None)
                    output_tokens = getattr(
                        usage,
                        "completion_tokens",
                        None,
                    )
                    total_tokens = getattr(usage, "total_tokens", None)
                    usage_cost = getattr(usage, "cost", None)
                    if usage_cost is not None:
                        reported_cost = float(usage_cost)

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

            latency_ms = round((perf_counter() - started) * 1000, 2)
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content=content,
                status="success",
                usage=GatewayUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                cost=reported_cost,
                latency_ms=latency_ms,
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "streamed": True,
                },
            )
        except ProviderStreamCancelled:
            raise
        except Exception as exc:
            latency_ms = round((perf_counter() - started) * 1000, 2)
            return GatewayResponse(
                request_id=request.request_id,
                provider=self.name,
                model=request.model,
                content="",
                status="error",
                latency_ms=latency_ms,
                error=normalize_provider_error(exc, provider=self.name),
                metadata={
                    "mode": request.mode,
                    "source": request.source,
                    "streamed": True,
                },
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
