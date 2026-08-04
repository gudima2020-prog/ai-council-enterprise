import {
  useEffect,
  useMemo,
  useState,
} from "react";

type WorkspaceInfo = {
  id: string;
  name: string;
  description?: string;
  workspace_type?: string;
  status: string;
};

type PolicyApprovalStatus =
  | "pending"
  | "approved"
  | "denied"
  | "expired"
  | "revoked"
  | "consumed";

type PolicyOperation =
  | "model_inference"
  | "code_execution"
  | "metadata_validation"
  | "artifact_export";

type PolicyApprovalScope = {
  workspace_id: string;
  operation: PolicyOperation;
  policy_version: string;
  policy_fingerprint: string;
  subject_type: string;
  subject_id: string;
  subject_payload: Record<string, unknown>;
};

type PolicyApprovalRecord = {
  id: string;
  scope: PolicyApprovalScope;
  scope_fingerprint: string;
  reason_codes: string[];
  status: PolicyApprovalStatus;
  requested_by: string | null;
  requested_at: string;
  expires_at: string;
  request_note: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  expired_at: string | null;
  revoked_by: string | null;
  revoked_at: string | null;
  revocation_note: string | null;
  consumed_at: string | null;
  metadata: Record<string, unknown>;
  updated_at: string;
};

type PolicyApprovalEvidence = {
  id: string;
  sequence: number;
  approval_id: string;
  workspace_id: string;
  event_type: string;
  actor_id: string | null;
  approval_status: PolicyApprovalStatus;
  payload: Record<string, unknown>;
  occurred_at: string;
  previous_hash: string;
  event_hash: string;
  created_at: string;
};

type ApprovalGrant = {
  approval: PolicyApprovalRecord;
  token: string;
  token_delivery: "one_time";
};

const API_BASE = "http://127.0.0.1:8000/api";

const STATUS_LABELS: Record<
  PolicyApprovalStatus,
  string
> = {
  pending: "Ожидает решения",
  approved: "Одобрено",
  denied: "Отклонено",
  expired: "Истекло",
  revoked: "Отозвано",
  consumed: "Использовано",
};

const OPERATION_LABELS: Record<
  PolicyOperation,
  string
> = {
  model_inference: "Model inference",
  code_execution: "Code execution",
  metadata_validation: "Metadata validation",
  artifact_export: "Artifact export",
};

function apiError(
  data: unknown,
  fallback: string,
): string {
  if (
    typeof data === "object" &&
    data !== null &&
    "detail" in data
  ) {
    const detail = (data as { detail: unknown }).detail;

    if (typeof detail === "string") {
      return detail;
    }

    if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof (
        detail as { message: unknown }
      ).message === "string"
    ) {
      return (
        detail as { message: string }
      ).message;
    }
  }

  return fallback;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function shortHash(value: string): string {
  if (value.length <= 20) return value;
  return `${value.slice(0, 10)}…${value.slice(-8)}`;
}

