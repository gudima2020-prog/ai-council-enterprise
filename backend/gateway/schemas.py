from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass(frozen=True)
class GatewayMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GatewayRequest:
    messages: list[GatewayMessage]
    model: str
    provider: str = "openrouter"
    temperature: float = 0.4
    max_tokens: int = 1000
    timeout_seconds: int = 60
    source: str = "unknown"
    mode: str = "universal"
    project_id: str | None = None
    workspace_id: str | None = None
    data_classification: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"ai_req_{uuid.uuid4().hex}")


@dataclass(frozen=True)
class GatewayUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class GatewayError:
    code: str
    message: str
    provider: str
    recoverable: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResponse:
    request_id: str
    provider: str
    model: str
    content: str
    status: str
    usage: GatewayUsage = field(default_factory=GatewayUsage)
    latency_ms: float | None = None
    cost: float | None = None
    error: GatewayError | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
