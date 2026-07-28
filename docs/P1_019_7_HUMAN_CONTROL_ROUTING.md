# P1-019.7 — On-call Routing and Notification Escalations

P1-019.7 extends the Human Control notification layer with persistent on-call
schedules, operator availability, availability-aware routing, and controlled
escalation when delivery or acknowledgement fails.

## Compatibility and safe defaults

No routing or escalation rule is created automatically. Existing P1-019.6
subscriptions therefore continue to use their original recipients until an
operator explicitly enables a routing rule. Escalation is also inactive until
an escalation rule is configured or a notification is escalated manually.

## Routing model

Routing strategies:

- `first_available` — select the highest-ranked available operator;
- `round_robin` — distribute notifications across eligible operators;
- `broadcast` — notify all eligible operators up to the configured limit;
- `primary_backup` — prefer primary members and use backups when required.

Fallback modes:

- `base_recipients` — preserve recipients produced by the subscription;
- `fallback_actors` — use explicitly configured fallback actors;
- `broadcast_roles` — resolve fallback operator roles;
- `fail_closed` — create no delivery when no eligible operator exists.

An unroutable notification publishes
`human_control.notification.unroutable`, allowing a dedicated escalation rule
to handle it.

## On-call and availability

Schedules use IANA time zones and member-specific weekday/time windows.
`UTC`, `GMT`, and `Etc/UTC` continue to work without the external Windows
`tzdata` package.

Availability states:

- `available`;
- `busy`;
- `offline`;
- `do_not_disturb`;
- `unknown`.

Availability sources:

- `manual`;
- `heartbeat`;
- `schedule`;
- `system`.

Routing can also enforce operator capacity and active-notification limits.

## Escalation model

Supported triggers:

- `ack_overdue`;
- `delivery_failed`;
- `unroutable`;
- `manual`.

Each escalation has a persistent case and attempt history. Rules may set an
initial delay, repeat interval, maximum escalation count, target schedule,
target roles or actors, target channel, priority increase, and acknowledgement
requirements. An acknowledgement automatically resolves an open escalation for
the notification.

## Persistence

Tables:

- `human_control_on_call_schedules`;
- `human_control_on_call_members`;
- `human_control_operator_availability`;
- `human_control_notification_routing_rules`;
- `human_control_notification_escalation_rules`;
- `human_control_notification_escalations`;
- `human_control_notification_escalation_attempts`.

## Security

Reading routing state requires `human_control.notification.view`.
Changing schedules, routing rules, availability, and escalation state requires
`human_control.notification.manage`, except an operator heartbeat, which uses
`human_control.notification.ack`. Existing Principal, Workspace, RBAC, CSRF,
and Origin controls remain in force.

## Runtime setting

- `AI_STUDIO_HUMAN_CONTROL_ROUTING_SECONDS` — background routing and escalation
  reconciliation interval, default `15` seconds.

## Key endpoints

- `/api/human-control/routing/status`;
- `/api/human-control/routing/dashboard`;
- `/api/human-control/on-call-schedules`;
- `/api/human-control/operator-availability`;
- `/api/human-control/notification-routing-rules`;
- `/api/human-control/notification-routing/evaluate`;
- `/api/human-control/notification-escalation-rules`;
- `/api/human-control/notification-escalations`;
- `/api/human-control/notification-escalations/scan`.