export default function PolicyApprovalCenter() {
  const [workspaces, setWorkspaces] = useState<
    WorkspaceInfo[]
  >([]);
  const [
    selectedWorkspaceId,
    setSelectedWorkspaceId,
  ] = useState("");
  const [statusFilter, setStatusFilter] =
    useState<PolicyApprovalStatus | "">("");
  const [operationFilter, setOperationFilter] =
    useState<PolicyOperation | "">("");
  const [items, setItems] = useState<
    PolicyApprovalRecord[]
  >([]);
  const [selectedApprovalId, setSelectedApprovalId] =
    useState("");
  const [detail, setDetail] =
    useState<PolicyApprovalRecord | null>(null);
  const [evidence, setEvidence] = useState<
    PolicyApprovalEvidence[]
  >([]);
  const [operatorId, setOperatorId] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [tokenGrant, setTokenGrant] = useState<{
    approvalId: string;
    token: string;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] =
    useState(false);
  const [actionLoading, setActionLoading] =
    useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);

  async function requestJson<T>(
    url: string,
    init?: RequestInit,
  ): Promise<T> {
    const response = await fetch(url, init);
    const data = (await response.json()) as T & {
      detail?: unknown;
    };

    if (!response.ok) {
      throw new Error(
        apiError(
          data,
          "Policy Approval operation failed.",
        ),
      );
    }

    return data;
  }

  useEffect(() => {
    let cancelled = false;

    async function loadWorkspaces() {
      setLoading(true);
      setError("");

      try {
        const data = await requestJson<{
          workspaces?: WorkspaceInfo[];
        }>(
          `${API_BASE}/workspaces?active_only=true`,
        );
        if (cancelled) return;

        const active = data.workspaces ?? [];
        setWorkspaces(active);
        setSelectedWorkspaceId((current) => {
          if (
            current &&
            active.some(
              (workspace) => workspace.id === current,
            )
          ) {
            return current;
          }
          return active[0]?.id ?? "";
        });
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error
              ? caught.message
              : String(caught),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadWorkspaces();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedWorkspaceId) {
      setItems([]);
      setSelectedApprovalId("");
      setDetail(null);
      setEvidence([]);
      return;
    }

    let cancelled = false;

    async function loadApprovals() {
      setLoading(true);
      setError("");
      setMessage("");

      try {
        const params = new URLSearchParams({
          limit: "200",
          offset: "0",
        });
        if (statusFilter) {
          params.set("status", statusFilter);
        }
        if (operationFilter) {
          params.set("operation", operationFilter);
        }

        const data = await requestJson<{
          items: PolicyApprovalRecord[];
        }>(
          `${API_BASE}/workspaces/${encodeURIComponent(
            selectedWorkspaceId,
          )}/policy-approvals?${params.toString()}`,
        );

        if (cancelled) return;

        setItems(data.items);
        setSelectedApprovalId((current) => {
          if (
            current &&
            data.items.some((item) => item.id === current)
          ) {
            return current;
          }
          return data.items[0]?.id ?? "";
        });
      } catch (caught) {
        if (!cancelled) {
          setItems([]);
          setSelectedApprovalId("");
          setDetail(null);
          setEvidence([]);
          setError(
            caught instanceof Error
              ? caught.message
              : String(caught),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadApprovals();

    return () => {
      cancelled = true;
    };
  }, [
    selectedWorkspaceId,
    statusFilter,
    operationFilter,
    revision,
  ]);

  useEffect(() => {
    setTokenGrant(null);
    setCopied(false);
    setDecisionNote("");
  }, [
    selectedWorkspaceId,
    selectedApprovalId,
  ]);

  useEffect(() => {
    if (
      !selectedWorkspaceId ||
      !selectedApprovalId
    ) {
      setDetail(null);
      setEvidence([]);
      return;
    }

    let cancelled = false;

    async function loadDetail() {
      setDetailLoading(true);
      setError("");

      try {
        const base =
          `${API_BASE}/workspaces/${encodeURIComponent(
            selectedWorkspaceId,
          )}/policy-approvals/${encodeURIComponent(
            selectedApprovalId,
          )}`;

        const [record, evidenceResult] =
          await Promise.all([
            requestJson<PolicyApprovalRecord>(base),
            requestJson<{
              items: PolicyApprovalEvidence[];
            }>(`${base}/evidence`),
          ]);

        if (!cancelled) {
          setDetail(record);
          setEvidence(evidenceResult.items);
        }
      } catch (caught) {
        if (!cancelled) {
          setDetail(null);
          setEvidence([]);
          setError(
            caught instanceof Error
              ? caught.message
              : String(caught),
          );
        }
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    }

    void loadDetail();

    return () => {
      cancelled = true;
    };
  }, [
    selectedWorkspaceId,
    selectedApprovalId,
    revision,
  ]);

  const selectedWorkspace = useMemo(
    () =>
      workspaces.find(
        (workspace) =>
          workspace.id === selectedWorkspaceId,
      ) ?? null,
    [workspaces, selectedWorkspaceId],
  );

  const counters = useMemo(() => {
    const result: Record<PolicyApprovalStatus, number> = {
      pending: 0,
      approved: 0,
      denied: 0,
      expired: 0,
      revoked: 0,
      consumed: 0,
    };

    for (const item of items) {
      result[item.status] += 1;
    }

    return result;
  }, [items]);

  async function reconcileExpired() {
    if (!selectedWorkspaceId || actionLoading) return;

    setActionLoading(true);
    setError("");
    setMessage("");

    try {
      const data = await requestJson<{
        expired_count: number;
      }>(
        `${API_BASE}/workspaces/${encodeURIComponent(
          selectedWorkspaceId,
        )}/policy-approvals/reconcile-expired`,
        { method: "POST" },
      );

      setMessage(
        `TTL reconciliation завершён. Истекло: ${
          data.expired_count
        }.`,
      );
      setRevision((value) => value + 1);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : String(caught),
      );
    } finally {
      setActionLoading(false);
    }
  }

  function decisionBody() {
    return JSON.stringify({
      actor_id: operatorId.trim() || null,
      note: decisionNote.trim() || null,
    });
  }

  async function decide(
    action: "approve" | "deny" | "revoke",
  ) {
    if (
      !selectedWorkspaceId ||
      !selectedApprovalId ||
      actionLoading
    ) {
      return;
    }

    const label =
      action === "approve"
        ? "одобрить"
        : action === "deny"
          ? "отклонить"
          : "отозвать";

    const confirmed = window.confirm(
      `Действительно ${label} approval ${selectedApprovalId}?`,
    );
    if (!confirmed) return;

    setActionLoading(true);
    setError("");
    setMessage("");
    setTokenGrant(null);
    setCopied(false);

    try {
      const url =
        `${API_BASE}/workspaces/${encodeURIComponent(
          selectedWorkspaceId,
        )}/policy-approvals/${encodeURIComponent(
          selectedApprovalId,
        )}/${action}`;

      if (action === "approve") {
        const grant = await requestJson<ApprovalGrant>(
          url,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: decisionBody(),
          },
        );

        setTokenGrant({
          approvalId: selectedApprovalId,
          token: grant.token,
        });
        setMessage(
          "Approval одобрен. One-time token показан только в текущем окне.",
        );
      } else {
        await requestJson<PolicyApprovalRecord>(
          url,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: decisionBody(),
          },
        );

        setMessage(
          action === "deny"
            ? "Approval отклонён."
            : "Approval отозван.",
        );
      }

      setRevision((value) => value + 1);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : String(caught),
      );
    } finally {
      setActionLoading(false);
    }
  }

  async function copyToken() {
    if (!tokenGrant) return;

    try {
      await navigator.clipboard.writeText(
        tokenGrant.token,
      );
      setCopied(true);
    } catch {
      setError(
        "Не удалось скопировать token автоматически.",
      );
    }
  }

  return (
    <>
      <header className="topbar approval-topbar">
        <div>
          <div className="eyebrow">
            P2-012 · Policy Approval Workflow
          </div>
          <h1>Approval Center</h1>
          <p>
            Workspace queue, exact subject scope,
            одноразовые capability tokens и
            append-only decision evidence.
          </p>
        </div>
        <button
          onClick={reconcileExpired}
          disabled={
            !selectedWorkspaceId || actionLoading
          }
        >
          {actionLoading
            ? "Обработка…"
            : "Сверить TTL"}
        </button>
      </header>

      <section className="approval-summary-grid">
        {(
          Object.keys(
            STATUS_LABELS,
          ) as PolicyApprovalStatus[]
        ).map((status) => (
          <article
            key={status}
            className={`approval-summary ${status}`}
          >
            <strong>{counters[status]}</strong>
            <span>{STATUS_LABELS[status]}</span>
          </article>
        ))}
      </section>

      <section className="approval-layout">
        <aside className="sandbox-panel approval-queue">
          <div className="panel-heading">
            <div>
              <span className="step">Workspace queue</span>
              <h2>Запросы approval</h2>
            </div>
            <span className="counter">{items.length}</span>
          </div>

          <label className="sandbox-field">
            <span>Рабочее пространство</span>
            <select
              value={selectedWorkspaceId}
              onChange={(event) =>
                setSelectedWorkspaceId(
                  event.target.value,
                )
              }
              disabled={loading || actionLoading}
            >
              {workspaces.length === 0 && (
                <option value="">
                  Нет активных Workspace
                </option>
              )}
              {workspaces.map((workspace) => (
                <option
                  key={workspace.id}
                  value={workspace.id}
                >
                  {workspace.name}
                </option>
              ))}
            </select>
          </label>

          {selectedWorkspace && (
            <div className="approval-workspace-meta">
              <strong>{selectedWorkspace.name}</strong>
              <code>{selectedWorkspace.id}</code>
              <small>
                {selectedWorkspace.workspace_type ??
                  "general"}{" "}
                · {selectedWorkspace.status}
              </small>
            </div>
          )}

          <div className="approval-filter-grid">
            <label className="sandbox-field">
              <span>Статус</span>
              <select
                value={statusFilter}
                onChange={(event) =>
                  setStatusFilter(
                    event.target.value as
                      | PolicyApprovalStatus
                      | "",
                  )
                }
              >
                <option value="">Все статусы</option>
                {(
                  Object.keys(
                    STATUS_LABELS,
                  ) as PolicyApprovalStatus[]
                ).map((status) => (
                  <option key={status} value={status}>
                    {STATUS_LABELS[status]}
                  </option>
                ))}
              </select>
            </label>

            <label className="sandbox-field">
              <span>Операция</span>
              <select
                value={operationFilter}
                onChange={(event) =>
                  setOperationFilter(
                    event.target.value as
                      | PolicyOperation
                      | "",
                  )
                }
              >
                <option value="">Все операции</option>
                {(
                  Object.keys(
                    OPERATION_LABELS,
                  ) as PolicyOperation[]
                ).map((operation) => (
                  <option
                    key={operation}
                    value={operation}
                  >
                    {OPERATION_LABELS[operation]}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="approval-list">
            {items.map((item) => (
              <button
                key={item.id}
                className={`approval-list-item ${
                  selectedApprovalId === item.id
                    ? "active"
                    : ""
                }`}
                onClick={() =>
                  setSelectedApprovalId(item.id)
                }
              >
                <span className="approval-list-top">
                  <em
                    className={`approval-status ${item.status}`}
                  >
                    {STATUS_LABELS[item.status]}
                  </em>
                  <time>
                    {formatDate(item.requested_at)}
                  </time>
                </span>
                <strong>
                  {
                    OPERATION_LABELS[
                      item.scope.operation
                    ]
                  }
                </strong>
                <span>
                  {item.scope.subject_type} ·{" "}
                  {item.scope.subject_id}
                </span>
                <small>
                  {item.reason_codes.join(", ")}
                </small>
              </button>
            ))}

            {!loading && items.length === 0 && (
              <div className="empty-state">
                Approval-запросов с выбранными
                фильтрами нет.
              </div>
            )}

            {loading && (
              <div className="history-loading">
                Обновляю approval queue…
              </div>
            )}
          </div>
        </aside>

        <div className="approval-detail-column">
          {error && (
            <div className="alert error">{error}</div>
          )}
          {message && (
            <div className="alert success">
              {message}
            </div>
          )}

          {tokenGrant && (
            <article className="approval-token-card">
              <div>
                <span className="step">
                  One-time capability
                </span>
                <h2>Сохраните token сейчас</h2>
                <p>
                  После закрытия или смены approval
                  token повторно получить нельзя.
                </p>
              </div>
              <code>{tokenGrant.token}</code>
              <div className="approval-token-actions">
                <button onClick={copyToken}>
                  {copied
                    ? "Скопировано"
                    : "Копировать token"}
                </button>
                <button
                  className="secondary"
                  onClick={() =>
                    setTokenGrant(null)
                  }
                >
                  Скрыть
                </button>
              </div>
            </article>
          )}

          {!selectedApprovalId &&
            !loading && (
              <div className="sandbox-panel">
                <div className="council-placeholder">
                  <h2>Выберите approval</h2>
                  <p>
                    Здесь появятся exact scope,
                    reason codes, TTL, решение и
                    evidence chain.
                  </p>
                </div>
              </div>
            )}

          {selectedApprovalId &&
            detailLoading && (
              <div className="sandbox-panel">
                Загружаю approval detail…
              </div>
            )}

          {detail && !detailLoading && (
            <>
              <article className="sandbox-panel approval-detail">
                <div className="panel-heading">
                  <div>
                    <span className="step">
                      Exact policy subject
                    </span>
                    <h2>
                      {
                        OPERATION_LABELS[
                          detail.scope.operation
                        ]
                      }
                    </h2>
                  </div>
                  <em
                    className={`approval-status ${detail.status}`}
                  >
                    {STATUS_LABELS[detail.status]}
                  </em>
                </div>

                <div className="approval-detail-grid">
                  <span>
                    <small>Approval ID</small>
                    <code>{detail.id}</code>
                  </span>
                  <span>
                    <small>Workspace</small>
                    <code>
                      {detail.scope.workspace_id}
                    </code>
                  </span>
                  <span>
                    <small>Subject</small>
                    <strong>
                      {detail.scope.subject_type}
                    </strong>
                    <code>
                      {detail.scope.subject_id}
                    </code>
                  </span>
                  <span>
                    <small>TTL</small>
                    <strong>
                      {formatDate(detail.expires_at)}
                    </strong>
                  </span>
                  <span>
                    <small>Requested by</small>
                    <strong>
                      {detail.requested_by ?? "system"}
                    </strong>
                  </span>
                  <span>
                    <small>Decision actor</small>
                    <strong>
                      {detail.decided_by ??
                        detail.revoked_by ??
                        "—"}
                    </strong>
                  </span>
                </div>

                <div className="approval-reasons">
                  {detail.reason_codes.map((reason) => (
                    <span key={reason}>{reason}</span>
                  ))}
                </div>

                <details
                  className="approval-scope"
                  open
                >
                  <summary>
                    Exact subject payload
                  </summary>
                  <pre>
                    {JSON.stringify(
                      detail.scope.subject_payload,
                      null,
                      2,
                    )}
                  </pre>
                </details>

                <details className="approval-scope">
                  <summary>
                    Policy and scope fingerprints
                  </summary>
                  <div className="approval-hash-list">
                    <span>
                      <small>
                        policy version
                      </small>
                      <code>
                        {detail.scope.policy_version}
                      </code>
                    </span>
                    <span title={detail.scope.policy_fingerprint}>
                      <small>
                        policy fingerprint
                      </small>
                      <code>
                        {shortHash(
                          detail.scope
                            .policy_fingerprint,
                        )}
                      </code>
                    </span>
                    <span title={detail.scope_fingerprint}>
                      <small>
                        scope fingerprint
                      </small>
                      <code>
                        {shortHash(
                          detail.scope_fingerprint,
                        )}
                      </code>
                    </span>
                  </div>
                </details>
              </article>

              <article className="sandbox-panel approval-decision">
                <div className="panel-heading">
                  <div>
                    <span className="step">
                      Human decision
                    </span>
                    <h2>Решение оператора</h2>
                  </div>
                </div>

                <div className="approval-decision-grid">
                  <label className="sandbox-field">
                    <span>
                      Operator ID · обязателен без
                      authenticated principal
                    </span>
                    <input
                      value={operatorId}
                      onChange={(event) =>
                        setOperatorId(
                          event.target.value,
                        )
                      }
                      placeholder="operator_alpha"
                      maxLength={255}
                      disabled={actionLoading}
                    />
                  </label>

                  <label className="sandbox-field">
                    <span>Комментарий</span>
                    <textarea
                      value={decisionNote}
                      onChange={(event) =>
                        setDecisionNote(
                          event.target.value,
                        )
                      }
                      rows={3}
                      maxLength={4000}
                      placeholder="Причина решения для audit trail"
                      disabled={actionLoading}
                    />
                  </label>
                </div>

                <div className="approval-actions">
                  {detail.status === "pending" && (
                    <>
                      <button
                        onClick={() =>
                          decide("approve")
                        }
                        disabled={actionLoading}
                      >
                        Одобрить
                      </button>
                      <button
                        className="danger-subtle"
                        onClick={() => decide("deny")}
                        disabled={actionLoading}
                      >
                        Отклонить
                      </button>
                    </>
                  )}

                  {detail.status === "approved" && (
                    <button
                      className="danger-subtle"
                      onClick={() => decide("revoke")}
                      disabled={actionLoading}
                    >
                      Отозвать approval
                    </button>
                  )}

                  {!["pending", "approved"].includes(
                    detail.status,
                  ) && (
                    <small>
                      Статус {detail.status} является
                      терминальным; новое решение
                      недоступно.
                    </small>
                  )}
                </div>
              </article>

              <article className="sandbox-panel approval-evidence">
                <div className="panel-heading">
                  <div>
                    <span className="step">
                      Append-only evidence
                    </span>
                    <h2>Decision evidence</h2>
                  </div>
                  <span className="counter">
                    {evidence.length}
                  </span>
                </div>

                <div className="approval-evidence-list">
                  {evidence.map((event) => (
                    <div
                      key={event.id}
                      className="approval-evidence-item"
                    >
                      <span className="approval-evidence-sequence">
                        {event.sequence}
                      </span>
                      <div>
                        <strong>
                          {event.event_type}
                        </strong>
                        <small>
                          {formatDate(
                            event.occurred_at,
                          )}{" "}
                          ·{" "}
                          {event.actor_id ??
                            "runtime"}
                        </small>
                        <pre>
                          {JSON.stringify(
                            event.payload,
                            null,
                            2,
                          )}
                        </pre>
                        <span
                          className="approval-evidence-hash"
                          title={event.event_hash}
                        >
                          hash{" "}
                          {shortHash(
                            event.event_hash,
                          )}
                        </span>
                      </div>
                    </div>
                  ))}

                  {evidence.length === 0 && (
                    <div className="empty-state">
                      Evidence ещё не создан.
                    </div>
                  )}
                </div>
              </article>
            </>
          )}
        </div>
      </section>
    </>
  );
}
