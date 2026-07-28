# P1-019.2 — Human Control Governance

P1-019.2 adds operator RBAC, separation of duties, multi-step approval
chains, and escalation handling above the P1-019.1 Human Control Center.

## Compatibility mode

A Workspace remains in legacy-permissive mode until one of these conditions is
true:

- an active role binding exists for the Workspace or globally;
- an enabled approval policy applies to the Workspace.

The first owner is created once through the bootstrap endpoint. After that,
role and policy administration requires an authorized role.

## Built-in roles

- `owner` — unrestricted access;
- `control_admin` — role, policy, override and operational administration;
- `operator` — low and medium risk decisions;
- `senior_operator` — high-risk decisions and approval voting;
- `risk_officer` — critical risk and `accept_risk` authority;
- `budget_officer` — Mission and Workspace resource approvals;
- `learning_officer` — Mission Learning calibration approvals;
- `auditor` — read-only access and audit.

Built-in role permissions are seeded idempotently at application startup.
Custom roles can constrain access by risk level, source type, and decision
action.

## Approval policy matching

Policies can match:

- Workspace;
- Human Control source type;
- normalized action kind;
- risk level;
- requested decision action.

The highest-priority Workspace-specific policy wins over a global policy.
Every approval case stores a snapshot of its steps, so later policy changes do
not rewrite an active chain.

## Separation of duties

Policies can require:

- different operators for different steps;
- a source requester not to approve their own request;
- an Approval Case initiator not to vote;
- one or more approvals at each step;
- explicit role keys for every step.

Votes are idempotent and an operator can vote only once per step.

## Escalation

Each step has a response deadline. The background governance monitor scans for
expired deadlines and creates an escalation. An escalated step can additionally
be handled by its configured escalation roles. Manual escalation is available
through the API.

The monitor interval is configured with:

```powershell
$env:AI_STUDIO_HUMAN_CONTROL_ESCALATION_SCAN_SECONDS = "30"
```

## Decision execution

The original Human Control item remains authoritative. After the final approval
step, the Governance Service invokes the existing P1-019.1 decision router.
Approval cases and votes retain the complete authorization evidence.

## API

### Governance and access

- `GET /api/human-control/governance/status`
- `POST /api/human-control/governance/bootstrap-owner`
- `GET /api/human-control/governance/effective-access`

### Roles and bindings

- `POST /api/human-control/governance/roles`
- `GET /api/human-control/governance/roles`
- `PATCH /api/human-control/governance/roles/{role_id}`
- `POST /api/human-control/governance/role-bindings`
- `GET /api/human-control/governance/role-bindings`
- `POST /api/human-control/governance/role-bindings/{binding_id}/revoke`

### Approval policies and cases

- `POST /api/human-control/governance/approval-policies`
- `GET /api/human-control/governance/approval-policies`
- `PATCH /api/human-control/governance/approval-policies/{policy_id}`
- `GET /api/human-control/items/{item_id}/approval-policy`
- `GET /api/human-control/approval-cases`
- `GET /api/human-control/approval-cases/{case_id}`
- `POST /api/human-control/approval-cases/{case_id}/vote`
- `POST /api/human-control/approval-cases/{case_id}/retry-execution`
- `POST /api/human-control/approval-cases/{case_id}/cancel`

The existing endpoint `POST /api/human-control/items/{item_id}/decision` now
performs a direct decision when no policy applies, or creates and advances an
Approval Case when a policy matches.

### Escalations

- `POST /api/human-control/escalations/scan`
- `POST /api/human-control/approval-cases/{case_id}/escalate`
- `GET /api/human-control/escalations`
- `POST /api/human-control/escalations/{escalation_id}/acknowledge`
- `POST /api/human-control/escalations/{escalation_id}/resolve`

## Security boundary

P1-019.2 authorizes an `actor_id` supplied by the calling layer. It does not yet
cryptographically authenticate that identity. Production deployments should
place these endpoints behind an authenticated identity provider. Identity,
session, and API-token enforcement is planned for the next Human Control stage.
