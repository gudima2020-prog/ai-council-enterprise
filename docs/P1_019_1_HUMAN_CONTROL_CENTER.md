# P1-019.1 — Human Control Center

P1-019.1 adds a unified operator inbox above the existing Task Engine,
Mission Governance, resource, and learning services.

## Design principle

`human_control_items` is a normalized projection, not an alternative source of
truth. The original approval, checkpoint, allocation, reservation, or learning
run remains authoritative. A Human Control decision is routed back to that
owning service and then reconciled into the inbox.

## Sources

- pending Task Approval Gates;
- ready Mission Decision Checkpoints that require a human;
- pending Mission Resource Allocations;
- pending Workspace Resource Reservations;
- proposed Mission Learning Runs.

## Operator workflow

1. Synchronize or open the dashboard.
2. Filter by Workspace, risk, source, status, assignment, or overdue state.
3. Claim an item with a time-limited operator lease.
4. Inspect the normalized payload and allowed decisions.
5. Approve, reject, defer, replan, pause, release, or apply as supported by the
   original source.
6. Review the immutable operator action history.

## Safety

- another operator cannot decide an actively claimed item without `force`;
- decision actions are checked against source-specific options;
- every decision requires an idempotency key;
- repeated requests return the original result and do not execute twice;
- snooze only changes operator visibility and never changes the source object;
- bulk decisions are executed per item and return an explicit result for each;
- all `human_control.*` events are written to Audit Trail and mirrored through
  distributed Event Transport.

## API

- `GET /api/human-control/status`
- `POST /api/human-control/sync`
- `GET /api/human-control/dashboard`
- `GET /api/human-control/items`
- `GET /api/human-control/items/{item_id}`
- `POST /api/human-control/items/{item_id}/claim`
- `POST /api/human-control/items/{item_id}/release`
- `POST /api/human-control/items/{item_id}/snooze`
- `POST /api/human-control/items/{item_id}/decision`
- `POST /api/human-control/items/bulk-decision`
- `GET /api/human-control/actions`
