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
