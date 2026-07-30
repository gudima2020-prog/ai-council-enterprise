import {
  useEffect,
  useMemo,
  useState,
} from "react";

type DataClassification =
  | "public"
  | "internal"
  | "confidential"
  | "restricted";

type ProviderTrust =
  | "local"
  | "trusted_external"
  | "external"
  | "blocked";

type WorkspaceInfo = {
  id: string;
  name: string;
  description?: string;
  workspace_type?: string;
  status: string;
};

type WorkspacePolicy = {
  workspace_id: string;
  provider: string;
  model: string;
  temperature: number;
  max_tokens: number;
  memory_mode: string;
  network_access: string;
  filesystem_access: string;
  data_classification: DataClassification;
  provider_trust: Record<string, ProviderTrust>;
  enabled_plugins: string[];
  disabled_plugins: string[];
};

type GatewayProviderInfo = {
  provider: string;
};

const API_BASE = "http://127.0.0.1:8000/api";

const DATA_CLASSIFICATIONS: DataClassification[] = [
  "public",
  "internal",
  "confidential",
  "restricted",
];

const DATA_CLASSIFICATION_LABELS: Record<
  DataClassification,
  string
> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

const DATA_CLASSIFICATION_HELP: Record<
  DataClassification,
  string
> = {
  public:
    "Публичные данные разрешены для внешних провайдеров согласно их trust tier.",
  internal:
    "Внутренние рабочие данные используют стандартную fail-closed политику.",
  confidential:
    "Локальные провайдеры разрешены; trusted external требует отдельного approval.",
  restricted:
    "Разрешён только локальный provider. Внешние маршруты и экспорт артефактов блокируются.",
};

const PROVIDER_TRUST_LEVELS: ProviderTrust[] = [
  "local",
  "trusted_external",
  "external",
  "blocked",
];

const PROVIDER_TRUST_LABELS: Record<
  ProviderTrust,
  string
> = {
  local: "Local",
  trusted_external: "Trusted external",
  external: "External",
  blocked: "Blocked",
};

function apiError(data: unknown, fallback: string): string {
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
      typeof (detail as { message: unknown }).message === "string"
    ) {
      return (detail as { message: string }).message;
    }
  }

  return fallback;
}

