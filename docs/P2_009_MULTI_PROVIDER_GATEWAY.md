# P2-009 — Multi-Provider Model Gateway

P2-009 removes the assumption that AI Studio is tied to one LLM gateway.

## Providers

- OpenRouter remains supported.
- SVRTR is added through the generic OpenAI Chat Completions compatible adapter.
- The adapter is reusable for future compatible gateways by supplying provider name, base URL and credential factory.

## Credentials

Preferred configuration uses Secret Manager references. API keys are resolved through ephemeral leases and are not persisted by the gateway health/routing layer.

## Health and circuit breaker

`gateway_provider_stats` stores request counts, success/failure counts, consecutive failures, EMA latency, last error and circuit-open deadline. Three consecutive failover-eligible failures open the circuit for a cooldown period.

## Failover

Failover is explicit. Configure `AI_STUDIO_GATEWAY_FAILOVER_JSON` or route through provider `auto`. Non-recoverable request errors do not silently switch provider. Streaming switches only before the first emitted token.

## Routing

`POST /api/gateway/route` ranks enabled models using catalog quality hints, known token prices, observed provider latency and observed reliability. Strategies: balanced, cost, latency, reliability and quality. Models on an open circuit or belonging to an unconfigured provider are excluded.

## SVRTR catalog

The catalog snapshot is dated 2026-07-24. Sonnet 5 pricing is marked with `valid_until=2026-08-31`; after that date cost estimation becomes unknown until the catalog is refreshed instead of silently using the promotional rate.

## Security boundary

P2-009 is a routing layer. It does not weaken P2-007/P2-008 sandbox policies and it does not grant providers access to secrets or exchange execution credentials. Provider trust/data-classification policy remains a future hardening item.
