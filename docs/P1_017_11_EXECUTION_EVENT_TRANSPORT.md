# P1-017.11 — Durable Execution Event Transport

## Scope

This stage completes the first Orchestration Engine block with a durable,
database-backed transport for remote executors.

The transport is intentionally broker-independent. It provides an internal
protocol that can later be implemented over PostgreSQL, Redis Streams,
NATS or another message broker without changing the remote executor contract.

## Delivery contract

- delivery semantics: at least once;
- idempotent publish through `event_id` or `idempotency_key`;
- idempotent acknowledgement per consumer and delivery attempt;
- expiring delivery leases;
- lease renewal;
- retry with delay;
- dead-letter state after attempt exhaustion;
- controlled replay of dead-lettered events;
- durable receipts for processed and failed attempts;
- integrity verification endpoint.

The system does not claim exactly-once delivery. Remote executors must use the
provided event identifier and their own side-effect idempotency boundary.

## Remote executor session

A remote executor must already be registered as an Execution Worker. It opens
a short-lived protocol session using the Worker ID and exact `instance_id`.
The server returns a random session token only once and stores only its SHA-256
hash.

A session is invalidated when:

- its TTL expires;
- the Worker heartbeat expires;
- the Worker becomes offline or unhealthy;
- the Worker `instance_id` changes;
- a replacement session is opened;
- the remote executor closes it explicitly.

## Security boundary

Remote publishers may emit only event types with these prefixes:

- `remote_executor.*`
- `execution_remote.*`
- `execution_plan.remote.*`
- `execution_worker.remote.*`

Internal EventBus mirroring is trusted and uses a separate code path.

## Persistent tables

- `execution_remote_sessions`
- `execution_event_envelopes`
- `execution_event_receipts`

## Runtime bridge

Important orchestration events are mirrored into the durable transport:

- plan runtime events;
- step runtime events;
- Worker lifecycle events;
- dispatch and execution lease events;
- tool invocation events;
- delegation events.

Mirroring is idempotent using the original EventBus event ID.

## Operational endpoints

- `GET /api/execution-transport/status`
- `GET /api/execution-transport/protocol`
- `GET /api/execution-transport/verify`
- `POST /api/execution-transport/reconcile`
- session open, heartbeat and close endpoints;
- publish, claim, lease renew, ack and nack endpoints;
- event listing, inspection and dead-letter replay endpoints.

## Database recommendation

SQLite remains supported for local and single-writer deployments. PostgreSQL
is recommended for multiple concurrent backend and remote worker processes.
