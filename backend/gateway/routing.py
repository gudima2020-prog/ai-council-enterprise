from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import log1p
from typing import Literal

from sqlalchemy.orm import Session

from backend.gateway.health import ProviderHealthService, provider_health
from backend.repositories.models import ModelRepository

RoutingStrategy = Literal["balanced", "cost", "latency", "reliability", "quality"]


@dataclass(frozen=True)
class RoutingRequirements:
    require_tools: bool = False
    require_vision: bool = False
    require_json: bool = False
    expected_input_tokens: int = 2000
    expected_output_tokens: int = 1000


class GatewayRoutingService:
    def __init__(
        self,
        *,
        session: Session,
        health_service: ProviderHealthService | None = None,
    ) -> None:
        self.repository = ModelRepository(session)
        self.health = health_service or provider_health

    @staticmethod
    def _pricing(row) -> tuple[float | None, float | None]:
        metadata = dict(row.metadata_json or {})
        if metadata.get("billing") == "free":
            return 0.0, 0.0
        pricing = metadata.get("pricing")
        if not isinstance(pricing, dict):
            pricing = metadata
        valid_until = pricing.get("valid_until")
        if valid_until:
            try:
                expires = datetime.fromisoformat(str(valid_until)).date()
            except ValueError:
                return None, None
            if datetime.now(timezone.utc).date() > expires:
                return None, None
        try:
            input_price = float(pricing.get("input_per_million_usd"))
            output_price = float(pricing.get("output_per_million_usd"))
        except (TypeError, ValueError):
            return None, None
        if input_price < 0 or output_price < 0:
            return None, None
        return input_price, output_price

    @staticmethod
    def _quality(row) -> float:
        metadata = dict(row.metadata_json or {})
        try:
            value = float(metadata.get("quality_score", 70.0))
        except (TypeError, ValueError):
            value = 70.0
        return max(0.0, min(100.0, value)) / 100.0

    def recommend(
        self,
        *,
        strategy: RoutingStrategy = "balanced",
        requirements: RoutingRequirements | None = None,
        limit: int = 5,
        allowed_providers: set[str] | None = None,
    ) -> list[dict]:
        req = requirements or RoutingRequirements()
        rows = self.repository.list_enabled()
        candidates: list[dict] = []
        for row in rows:
            if row.provider == "auto":
                continue
            if allowed_providers is not None and row.provider not in allowed_providers:
                continue
            metadata = dict(row.metadata_json or {})
            if metadata.get("routing_disabled") is True:
                continue
            if req.require_tools and not row.supports_tools:
                continue
            if req.require_vision and not row.supports_vision:
                continue
            if req.require_json and not row.supports_json:
                continue

            health = self.health.snapshot(row.provider)
            if health["circuit_open"]:
                continue
            reliability = health["reliability"]
            if reliability is None:
                reliability = 0.95
            latency = health["ema_latency_ms"]
            if latency is None:
                try:
                    latency = float(metadata.get("latency_ms_hint", 2500.0))
                except (TypeError, ValueError):
                    latency = 2500.0

            input_price, output_price = self._pricing(row)
            cost = None
            if input_price is not None and output_price is not None:
                cost = (
                    max(0, req.expected_input_tokens) * input_price / 1_000_000
                    + max(0, req.expected_output_tokens) * output_price / 1_000_000
                )

            quality = self._quality(row)
            cost_component = 0.35 if cost is None else 1.0 / (1.0 + cost * 100.0)
            latency_component = 1.0 / (1.0 + max(0.0, latency) / 2000.0)
            reliability_component = max(0.0, min(1.0, float(reliability)))

            weights = {
                "balanced": (0.35, 0.25, 0.20, 0.20),
                "cost": (0.15, 0.60, 0.10, 0.15),
                "latency": (0.15, 0.15, 0.55, 0.15),
                "reliability": (0.15, 0.15, 0.10, 0.60),
                "quality": (0.65, 0.10, 0.10, 0.15),
            }[strategy]
            score = (
                quality * weights[0]
                + cost_component * weights[1]
                + latency_component * weights[2]
                + reliability_component * weights[3]
            )
            # Tiny deterministic preference for explicit catalog priority.
            score += 0.002 / (1.0 + log1p(max(0, int(row.priority))))
            candidates.append(
                {
                    "provider": row.provider,
                    "model": row.slug,
                    "display_name": row.display_name,
                    "score": round(score, 6),
                    "estimated_cost_usd": None if cost is None else round(cost, 8),
                    "pricing_known": cost is not None,
                    "quality_score": round(quality * 100, 2),
                    "reliability": round(float(reliability), 6),
                    "latency_ms": round(float(latency), 2),
                    "health_status": health["status"],
                }
            )
        candidates.sort(
            key=lambda item: (
                -item["score"],
                item["estimated_cost_usd"] is None,
                item["estimated_cost_usd"] or 0.0,
                item["provider"],
                item["model"],
            )
        )
        return candidates[: max(1, min(20, int(limit)))]