export default function WorkspacePolicyPanel() {
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [providerNames, setProviderNames] = useState<string[]>([]);
  const [policy, setPolicy] = useState<WorkspacePolicy | null>(null);
  const [classification, setClassification] =
    useState<DataClassification>("internal");
  const [providerTrust, setProviderTrust] = useState<
    Record<string, ProviderTrust>
  >({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

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
          "Не удалось выполнить операцию Workspace Policy.",
        ),
      );
    }

    return data;
  }

  function applyPolicy(next: WorkspacePolicy) {
    setPolicy(next);
    setClassification(next.data_classification);
    setProviderTrust(next.provider_trust ?? {});
  }

  useEffect(() => {
    let cancelled = false;

    async function loadCatalog() {
      setLoading(true);
      setError("");

      try {
        const workspaceData = await requestJson<{
          workspaces?: WorkspaceInfo[];
        }>(`${API_BASE}/workspaces?active_only=true`);

        let names: string[] = [];

        try {
          const gatewayResponse = await fetch(
            `${API_BASE}/gateway/providers`,
          );

          if (gatewayResponse.ok) {
            const gatewayData = (await gatewayResponse.json()) as {
              providers?: GatewayProviderInfo[];
            };

            names = Array.from(
              new Set(
                (gatewayData.providers ?? [])
                  .map((item) =>
                    item.provider.trim().toLowerCase(),
                  )
                  .filter(Boolean),
              ),
            ).sort((left, right) => left.localeCompare(right));
          }
        } catch {
          names = [];
        }

        if (cancelled) return;

        const activeWorkspaces = workspaceData.workspaces ?? [];

        setWorkspaces(activeWorkspaces);
        setProviderNames(names);
        setSelectedWorkspaceId((current) => {
          if (
            current &&
            activeWorkspaces.some((item) => item.id === current)
          ) {
            return current;
          }

          return activeWorkspaces[0]?.id ?? "";
        });
      } catch (caught) {
        if (!cancelled) {
          setError(
            caught instanceof Error ? caught.message : String(caught),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadCatalog();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedWorkspaceId) {
      setPolicy(null);
      return;
    }

    let cancelled = false;

    async function loadPolicy() {
      setLoading(true);
      setError("");
      setMessage("");

      try {
        const data = await requestJson<WorkspacePolicy>(
          `${API_BASE}/workspaces/${encodeURIComponent(
            selectedWorkspaceId,
          )}/policy`,
        );

        if (!cancelled) applyPolicy(data);
      } catch (caught) {
        if (!cancelled) {
          setPolicy(null);
          setError(
            caught instanceof Error ? caught.message : String(caught),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void loadPolicy();

    return () => {
      cancelled = true;
    };
  }, [selectedWorkspaceId]);

  const effectiveProviders = useMemo(
    () =>
      Array.from(
        new Set(
          [
            ...providerNames,
            policy?.provider ?? "",
            ...Object.keys(policy?.provider_trust ?? {}),
          ].filter(Boolean),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [providerNames, policy],
  );

  const selectedWorkspace =
    workspaces.find((item) => item.id === selectedWorkspaceId) ?? null;

  function trustFor(provider: string): ProviderTrust {
    return providerTrust[provider] ?? "external";
  }

  async function savePolicy() {
    if (!selectedWorkspaceId || !policy || saving) return;

    setSaving(true);
    setError("");
    setMessage("");

    try {
      const normalizedTrust = Object.fromEntries(
        effectiveProviders.map((provider) => [
          provider,
          trustFor(provider),
        ]),
      ) as Record<string, ProviderTrust>;

      const data = await requestJson<WorkspacePolicy>(
        `${API_BASE}/workspaces/${encodeURIComponent(
          selectedWorkspaceId,
        )}/policy`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            data_classification: classification,
            provider_trust: normalizedTrust,
          }),
        },
      );

      applyPolicy(data);
      setMessage(
        "Классификация данных и trust tiers сохранены. Новые gateway/runtime операции применят policy немедленно.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : String(caught),
      );
    } finally {
      setSaving(false);
    }
  }

  async function resetPolicy() {
    if (!selectedWorkspaceId || saving) return;

    const confirmed = window.confirm(
      "Сбросить все настройки Workspace Policy к системным значениям? Будут сброшены не только trust tiers, но и AI, memory, network, filesystem и plugin overrides.",
    );

    if (!confirmed) return;

    setSaving(true);
    setError("");
    setMessage("");

    try {
      const data = await requestJson<WorkspacePolicy>(
        `${API_BASE}/workspaces/${encodeURIComponent(
          selectedWorkspaceId,
        )}/policy`,
        {
          method: "DELETE",
        },
      );

      applyPolicy(data);
      setMessage(
        "Workspace Policy сброшена к системным значениям.",
      );
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : String(caught),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div>
          <div className="eyebrow">
            P2-011 · Runtime Policy, Trust and Data Classification
          </div>
          <h1>Workspace Security Policy</h1>
          <p>
            Единые fail-closed правила для Model Gateway, Code Sandbox,
            Isolated Runtime и экспорта артефактов.
          </p>
        </div>
      </header>

      <section className="policy-layout">
        <aside className="sandbox-panel policy-workspace-panel">
          <div className="panel-heading">
            <div>
              <span className="step">Workspace</span>
              <h2>Контекст политики</h2>
            </div>
          </div>

          <label className="sandbox-field">
            <span>Активное рабочее пространство</span>
            <select
              value={selectedWorkspaceId}
              onChange={(event) =>
                setSelectedWorkspaceId(event.target.value)
              }
              disabled={loading || saving}
            >
              {workspaces.length === 0 && (
                <option value="">Нет активных Workspace</option>
              )}
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
          </label>

          {selectedWorkspace && (
            <div className="policy-workspace-meta">
              <strong>{selectedWorkspace.name}</strong>
              <span>{selectedWorkspace.id}</span>
              <small>
                {selectedWorkspace.workspace_type ?? "general"} ·{" "}
                {selectedWorkspace.status}
              </small>
            </div>
          )}

          {policy && (
            <div className="policy-effective-summary">
              <span>
                <strong>{policy.provider}</strong>
                default provider
              </span>
              <span>
                <strong>{policy.model}</strong>
                default model
              </span>
              <span>
                <strong>{policy.network_access}</strong>
                network
              </span>
              <span>
                <strong>{policy.filesystem_access}</strong>
                filesystem
              </span>
            </div>
          )}
        </aside>

        <div className="policy-main">
          {loading && (
            <div className="sandbox-panel">
              Загружаю effective policy…
            </div>
          )}

          {!loading && workspaces.length === 0 && (
            <div className="sandbox-panel">
              <div className="alert error">
                Нет активных Workspace. Создайте или активируйте рабочее
                пространство через API перед настройкой политики.
              </div>
            </div>
          )}

          {!loading && policy && (
            <>
              <article className="sandbox-panel">
                <div className="panel-heading">
                  <div>
                    <span className="step">Data boundary</span>
                    <h2>Классификация данных</h2>
                  </div>
                  <span
                    className={`policy-classification-badge ${classification}`}
                  >
                    {DATA_CLASSIFICATION_LABELS[classification]}
                  </span>
                </div>

                <label className="sandbox-field">
                  <span>security.data_classification</span>
                  <select
                    value={classification}
                    onChange={(event) =>
                      setClassification(
                        event.target.value as DataClassification,
                      )
                    }
                    disabled={saving}
                  >
                    {DATA_CLASSIFICATIONS.map((item) => (
                      <option key={item} value={item}>
                        {DATA_CLASSIFICATION_LABELS[item]}
                      </option>
                    ))}
                  </select>
                </label>

                <div
                  className={`policy-classification-note ${classification}`}
                >
                  <strong>
                    {DATA_CLASSIFICATION_LABELS[classification]}
                  </strong>
                  <span>
                    {DATA_CLASSIFICATION_HELP[classification]}
                  </span>
                </div>
              </article>

              <article className="sandbox-panel">
                <div className="panel-heading">
                  <div>
                    <span className="step">Provider boundary</span>
                    <h2>Trust tiers провайдеров</h2>
                  </div>
                  <span className="counter">
                    {effectiveProviders.length}
                  </span>
                </div>

                <div className="policy-trust-list">
                  {effectiveProviders.map((provider) => {
                    const trust = trustFor(provider);

                    return (
                      <label
                        key={provider}
                        className={`policy-trust-row ${trust}`}
                      >
                        <span>
                          <strong>{provider}</strong>
                          <small>
                            {trust === "local"
                              ? "Данные не покидают локальный контур."
                              : trust === "trusted_external"
                                ? "Доверенный внешний provider; sensitive use может требовать approval."
                                : trust === "external"
                                  ? "Обычный внешний provider; sensitive data блокируется."
                                  : "Маршрутизация к provider полностью запрещена."}
                          </small>
                        </span>
                        <select
                          value={trust}
                          onChange={(event) =>
                            setProviderTrust((current) => ({
                              ...current,
                              [provider]:
                                event.target.value as ProviderTrust,
                            }))
                          }
                          disabled={saving}
                        >
                          {PROVIDER_TRUST_LEVELS.map((item) => (
                            <option key={item} value={item}>
                              {PROVIDER_TRUST_LABELS[item]}
                            </option>
                          ))}
                        </select>
                      </label>
                    );
                  })}

                  {effectiveProviders.length === 0 && (
                    <div className="empty-state">
                      Gateway не вернул ни одного provider.
                    </div>
                  )}
                </div>
              </article>

              <article className="sandbox-panel policy-enforcement-panel">
                <div className="panel-heading">
                  <div>
                    <span className="step">Enforcement</span>
                    <h2>Применяемые ограничения</h2>
                  </div>
                </div>

                <div className="policy-enforcement-grid">
                  <span>
                    <strong>Model Gateway</strong>
                    Проверка каждого primary и failover route до
                    credential lease и вызова adapter.
                  </span>
                  <span>
                    <strong>Code execution</strong>
                    Host разрешён только для metadata-only diff_check;
                    код исполняется в isolated container.
                  </span>
                  <span>
                    <strong>Artifacts</strong>
                    Restricted export запрещён; confidential export
                    требует approval.
                  </span>
                  <span>
                    <strong>Audit</strong>
                    Decision reason codes и SHA-256 fingerprint
                    сохраняются в runtime metadata и событиях.
                  </span>
                </div>

                <div className="sandbox-actions">
                  <button onClick={savePolicy} disabled={saving}>
                    {saving ? "Сохранение…" : "Сохранить policy"}
                  </button>
                  <button
                    className="secondary"
                    onClick={resetPolicy}
                    disabled={saving}
                  >
                    Сбросить overrides
                  </button>
                </div>

                {message && (
                  <div className="alert success">{message}</div>
                )}
                {error && <div className="alert error">{error}</div>}
              </article>
            </>
          )}

          {!loading && !policy && error && (
            <div className="sandbox-panel">
              <div className="alert error">{error}</div>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
