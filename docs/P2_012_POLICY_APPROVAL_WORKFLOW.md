# P2-012 — Policy Approval Workflow & Decision Evidence

## Статус

P2-012 начат. Подэтап **P2-012.1 Approval Domain Core** реализует
независимую от БД state machine для одноразовых approvals, связанных с точным
Runtime Policy decision и точным operation subject.

## Почему нужен отдельный policy approval domain

В проекте уже есть несколько специализированных механизмов:

- Task Approval Gate управляет жизненным циклом Task, но не связан с Runtime
  Policy fingerprint и не выдаёт одноразовый capability token;
- Code Sandbox Approval связывает token hash с patch fingerprint, но ограничен
  одной sandbox session и не имеет общей pending/approved/denied очереди;
- Council Cost Approval связан с cost fingerprint, но относится только к
  бюджетному preflight.

P2-012 не заменяет эти механизмы автоматически. Он создаёт общий домен именно
для решений `RuntimePolicyEngine`, после чего AI Gateway и artifact export будут
подключены к нему отдельными подэтапами.

## P2-012.1 Domain invariants

- статусы: `pending`, `approved`, `denied`, `expired`, `revoked`, `consumed`;
- approval имеет ограниченный TTL: 60 секунд — 24 часа, default 15 минут;
- raw token возвращается только при approve и не входит в record/evidence;
- хранится только domain-separated SHA-256 token hash;
- token одноразовый: успешная авторизация переводит record в `consumed`;
- approval связан с Workspace, PolicyOperation, policy version и policy
  fingerprint;
- отдельный scope fingerprint связывает approval с точным subject;
- изменение provider/model/request fingerprint/runtime run/artifact hash
  инвалидирует approval;
- сравнение token hash и fingerprints выполняется constant-time;
- ошибки и истечение срока работают fail-closed;
- scope payload и metadata принимают только ограниченный JSON;
- в scope запрещено сохранять raw prompts, responses, secrets и artifact bytes.

## Intended operation subjects

### Model inference

Рекомендуемый subject:

- `subject_type=gateway_route`;
- `subject_id=request_id`;
- provider;
- model;
- hash нормализованного inference request без raw prompt;
- policy decision fingerprint.

### Runtime artifact export

Рекомендуемый subject:

- `subject_type=runtime_artifact`;
- `subject_id=runtime_run_id`;
- artifact ZIP SHA-256;
- artifact manifest fingerprint;
- policy decision fingerprint.

## Следующие подэтапы

### P2-012.2 — Persistence and API

- новая Alembic migration;
- Workspace-isolated approval repository/service;
- pending queue, approve, deny, revoke и expiry reconciliation;
- append-only decision evidence без raw token;
- API schemas и routes.

### P2-012.3 — AI Gateway enforcement

- создание approval request при confidential → trusted external;
- повтор запроса с one-time policy approval token;
- consume до secret resolution и provider adapter;
- запрет failover bypass.

### P2-012.4 — Runtime artifact enforcement

- approval request для confidential artifact export;
- binding к runtime run, ZIP hash и manifest fingerprint;
- consume непосредственно перед чтением/выдачей bytes;
- повторная policy evaluation перед consume.

### P2-012.5 — Approval Center UI and release

- Workspace approval queue;
- evidence, reason codes, TTL и exact subject preview;
- approve/deny/revoke;
- regression, documentation и release verification.

## P2-012.2a — Persistence and Decision Evidence

Реализованы:

- Alembic revision `20260731_0053`;
- Workspace-isolated таблица `policy_approvals`;
- append-only глобальная SHA-256 hash chain `policy_approval_evidence`;
- repository mapping между SQLAlchemy и P2-012.1 domain records;
- idempotent active-scope request;
- approve, deny, revoke, consume и expiry reconciliation;
- raw approval token возвращается только из approve и никогда не сохраняется;
- evidence отклоняет ключи, похожие на token, secret, prompt, response или raw content;
- события содержат только безопасный approval summary.

API routes будут подключены в P2-012.2b.

## P2-012.2b — Policy Approval REST API

The policy approval workflow is exposed through Workspace-scoped routes under
`/api/workspaces/{workspace_id}/policy-approvals`.

The API supports request, list, get, approve, deny, revoke, expiry
reconciliation and immutable evidence retrieval. Runtime token consumption is
deliberately not exposed as a public route; gateway and artifact enforcement
consume the one-time capability internally in later P2-012 phases.

Human Control permissions are reused instead of introducing a parallel RBAC
system:

- view and get: `human_control.view`;
- request: `human_control.approval.initiate`;
- approve and deny: `human_control.approval.vote`;
- revoke and expiry reconciliation: `human_control.override`;
- evidence: `human_control.audit`.

Authenticated actor and Workspace bindings are authoritative. A request cannot
supply a different actor or cross into another Workspace. The approval token is
returned only by the successful approve response and is never returned by get,
list or evidence endpoints. API scope and metadata payloads reject common raw
secret, prompt, response and token fields.

## P2-012.3a — Gateway Approval Coordinator and Atomic Consume

This subphase adds the gateway-specific approval coordinator before it is wired
into the provider execution loop.

The coordinator builds `gateway_route` subjects from the original gateway
`request_id`, provider, model and a SHA-256 fingerprint of normalized inference
parameters. Message content is represented only by per-message SHA-256 hashes;
raw prompts are not persisted.

Policy approval consumption now uses database compare-and-set semantics. The
update succeeds only while the same record remains approved, unexpired and
bound to the expected token hash, policy fingerprint and scope fingerprint.
A stale concurrent session receives a fail-closed state error and cannot
authorize a second external inference.

P2-012.3b will connect the coordinator to both normal and streaming AI Gateway
execution before secret resolution and provider adapter construction.


## P2-012.3b — AI Gateway Enforcement

The AI Gateway creates or reuses an exact Workspace-scoped approval request
for each concrete provider/model route that requires human approval. The
caller resumes the same gateway request with its stable request id, approval
id and one-time token.

The token is never stored in request metadata, response metadata, events or
logs. It is atomically consumed only after an adapter is available and
immediately before non-streaming inference or a streaming provider call.
Failover candidates are evaluated independently, so an approval for one
provider/model route cannot authorize another route. Streaming emits no
content before successful consumption.
