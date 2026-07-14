from __future__ import annotations

from time import perf_counter

from openai import OpenAI

from backend.gateway.errors import normalize_provider_error
from backend.gateway.providers.base import ProviderAdapter
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
