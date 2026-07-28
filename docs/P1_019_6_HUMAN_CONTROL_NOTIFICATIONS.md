# P1-019.6 — Human Control Notifications

P1-019.6 adds a persistent notification layer for the Human Control Center.
It converts Human Control events into operator-specific notifications, sends
those notifications through configured delivery channels, retries transient
failures, and tracks read and acknowledgement receipts.

## Delivery model

Delivery is **at least once**. Each notification has a stable idempotency key,
a persistent queue state, and a separate attempt history. Delivery adapters
must therefore treat the notification ID as an idempotency key when they cause
external side effects.

Built-in adapters:

- `in_app` — persistent inbox delivery;
- `log` — server log delivery;
- `webhook` — HTTP/HTTPS JSON POST.

Additional adapters can be registered at runtime for `email`, `slack`,
`telegram`, or `custom` channels.

## Acknowledgement lifecycle

A notification can require an explicit operator acknowledgement:

`pending -> delivering -> delivered -> acknowledged`

If the acknowledgement deadline is exceeded, `ack_status` becomes `overdue`
and the service publishes `human_control.notification.ack_overdue`.
Acknowledgement remains possible after the deadline.

## Security

Management endpoints require `human_control.notification.manage`.
Reading notifications requires `human_control.notification.view`.
Read and acknowledgement receipts require `human_control.notification.ack`.
Authenticated requests remain bound to the Principal and Workspace rules added
in P1-019.4.

Webhook secrets are not embedded into notification payloads. `credential_ref`
is passed only as a reference for a future secret-provider integration.

## Persistence

Tables:

- `human_control_notification_channels`;
- `human_control_notification_subscriptions`;
- `human_control_notifications`;
- `human_control_notification_attempts`;
- `human_control_notification_receipts`.

## Runtime settings

- `AI_STUDIO_HUMAN_CONTROL_NOTIFICATION_SECONDS` — dispatch/reconciliation
  interval, default `10` seconds;
- `AI_STUDIO_HUMAN_CONTROL_NOTIFICATION_BATCH` — maximum notifications per
  dispatch iteration, default `100`.

## Default configuration

The service creates an idempotent global `builtin.in_app` channel and a
`builtin.owner-critical` subscription. The subscription targets active
`owner` and `control_admin` role bindings for high and critical Human Control
events. It requires acknowledgement by default.
