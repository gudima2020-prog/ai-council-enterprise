import React, {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import PolicyApprovalCenter from "./PolicyApprovalCenter";
import WorkspacePolicyPanel from "./WorkspacePolicyPanel";
import "./styles.css";

type View = "chat" | "council" | "gateway" | "sandbox" | "approvals" | "settings";
type Mode = "universal" | "crypto" | "code" | "documents";
type CouncilExecutionMode =
  | "solo"
  | "council"
  | "best_of_n"
  | "review"
  | "arbitration"
  | "delegate";
type CouncilRole =
  | "analyst"
  | "critic"
  | "strategist"
  | "researcher"
  | "risk";

type Message = {
  role: "user" | "assistant" | "system";
  text: string;
};

type ModelInfo = {
  provider: string;
  slug: string;
  display_name: string;
  enabled: boolean;
  metadata?: {
    billing?: "free" | "metered" | string;
    kind?: string;
    catalog_hidden?: boolean;
    input_per_million_usd?: number;
    output_per_million_usd?: number;
    pricing?: {
      input_per_million_usd?: number;
      output_per_million_usd?: number;
    };
  };
};

type CouncilMemberRequest = {
  provider: string;
  model: string;
  role: CouncilRole;
  label: string;
};

type CouncilRunPayload = {
  question: string;
  mode: Mode;
  execution_mode: CouncilExecutionMode;
  member_timeout_seconds: number;
  members: CouncilMemberRequest[];
  synthesizer_provider?: string;
  synthesizer_model?: string;
  reviewer_provider?: string;
  reviewer_model?: string;
  arbiter_provider?: string;
  arbiter_model?: string;
  delegation_max_calls?: number;
  delegation_max_depth?: 1;
  cost_approval_token?: string;
};

type CouncilPreset = {
  id: string;
  workspace_id: string | null;
  name: string;
  description: string;
  mode: Mode;
  execution_mode: CouncilExecutionMode;
  members: CouncilMemberRequest[];
  synthesizer_provider: string | null;
  synthesizer_model: string | null;
  reviewer_provider: string | null;
  reviewer_model: string | null;
  arbiter_provider: string | null;
  arbiter_model: string | null;
  delegation_max_calls: number;
  delegation_max_depth: 1;
  member_timeout_seconds: number;
  created_at: string;
  updated_at: string;
};

type CouncilBudgetPolicy = {
  workspace_id: string | null;
  monthly_budget_usd: number;
  per_run_soft_limit_usd: number;
  per_run_hard_limit_usd: number;
  approval_threshold_usd: number;
  unknown_cost_policy: "allow" | "require_approval" | "block";
  expected_member_output_tokens: number;
  expected_synthesis_output_tokens: number;
  monthly_run_limit: number;
  monthly_token_limit: number;
  per_minute_run_limit: number;
  routing_mode: "off" | "advisory";
  routing_min_savings_percent: number;
};

type CouncilCostEstimate = {
  workspace_id: string | null;
  currency: "USD";
  estimate_status: "known" | "partial" | "unknown";
  estimated_cost_usd: number | null;
  known_cost_usd: number;
  unknown_models: string[];
  monthly_estimated_spend_usd: number;
  monthly_actual_spend_usd: number;
  monthly_reserved_spend_usd: number;
  monthly_run_count: number;
  monthly_reserved_run_count: number;
  monthly_total_tokens: number;
  monthly_reserved_tokens: number;
  projected_monthly_spend_usd: number | null;
  projected_monthly_run_count: number;
  projected_monthly_tokens: number;
  decision: "allow" | "approval_required" | "blocked";
  approval_required: boolean;
  reasons: string[];
  fingerprint: string;
};

type CouncilCostApproval = {
  approval_id: string;
  token: string;
  estimated_cost_usd: number | null;
  estimate_status: string;
  expires_at: string;
};

type CouncilCostUsage = {
  workspace_id: string | null;
  month: string;
  actual_spend_usd: number;
  committed_spend_usd: number;
  reserved_spend_usd: number;
  completed_runs: number;
  reserved_runs: number;
  actual_total_tokens: number;
  reserved_tokens: number;
  monthly_budget_usd: number;
  monthly_run_limit: number;
  monthly_token_limit: number;
  budget_utilization_percent: number | null;
  run_utilization_percent: number | null;
  token_utilization_percent: number | null;
};

type CouncilRoutingPlan = {
  workspace_id: string | null;
  available: boolean;
  original_estimate: CouncilCostEstimate;
  routed_estimate: CouncilCostEstimate | null;
  recommended_members: CouncilMemberRequest[];
  synthesizer_provider: string | null;
  synthesizer_model: string | null;
  reviewer_provider: string | null;
  reviewer_model: string | null;
  arbiter_provider: string | null;
  arbiter_model: string | null;
  changes: Array<{
    kind: "member" | "synthesis" | "reviewer" | "draft" | "planner" | "delegate";
    member_index: number | null;
    provider: string;
    from_model: string;
    to_model: string;
    estimated_savings_usd: number | null;
    reason: string;
  }>;
  estimated_savings_usd: number | null;
  reason: string;
};

type CouncilMemberResult = {
  provider: string;
  model: string;
  requested_model: string | null;
  role: CouncilRole;
  label: string;
  status: "success" | "error";
  fallback_used: boolean;
  fallback_model: string | null;
  answer: string;
  error_code: string | null;
  error_message: string | null;
  latency_ms: number | null;
};

type CouncilSynthesis = {
  provider: string;
  model: string;
  requested_model: string | null;
  status: "structured" | "unstructured" | "fallback";
  fallback_used: boolean;
  fallback_model: string | null;
  final_answer: string;
  consensus: string[];
  disagreements: string[];
  recommendations: string[];
  confidence: number | null;
};


type CouncilAuxiliaryResult = {
  stage: "draft" | "reviewer" | "planner" | "delegate";
  ordinal: number | null;
  provider: string;
  model: string;
  requested_model: string | null;
  label: string;
  status: "success" | "error";
  content: string;
  error_code: string | null;
  error_message: string | null;
  latency_ms: number | null;
  metadata: Record<string, unknown>;
};

type CouncilOrchestrationTrace = {
  execution_mode: CouncilExecutionMode;
  finalizer_stage: "solo" | "synthesis" | "selection" | "revision" | "arbiter";
  auxiliary_calls: CouncilAuxiliaryResult[];
  delegation_depth: number;
  delegation_calls: number;
};

type CouncilResult = {
  run_id: string;
  execution_mode: CouncilExecutionMode;
  status: "completed" | "partial";
  question: string;
  mode: string;
  members: CouncilMemberResult[];
  synthesis: CouncilSynthesis;
  duration_ms: number;
  history_saved?: boolean;
  replay_of_run_id?: string | null;
  estimated_cost_usd?: number | null;
  cost_estimate_status?: "known" | "partial" | "unknown" | null;
  cost_approval_id?: string | null;
  cost_reservation_id?: string | null;
  actual_cost_usd?: number | null;
  actual_cost_status?: "known" | "partial" | "unknown" | null;
  actual_total_tokens?: number | null;
  orchestration: CouncilOrchestrationTrace;
};

type CouncilRunStatus =
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

type CouncilHistorySummary = {
  run_id: string;
  execution_mode: CouncilExecutionMode;
  status: CouncilRunStatus;
  question_preview: string;
  mode: Mode;
  member_count: number;
  successful_member_count: number;
  confidence: number | null;
  duration_ms: number;
  replay_of_run_id: string | null;
  estimated_cost_usd?: number | null;
  cost_estimate_status?: string | null;
  actual_cost_usd?: number | null;
  actual_cost_status?: string | null;
  actual_total_tokens?: number | null;
  started_at: string;
  finished_at: string;
};

type CouncilHistoryPage = {
  workspace_id: string | null;
  items: CouncilHistorySummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

type CouncilHistoryDetail = {
  run_id: string;
  execution_mode: CouncilExecutionMode;
  workspace_id: string | null;
  replay_of_run_id: string | null;
  estimated_cost_usd?: number | null;
  cost_estimate_status?: string | null;
  cost_approval_id?: string | null;
  cost_reservation_id?: string | null;
  actual_cost_usd?: number | null;
  actual_cost_status?: string | null;
  actual_input_tokens?: number | null;
  actual_output_tokens?: number | null;
  actual_total_tokens?: number | null;
  status: CouncilRunStatus;
  question: string;
  mode: Mode;
  members: CouncilMemberResult[];
  synthesis: CouncilSynthesis | null;
  member_count: number;
  successful_member_count: number;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number;
  started_at: string;
  finished_at: string;
  orchestration: CouncilOrchestrationTrace;
};

type CouncilRetentionPolicy = {
  workspace_id: string | null;
  retention_days: number;
  auto_delete_enabled: boolean;
  store_member_answers: boolean;
  store_token_usage: boolean;
};

type CouncilLiveStart = {
  run_id: string;
  status: "queued" | "running";
  kind: "run" | "replay" | "retry_failed";
  replay_of_run_id: string | null;
  events_url: string;
  status_url: string;
  cancel_url: string;
};



type CodeSandboxVerificationItem = {
  profile: "diff_check" | "python_compile" | "pytest" | "frontend_build";
  status: "passed" | "failed" | "skipped";
  exit_code: number | null;
  duration_ms: number;
  output: string;
  execution_backend: "host" | "docker";
  runtime_run_id: string | null;
  image: string | null;
};

type CodeSandboxSession = {
  id: string;
  workspace_id: string | null;
  repo_path: string;
  worktree_path: string;
  base_ref: string;
  base_commit: string;
  status: "active" | "inspected" | "verified" | "applied" | "closed" | "error";
  risk_level: "none" | "low" | "medium" | "high" | "critical";
  approval_required: boolean;
  verification_status: "not_run" | "passed" | "failed";
  patch_fingerprint: string | null;
  patch_sha256: string | null;
  files_changed: number;
  insertions: number;
  deletions: number;
  changed_paths: string[];
  protected_paths: string[];
  blocked_paths: string[];
  verification: CodeSandboxVerificationItem[];
  created_at: string;
  updated_at: string;
  applied_at: string | null;
  closed_at: string | null;
};

type CodeSandboxInspect = {
  session: CodeSandboxSession;
  patch_preview: string;
  patch_truncated: boolean;
};

type CodeSandboxApproval = {
  approval_id: string;
  token: string;
  fingerprint: string;
  expires_at: string;
};


type CodeAgentOperationAudit = {
  action: "write" | "replace" | "delete";
  path: string;
  bytes: number;
  reason: string;
};

type CodeAgentRun = {
  id: string;
  session_id: string;
  workspace_id: string | null;
  council_run_id: string | null;
  status: "created" | "running" | "completed" | "blocked" | "failed";
  provider: string | null;
  model: string | null;
  gateway_request_id: string | null;
  task_sha256: string;
  task_preview: string;
  context_paths: string[];
  writable_paths: string[];
  operations: CodeAgentOperationAudit[];
  summary: string;
  error_message: string;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  actual_cost_usd: number | null;
  cost_status: "known" | "unknown";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};

type CodeAgentRunResponse = {
  run: CodeAgentRun;
  sandbox: CodeSandboxSession;
  inspect: CodeSandboxInspect | null;
};


type RuntimeImageStatus = {
  profile: string;
  image: string;
  present: boolean;
  trusted: boolean;
  image_id: string | null;
};

type IsolatedRuntimeStatus = {
  backend: "docker";
  enabled: boolean;
  docker_cli_available: boolean;
  docker_daemon_available: boolean;
  daemon_error: string;
  network_mode: "none";
  root_filesystem_read_only: boolean;
  source_mount_read_only: boolean;
  no_new_privileges: boolean;
  capabilities_dropped: boolean;
  images: RuntimeImageStatus[];
};

type IsolatedRuntimeRun = {
  id: string;
  session_id: string;
  profile: CodeSandboxVerificationItem["profile"];
  backend: "docker";
  status: "running" | "passed" | "failed" | "timeout";
  image: string;
  image_id: string | null;
  network_mode: "none";
  cpu_limit: number;
  memory_mb: number;
  pids_limit: number;
  timeout_seconds: number;
  exit_code: number | null;
  duration_ms: number;
  timed_out: boolean;
  output: string;
  artifact_paths: string[];
  artifact_bytes: number;
  artifact_available: boolean;
};

type IsolatedRuntimeRunResponse = {
  session: CodeSandboxSession;
  runs: IsolatedRuntimeRun[];
};

type LiveMemberProgress = {
  index: number;
  label: string;
  model: string;
  status: "waiting" | "running" | "success" | "error" | "reused";
  error_code?: string | null;
};

const API_BASE = "http://127.0.0.1:8000/api";
const COUNCIL_ROLES: CouncilRole[] = [
  "analyst",
  "critic",
  "strategist",
  "researcher",
  "risk",
];

const EXECUTION_MODE_LABELS: Record<CouncilExecutionMode, string> = {
  solo: "Solo",
  council: "Council",
  best_of_n: "Best-of-N",
  review: "Review",
  arbitration: "Arbitration",
  delegate: "Delegate",
};

const EXECUTION_MODE_HELP: Record<CouncilExecutionMode, string> = {
  solo: "Одна модель без дополнительного синтеза.",
  council: "Независимые участники и итог председателя.",
  best_of_n: "Несколько вариантов и выбор/синтез лучшего.",
  review: "Черновик председателя → независимый Reviewer → финальная редакция.",
  arbitration: "Независимый Reviewer и отдельный Arbiter разрешают спор между вариантами.",
  delegate: "PRIMARY создаёт ограниченные подзадачи глубины 1 и затем собирает итог.",
};

const MODE_LABELS: Record<Mode, string> = {
  universal: "Универсальный",
  crypto: "Криптотрейдинг",
  code: "Программирование",
  documents: "Документы",
};

const ROLE_LABELS: Record<CouncilRole, string> = {
  analyst: "Аналитик",
  critic: "Критик",
  strategist: "Стратег",
  researcher: "Исследователь",
  risk: "Риск-аналитик",
};

const ERROR_LABELS: Record<string, string> = {
  RATE_LIMIT: "временный лимит",
  TIMEOUT: "тайм-аут",
  NETWORK_ERROR: "ошибка сети",
  MODEL_UNAVAILABLE: "модель недоступна",
  PROVIDER_UNAVAILABLE: "провайдер недоступен",
  INVALID_API_KEY: "ключ API",
  INSUFFICIENT_CREDITS: "недостаточно лимита",
  COUNCIL_MEMBER_TIMEOUT: "тайм-аут участника",
  COUNCIL_MEMBER_CANCELLED: "отменено",
};

const RUN_STATUS_LABELS: Record<CouncilRunStatus, string> = {
  completed: "Завершён",
  partial: "Частичный",
  failed: "Ошибка",
  cancelled: "Отменён",
};

function apiError(data: unknown, fallback: string): string {
  if (
    typeof data === "object" &&
    data !== null &&
    "detail" in data
  ) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof (detail as { message: unknown }).message === "string"
    ) {
      const message = (detail as { message: string }).message;
      const members = (
        detail as {
          members?: Array<{
            error_code?: string | null;
            error_message?: string | null;
          }>;
        }
      ).members;
      const firstFailure = members?.find(
        (member) => member.error_message || member.error_code,
      );
      return firstFailure
        ? `${message} ${firstFailure.error_message ?? firstFailure.error_code}`
        : message;
    }
  }
  return fallback;
}

function App() {
  const [view, setView] = useState<View>("chat");
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "system",
      text: "AI Studio Enterprise v0.15.0: fail-closed Runtime Policy для провайдеров, кода и артефактов.",
    },
  ]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<Mode>("universal");
  const [chatLoading, setChatLoading] = useState(false);

  const [models, setModels] = useState<ModelInfo[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [memberRoles, setMemberRoles] = useState<Record<string, CouncilRole>>({});
  const [chairModelSlug, setChairModelSlug] = useState("");
  const [modelsError, setModelsError] = useState("");
  const [councilQuestion, setCouncilQuestion] = useState("");
  const [councilMode, setCouncilMode] = useState<Mode>("universal");
  const [executionMode, setExecutionMode] = useState<CouncilExecutionMode>("council");
  const [reviewerModelSlug, setReviewerModelSlug] = useState("");
  const [arbiterModelSlug, setArbiterModelSlug] = useState("");
  const [delegationMaxCalls, setDelegationMaxCalls] = useState(4);
  const [councilLoading, setCouncilLoading] = useState(false);
  const [councilError, setCouncilError] = useState("");
  const [councilResult, setCouncilResult] =
    useState<CouncilResult | null>(null);
  const [memberTimeoutSeconds, setMemberTimeoutSeconds] = useState(60);
  const [presets, setPresets] = useState<CouncilPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetDescription, setPresetDescription] = useState("");
  const [presetSaving, setPresetSaving] = useState(false);
  const [costPolicy, setCostPolicy] = useState<CouncilBudgetPolicy | null>(null);
  const [costEstimate, setCostEstimate] = useState<CouncilCostEstimate | null>(null);
  const [costUsage, setCostUsage] = useState<CouncilCostUsage | null>(null);
  const [routingBusy, setRoutingBusy] = useState(false);
  const [costControlLoading, setCostControlLoading] = useState(false);
  const [costControlMessage, setCostControlMessage] = useState("");
  const [liveRunId, setLiveRunId] = useState<string | null>(null);
  const [liveStage, setLiveStage] = useState("");
  const [liveMembers, setLiveMembers] = useState<
    LiveMemberProgress[]
  >([]);
  const [liveSynthesis, setLiveSynthesis] = useState("");
  const liveSourceRef = useRef<EventSource | null>(null);
  const [councilSection, setCouncilSection] =
    useState<"new" | "history">("new");
  const [historyItems, setHistoryItems] = useState<
    CouncilHistorySummary[]
  >([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyStatus, setHistoryStatus] = useState<
    CouncilRunStatus | ""
  >("");
  const [historyMode, setHistoryMode] = useState<Mode | "">("");
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyDateFrom, setHistoryDateFrom] = useState("");
  const [historyDateTo, setHistoryDateTo] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyDetail, setHistoryDetail] =
    useState<CouncilHistoryDetail | null>(null);
  const [historyActionLoading, setHistoryActionLoading] =
    useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [retentionPolicy, setRetentionPolicy] =
    useState<CouncilRetentionPolicy | null>(null);
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [retentionMessage, setRetentionMessage] = useState("");

  useEffect(
    () => () => {
      liveSourceRef.current?.close();
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadModels() {
      try {
        const response = await fetch(
          `${API_BASE}/models?enabled_only=true`,
        );
        const data = (await response.json()) as {
          models?: ModelInfo[];
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(apiError(data, "Не удалось загрузить модели."));
        }
        const enabledModels = (data.models ?? []).filter(
          (item) =>
            item.enabled && item.metadata?.catalog_hidden !== true,
        );
        if (!cancelled) {
          setModels(enabledModels);
          const freeModels = enabledModels.filter(
            (item) => item.metadata?.billing === "free",
          );
          const defaults = (freeModels.length >= 2 ? freeModels : enabledModels)
            .slice(0, 3);
          const defaultSlugs = defaults.map((item) => item.slug);
          setSelectedModels(defaultSlugs);
          setMemberRoles(
            Object.fromEntries(
              defaultSlugs.map((slug, index) => [
                slug,
                COUNCIL_ROLES[index % COUNCIL_ROLES.length],
              ]),
            ) as Record<string, CouncilRole>,
          );
          setChairModelSlug(defaultSlugs[0] ?? enabledModels[0]?.slug ?? "");
        }
      } catch (error) {
        if (!cancelled) {
          setModelsError(
            error instanceof Error ? error.message : String(error),
          );
        }
      }
    }

    loadModels();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (view !== "council" || councilSection !== "new") return;

    let cancelled = false;

    async function loadCouncilControl() {
      try {
        const [presetResponse, policyResponse, usageResponse] = await Promise.all([
          fetch(`${API_BASE}/council/presets`),
          fetch(`${API_BASE}/council/cost/policy`),
          fetch(`${API_BASE}/council/cost/usage`),
        ]);
        const presetData = (await presetResponse.json()) as {
          items?: CouncilPreset[];
          detail?: unknown;
        };
        const policyData = (await policyResponse.json()) as
          CouncilBudgetPolicy & { detail?: unknown };
        const usageData = (await usageResponse.json()) as
          CouncilCostUsage & { detail?: unknown };
        if (!presetResponse.ok) {
          throw new Error(
            apiError(presetData, "Не удалось загрузить составы Совета."),
          );
        }
        if (!policyResponse.ok) {
          throw new Error(
            apiError(policyData, "Не удалось загрузить политику стоимости."),
          );
        }
        if (!usageResponse.ok) {
          throw new Error(
            apiError(usageData, "Не удалось загрузить фактическое использование бюджета."),
          );
        }
        if (!cancelled) {
          setPresets(presetData.items ?? []);
          setCostPolicy(policyData);
          setCostUsage(usageData);
        }
      } catch (error) {
        if (!cancelled) {
          setCouncilError(
            error instanceof Error ? error.message : String(error),
          );
        }
      }
    }

    void loadCouncilControl();
    return () => {
      cancelled = true;
    };
  }, [view, councilSection]);

  useEffect(() => {
    if (view !== "council" || councilSection !== "history") return;

    let cancelled = false;

    async function loadHistory() {
      setHistoryLoading(true);
      setHistoryError("");
      try {
        const params = new URLSearchParams({
          limit: "20",
          offset: String(historyOffset),
        });
        if (historyStatus) params.set("status", historyStatus);
        if (historyMode) params.set("mode", historyMode);
        if (historyQuery.trim()) {
          params.set("query", historyQuery.trim());
        }
        if (historyDateFrom) {
          params.set(
            "started_from",
            `${historyDateFrom}T00:00:00Z`,
          );
        }
        if (historyDateTo) {
          params.set(
            "started_to",
            `${historyDateTo}T23:59:59.999Z`,
          );
        }

        const response = await fetch(
          `${API_BASE}/council/runs?${params.toString()}`,
        );
        const data = (await response.json()) as CouncilHistoryPage & {
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(
            apiError(data, "Не удалось загрузить историю Совета."),
          );
        }
        if (cancelled) return;

        setHistoryItems(data.items);
        setHistoryTotal(data.total);
        setHistoryHasMore(data.has_more);

        const selectedStillVisible = data.items.some(
          (item) => item.run_id === historyDetail?.run_id,
        );
        const nextRunId = selectedStillVisible
          ? historyDetail?.run_id
          : data.items[0]?.run_id;
        if (!nextRunId) {
          setHistoryDetail(null);
          return;
        }

        const detailResponse = await fetch(
          `${API_BASE}/council/runs/${nextRunId}`,
        );
        const detailData =
          (await detailResponse.json()) as CouncilHistoryDetail & {
            detail?: unknown;
          };
        if (!detailResponse.ok) {
          throw new Error(
            apiError(
              detailData,
              "Не удалось открыть сохранённый отчёт.",
            ),
          );
        }
        if (!cancelled) setHistoryDetail(detailData);
      } catch (error) {
        if (!cancelled) {
          setHistoryError(
            error instanceof Error ? error.message : String(error),
          );
        }
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    }

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [
    view,
    councilSection,
    historyOffset,
    historyStatus,
    historyMode,
    historyQuery,
    historyDateFrom,
    historyDateTo,
    historyRevision,
  ]);

  useEffect(() => {
    if (view !== "council" || councilSection !== "history") return;

    let cancelled = false;

    async function loadRetentionPolicy() {
      try {
        const response = await fetch(`${API_BASE}/council/retention`);
        const data = (await response.json()) as CouncilRetentionPolicy & {
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(
            apiError(data, "Не удалось загрузить настройки хранения."),
          );
        }
        if (!cancelled) setRetentionPolicy(data);
      } catch (error) {
        if (!cancelled) {
          setHistoryError(
            error instanceof Error ? error.message : String(error),
          );
        }
      }
    }

    void loadRetentionPolicy();
    return () => {
      cancelled = true;
    };
  }, [view, councilSection]);

  const selectedModelInfo = useMemo(
    () =>
      selectedModels
        .map((slug) => models.find((item) => item.slug === slug))
        .filter((item): item is ModelInfo => item !== undefined),
    [models, selectedModels],
  );
  const chairModelInfo = useMemo(
    () =>
      models.find((item) => item.slug === chairModelSlug) ??
      selectedModelInfo[0] ??
      null,
    [models, chairModelSlug, selectedModelInfo],
  );
  const reviewerModelInfo = useMemo(
    () => models.find((item) => item.slug === reviewerModelSlug) ?? null,
    [models, reviewerModelSlug],
  );
  const arbiterModelInfo = useMemo(
    () => models.find((item) => item.slug === arbiterModelSlug) ?? null,
    [models, arbiterModelSlug],
  );
  const requiredMemberCount = executionMode === "solo" ? 1 : 2;
  const independentReviewerValid =
    executionMode !== "review" && executionMode !== "arbitration"
      ? true
      : Boolean(
          reviewerModelInfo &&
          !selectedModels.includes(reviewerModelInfo.slug) &&
          (executionMode !== "review" || reviewerModelInfo.slug !== chairModelInfo?.slug),
        );
  const independentArbiterValid =
    executionMode !== "arbitration"
      ? true
      : Boolean(
          arbiterModelInfo &&
          !selectedModels.includes(arbiterModelInfo.slug) &&
          arbiterModelInfo.slug !== reviewerModelInfo?.slug,
        );
  const councilSelectionValid =
    selectedModelInfo.length >= requiredMemberCount &&
    (executionMode !== "solo" || selectedModelInfo.length === 1) &&
    independentReviewerValid &&
    independentArbiterValid;
  const estimatedOrchestrationCalls =
    executionMode === "solo"
      ? selectedModelInfo.length
      : executionMode === "review"
        ? selectedModelInfo.length + 3
        : executionMode === "arbitration"
          ? selectedModelInfo.length + 2
          : executionMode === "delegate"
            ? selectedModelInfo.length + delegationMaxCalls + 2
            : selectedModelInfo.length + 1;
  const hasMeteredModels =
    selectedModelInfo.some(
      (item) => item.metadata?.billing === "metered",
    ) ||
    (executionMode !== "solo" &&
      executionMode !== "arbitration" &&
      chairModelInfo?.metadata?.billing === "metered") ||
    ((executionMode === "review" || executionMode === "arbitration") &&
      reviewerModelInfo?.metadata?.billing === "metered") ||
    (executionMode === "arbitration" &&
      arbiterModelInfo?.metadata?.billing === "metered");

  useEffect(() => {
    if (
      view !== "council" ||
      councilSection !== "new" ||
      !councilQuestion.trim() ||
      !councilSelectionValid
    ) {
      setCostEstimate(null);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE}/council/cost/estimate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildCouncilPayload()),
        });
        const data = (await response.json()) as CouncilCostEstimate & {
          detail?: unknown;
        };
        if (!response.ok) {
          throw new Error(
            apiError(data, "Не удалось оценить стоимость запуска."),
          );
        }
        if (!cancelled) setCostEstimate(data);
      } catch (error) {
        if (!cancelled) {
          setCostEstimate(null);
          setCostControlMessage(
            error instanceof Error ? error.message : String(error),
          );
        }
      }
    }, 300);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    view,
    councilSection,
    councilQuestion,
    councilMode,
    memberTimeoutSeconds,
    selectedModelInfo,
    memberRoles,
    chairModelInfo,
    reviewerModelInfo,
    arbiterModelInfo,
    executionMode,
    delegationMaxCalls,
    councilSelectionValid,
  ]);

  async function sendMessage() {
    const text = input.trim();
    if (!text || chatLoading) return;

    setInput("");
    setMessages((previous) => [
      ...previous,
      { role: "user", text },
    ]);
    setChatLoading(true);

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, mode }),
      });
      const data = (await response.json()) as {
        answer?: string;
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(apiError(data, "Ошибка backend."));
      }
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          text: data.answer ?? "Модель вернула пустой ответ.",
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          text: `Ошибка: ${
            error instanceof Error ? error.message : String(error)
          }`,
        },
      ]);
    } finally {
      setChatLoading(false);
    }
  }

  function buildCouncilPayload(questionOverride?: string): CouncilRunPayload {
    const question = (questionOverride ?? councilQuestion).trim();
    const members = selectedModelInfo.map((item, index) => {
      const role =
        memberRoles[item.slug] ??
        COUNCIL_ROLES[index % COUNCIL_ROLES.length];
      return {
        provider: item.provider,
        model: item.slug,
        role,
        label: `${ROLE_LABELS[role]} · ${item.display_name}`,
      };
    });
    return {
      question,
      mode: councilMode,
      execution_mode: executionMode,
      member_timeout_seconds: memberTimeoutSeconds,
      members,
      ...(executionMode !== "solo" && executionMode !== "arbitration" && chairModelInfo
        ? {
            synthesizer_provider: chairModelInfo.provider,
            synthesizer_model: chairModelInfo.slug,
          }
        : {}),
      ...((executionMode === "review" || executionMode === "arbitration") && reviewerModelInfo
        ? {
            reviewer_provider: reviewerModelInfo.provider,
            reviewer_model: reviewerModelInfo.slug,
          }
        : {}),
      ...(executionMode === "arbitration" && arbiterModelInfo
        ? {
            arbiter_provider: arbiterModelInfo.provider,
            arbiter_model: arbiterModelInfo.slug,
          }
        : {}),
      ...(executionMode === "delegate"
        ? { delegation_max_calls: delegationMaxCalls, delegation_max_depth: 1 as const }
        : {}),
    };
  }

  function changeExecutionMode(next: CouncilExecutionMode) {
    setExecutionMode(next);
    setCouncilError("");
    setCostControlMessage("");
    if (next === "solo" && selectedModels.length > 1) {
      const first = selectedModels[0];
      setSelectedModels(first ? [first] : []);
      if (first) setChairModelSlug(first);
    }
  }

  function toggleCouncilModel(slug: string) {
    if (selectedModels.includes(slug)) {
      setSelectedModels((previous) =>
        previous.filter((item) => item !== slug),
      );
      return;
    }
    if (executionMode === "solo") {
      setSelectedModels([slug]);
      setMemberRoles((roles) => ({
        ...roles,
        [slug]: roles[slug] ?? "analyst",
      }));
      setChairModelSlug(slug);
      return;
    }
    if (selectedModels.length >= 6) return;
    setMemberRoles((roles) => ({
      ...roles,
      [slug]:
        roles[slug] ??
        COUNCIL_ROLES[selectedModels.length % COUNCIL_ROLES.length],
    }));
    if (!chairModelSlug) setChairModelSlug(slug);
    setSelectedModels((previous) => [...previous, slug]);
  }

  function applyPreset(presetId: string) {
    setSelectedPresetId(presetId);
    if (!presetId) {
      setPresetName("");
      setPresetDescription("");
      return;
    }
    const preset = presets.find((item) => item.id === presetId);
    if (!preset) return;

    const availableMembers = preset.members.filter((member) =>
      models.some(
        (item) =>
          item.slug === member.model && item.provider === member.provider,
      ),
    );
    setSelectedModels(availableMembers.map((member) => member.model));
    setMemberRoles(
      Object.fromEntries(
        availableMembers.map((member) => [member.model, member.role]),
      ) as Record<string, CouncilRole>,
    );
    setCouncilMode(preset.mode);
    setExecutionMode(preset.execution_mode ?? "council");
    setMemberTimeoutSeconds(preset.member_timeout_seconds);
    setDelegationMaxCalls(preset.delegation_max_calls ?? 4);
    setPresetName(preset.name);
    setPresetDescription(preset.description);
    const chairAvailable = models.some(
      (item) =>
        item.slug === preset.synthesizer_model &&
        (!preset.synthesizer_provider ||
          item.provider === preset.synthesizer_provider),
    );
    setChairModelSlug(
      chairAvailable
        ? preset.synthesizer_model ?? ""
        : availableMembers[0]?.model ?? "",
    );
    const reviewerAvailable = models.some(
      (item) =>
        item.slug === preset.reviewer_model &&
        (!preset.reviewer_provider || item.provider === preset.reviewer_provider),
    );
    setReviewerModelSlug(reviewerAvailable ? preset.reviewer_model ?? "" : "");
    const arbiterAvailable = models.some(
      (item) =>
        item.slug === preset.arbiter_model &&
        (!preset.arbiter_provider || item.provider === preset.arbiter_provider),
    );
    setArbiterModelSlug(arbiterAvailable ? preset.arbiter_model ?? "" : "");
    if (availableMembers.length !== preset.members.length) {
      setCouncilError(
        "В сохранённом составе есть модели, которых сейчас нет в активном каталоге. Доступные участники загружены.",
      );
    } else {
      setCouncilError("");
    }
  }

  async function saveCouncilPreset() {
    const name = presetName.trim();
    if (presetSaving || !name || !councilSelectionValid) return;
    setPresetSaving(true);
    setCouncilError("");
    setCostControlMessage("");
    try {
      const current = buildCouncilPayload("preset");
      const body = {
        name,
        description: presetDescription.trim(),
        mode: current.mode,
        execution_mode: current.execution_mode,
        members: current.members,
        synthesizer_provider: current.synthesizer_provider ?? null,
        synthesizer_model: current.synthesizer_model ?? null,
        reviewer_provider: current.reviewer_provider ?? null,
        reviewer_model: current.reviewer_model ?? null,
        arbiter_provider: current.arbiter_provider ?? null,
        arbiter_model: current.arbiter_model ?? null,
        delegation_max_calls: current.delegation_max_calls ?? 4,
        delegation_max_depth: current.delegation_max_depth ?? 1,
        member_timeout_seconds: current.member_timeout_seconds,
      };
      const url = selectedPresetId
        ? `${API_BASE}/council/presets/${selectedPresetId}`
        : `${API_BASE}/council/presets`;
      const response = await fetch(url, {
        method: selectedPresetId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = (await response.json()) as CouncilPreset & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось сохранить состав Совета."),
        );
      }
      setPresets((previous) =>
        [...previous.filter((item) => item.id !== data.id), data].sort(
          (left, right) => left.name.localeCompare(right.name, "ru"),
        ),
      );
      setSelectedPresetId(data.id);
      setPresetName(data.name);
      setPresetDescription(data.description);
      setCostControlMessage(
        selectedPresetId ? "Состав обновлён." : "Состав сохранён.",
      );
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setPresetSaving(false);
    }
  }

  async function deleteCouncilPreset() {
    if (!selectedPresetId || presetSaving) return;
    const preset = presets.find((item) => item.id === selectedPresetId);
    if (
      !window.confirm(
        `Удалить сохранённый состав «${preset?.name ?? "Совет"}»?`,
      )
    ) {
      return;
    }
    setPresetSaving(true);
    try {
      const response = await fetch(
        `${API_BASE}/council/presets/${selectedPresetId}`,
        { method: "DELETE" },
      );
      const data = (await response.json()) as {
        deleted?: boolean;
        detail?: unknown;
      };
      if (!response.ok || !data.deleted) {
        throw new Error(
          apiError(data, "Не удалось удалить сохранённый состав."),
        );
      }
      setPresets((previous) =>
        previous.filter((item) => item.id !== selectedPresetId),
      );
      setSelectedPresetId("");
      setPresetName("");
      setPresetDescription("");
      setCostControlMessage("Состав удалён.");
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setPresetSaving(false);
    }
  }

  async function refreshCostUsage() {
    try {
      const response = await fetch(`${API_BASE}/council/cost/usage`);
      const data = (await response.json()) as CouncilCostUsage & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось обновить фактическое использование бюджета."),
        );
      }
      setCostUsage(data);
    } catch (error) {
      setCostControlMessage(
        error instanceof Error ? error.message : String(error),
      );
    }
  }

  async function optimizeCouncilComposition() {
    if (routingBusy || !councilSelectionValid) return;
    setRoutingBusy(true);
    setCouncilError("");
    setCostControlMessage("");
    try {
      const response = await fetch(`${API_BASE}/council/cost/route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildCouncilPayload(councilQuestion.trim() || "Оценка состава Совета")),
      });
      const data = (await response.json()) as CouncilRoutingPlan & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось построить экономичный маршрут моделей."),
        );
      }
      if (!data.available) {
        setCostControlMessage(
          data.reason === "routing_disabled_by_workspace_policy"
            ? "Budget-aware routing отключён политикой Workspace."
            : "Более дешёвого совместимого состава с известным тарифом не найдено.",
        );
        return;
      }
      const availableMembers = data.recommended_members.filter((member) =>
        models.some(
          (item) => item.slug === member.model && item.provider === member.provider,
        ),
      );
      if (availableMembers.length < requiredMemberCount) {
        throw new Error("Маршрутизатор вернул недостаточно доступных моделей.");
      }
      setSelectedModels(availableMembers.map((member) => member.model));
      setMemberRoles(
        Object.fromEntries(
          availableMembers.map((member) => [member.model, member.role]),
        ) as Record<string, CouncilRole>,
      );
      if (
        data.synthesizer_model &&
        models.some(
          (item) =>
            item.slug === data.synthesizer_model &&
            (!data.synthesizer_provider || item.provider === data.synthesizer_provider),
        )
      ) {
        setChairModelSlug(data.synthesizer_model);
      }
      if (
        data.reviewer_model &&
        models.some(
          (item) =>
            item.slug === data.reviewer_model &&
            (!data.reviewer_provider || item.provider === data.reviewer_provider),
        )
      ) {
        setReviewerModelSlug(data.reviewer_model);
      }
      if (
        data.arbiter_model &&
        models.some(
          (item) =>
            item.slug === data.arbiter_model &&
            (!data.arbiter_provider || item.provider === data.arbiter_provider),
        )
      ) {
        setArbiterModelSlug(data.arbiter_model);
      }
      setCostEstimate(data.routed_estimate ?? data.original_estimate);
      const savings =
        data.estimated_savings_usd === null
          ? ""
          : ` Экономия ≈ $${data.estimated_savings_usd.toFixed(4)} за запуск.`;
      setCostControlMessage(
        `Применён budget-aware состав: ${data.changes.length} замен.${savings}`,
      );
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setRoutingBusy(false);
    }
  }

  async function saveCostPolicy() {
    if (!costPolicy || costControlLoading) return;
    setCostControlLoading(true);
    setCouncilError("");
    setCostControlMessage("");
    try {
      const response = await fetch(`${API_BASE}/council/cost/policy`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          monthly_budget_usd: costPolicy.monthly_budget_usd,
          per_run_soft_limit_usd: costPolicy.per_run_soft_limit_usd,
          per_run_hard_limit_usd: costPolicy.per_run_hard_limit_usd,
          approval_threshold_usd: costPolicy.approval_threshold_usd,
          unknown_cost_policy: costPolicy.unknown_cost_policy,
          expected_member_output_tokens:
            costPolicy.expected_member_output_tokens,
          expected_synthesis_output_tokens:
            costPolicy.expected_synthesis_output_tokens,
          monthly_run_limit: costPolicy.monthly_run_limit,
          monthly_token_limit: costPolicy.monthly_token_limit,
          per_minute_run_limit: costPolicy.per_minute_run_limit,
          routing_mode: costPolicy.routing_mode,
          routing_min_savings_percent: costPolicy.routing_min_savings_percent,
        }),
      });
      const data = (await response.json()) as CouncilBudgetPolicy & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось сохранить лимиты стоимости."),
        );
      }
      setCostPolicy(data);
      await refreshCostUsage();
      setCostControlMessage("Политика бюджета, квот и маршрутизации сохранена.");
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setCostControlLoading(false);
    }
  }

  function costApprovalPrompt(estimate: CouncilCostEstimate): string {
    const cost =
      estimate.estimated_cost_usd === null
        ? `известная часть ≈ $${estimate.known_cost_usd.toFixed(4)}, полный тариф неизвестен`
        : `оценка ≈ $${estimate.estimated_cost_usd.toFixed(4)}`;
    const unknown = estimate.unknown_models.length
      ? `\nНеизвестен прайс: ${estimate.unknown_models.join(", ")}.`
      : "";
    return `Запуск требует подтверждения стоимости: ${cost}.${unknown}\nПродолжить один раз?`;
  }

  async function authorizeCouncilPayload(
    payload: CouncilRunPayload,
  ): Promise<CouncilRunPayload> {
    const estimateResponse = await fetch(
      `${API_BASE}/council/cost/estimate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    const estimateData =
      (await estimateResponse.json()) as CouncilCostEstimate & {
        detail?: unknown;
      };
    if (!estimateResponse.ok) {
      throw new Error(
        apiError(estimateData, "Не удалось оценить стоимость запуска."),
      );
    }
    setCostEstimate(estimateData);
    if (estimateData.decision === "blocked") {
      throw new Error(
        "Запуск заблокирован политикой бюджета Workspace. Измените состав или лимиты.",
      );
    }
    if (!estimateData.approval_required) return payload;
    if (!window.confirm(costApprovalPrompt(estimateData))) {
      throw new Error("Запуск отменён: стоимость не подтверждена.");
    }

    const approvalResponse = await fetch(
      `${API_BASE}/council/cost/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    const approvalData =
      (await approvalResponse.json()) as CouncilCostApproval & {
        detail?: unknown;
      };
    if (!approvalResponse.ok || !approvalData.token) {
      throw new Error(
        apiError(
          approvalData,
          "Не удалось получить одноразовое подтверждение стоимости.",
        ),
      );
    }
    return { ...payload, cost_approval_token: approvalData.token };
  }

  function updateLiveMember(
    index: number,
    patch: Partial<LiveMemberProgress>,
  ) {
    setLiveMembers((previous) => {
      const existing = previous.find((item) => item.index === index);
      const next: LiveMemberProgress = {
        index,
        label: existing?.label ?? `Участник ${index + 1}`,
        model: existing?.model ?? "",
        status: existing?.status ?? "waiting",
        ...patch,
      };
      return [
        ...previous.filter((item) => item.index !== index),
        next,
      ].sort((left, right) => left.index - right.index);
    });
  }

  function connectLiveRun(
    start: CouncilLiveStart,
    initialMembers: LiveMemberProgress[],
  ) {
    liveSourceRef.current?.close();
    setLiveRunId(start.run_id);
    setLiveStage("Запуск принят, подключаю живой прогресс…");
    setLiveMembers(initialMembers);
    setLiveSynthesis("");

    const source = new EventSource(
      `${API_BASE}/council/live/${start.run_id}/events`,
    );
    liveSourceRef.current = source;

    const dataOf = (event: Event): Record<string, unknown> => {
      try {
        return JSON.parse(
          (event as MessageEvent<string>).data,
        ) as Record<string, unknown>;
      } catch {
        return {};
      }
    };
    const listen = (
      eventType: string,
      handler: (data: Record<string, unknown>) => void,
    ) => {
      source.addEventListener(eventType, (event) => {
        if (liveSourceRef.current !== source) return;
        handler(dataOf(event));
      });
    };
    const finish = () => {
      if (liveSourceRef.current === source) {
        source.close();
        liveSourceRef.current = null;
      }
      setLiveRunId(null);
      setCouncilLoading(false);
      setHistoryRevision((value) => value + 1);
      void refreshCostUsage();
    };

    listen("run.accepted", () => {
      setLiveStage("Запуск поставлен в очередь.");
    });
    listen("run.started", () => {
      setLiveStage("Модели анализируют вопрос параллельно.");
    });
    listen("run.cancel.requested", () => {
      setLiveStage("Отмена принята, останавливаю активные запросы…");
    });
    listen("member.started", (data) => {
      const index = Number(data.member_index);
      if (!Number.isFinite(index)) return;
      updateLiveMember(index, {
        label: String(data.label ?? `Участник ${index + 1}`),
        model: String(data.model ?? ""),
        status: "running",
      });
    });
    for (const eventType of [
      "member.completed",
      "member.failed",
      "member.reused",
    ]) {
      listen(eventType, (data) => {
        const index = Number(data.member_index);
        const member = data.member as
          | CouncilMemberResult
          | undefined;
        if (!Number.isFinite(index) || !member) return;
        updateLiveMember(index, {
          label: member.label,
          model: member.requested_model ?? member.model,
          status:
            eventType === "member.reused"
              ? "reused"
              : member.status,
          error_code: member.error_code,
        });
      });
    }
    const stageLabels: Record<string, string> = {
      draft: "PRIMARY формирует черновик для независимой проверки.",
      reviewer: "Независимый Reviewer проверяет аргументы и выводы.",
      selection: "PRIMARY сравнивает варианты и выбирает лучший.",
      revision: "PRIMARY перерабатывает ответ с учётом review.",
      arbiter: "Независимый Arbiter выносит итоговое решение.",
      planner: "PRIMARY проектирует ограниченные подзадачи.",
      delegate: "Субагенты выполняют делегированные подзадачи глубины 1.",
    };
    for (const stage of Object.keys(stageLabels)) {
      listen(`${stage}.started`, () => {
        setLiveStage(stageLabels[stage]);
        if (["draft", "selection", "revision", "arbiter"].includes(stage)) {
          setLiveSynthesis("");
        }
      });
      listen(`${stage}.delta`, (data) => {
        if (typeof data.delta !== "string") return;
        setLiveSynthesis((previous) =>
          `${previous}${data.delta as string}`.slice(-50000),
        );
      });
      listen(`${stage}.completed`, () => {
        setLiveStage(`${EXECUTION_MODE_LABELS[executionMode]}: этап ${stage} завершён.`);
      });
      listen(`${stage}.failed`, () => {
        setLiveStage(`${EXECUTION_MODE_LABELS[executionMode]}: этап ${stage} завершился с ошибкой; продолжаю по политике partial.`);
      });
    }
    listen("synthesis.started", () => {
      setLiveStage("PRIMARY формирует общий итог.");
      setLiveSynthesis("");
    });
    listen("synthesis.delta", (data) => {
      if (typeof data.delta !== "string") return;
      setLiveSynthesis((previous) =>
        `${previous}${data.delta as string}`.slice(-50000),
      );
    });
    listen("synthesis.completed", () => {
      setLiveStage("Итог сформирован, сохраняю отчёт.");
    });
    listen("history.failed", () => {
      setLiveStage(
        "Итог готов, но сохранить отчёт в историю не удалось.",
      );
    });
    listen("run.completed", (data) => {
      const result = data.result as CouncilResult | undefined;
      if (result) setCouncilResult(result);
      setLiveStage("Анализ завершён.");
      finish();
    });
    listen("run.failed", (data) => {
      setCouncilError(
        apiError(data, "Совет ИИ не смог завершить анализ."),
      );
      setLiveStage("Запуск завершился с ошибкой.");
      finish();
    });
    listen("run.cancelled", (data) => {
      setCouncilError(
        apiError(data, "Запуск Совета отменён пользователем."),
      );
      setLiveStage("Запуск отменён.");
      finish();
    });

    source.onerror = () => {
      if (liveSourceRef.current === source) {
        setLiveStage(
          "Связь с потоком прервана, выполняется переподключение…",
        );
      }
    };
  }

  async function runCouncil() {
    const question = councilQuestion.trim();
    if (
      !question ||
      councilLoading ||
      !councilSelectionValid
    ) {
      return;
    }

    const basePayload = buildCouncilPayload(question);
    const initialMembers = basePayload.members.map((member, index) => ({
      index,
      label: member.label,
      model: member.model,
      status: "waiting" as const,
    }));
    setCouncilLoading(true);
    setCouncilError("");
    setCouncilResult(null);
    setLiveStage("Проверяю бюджет и стоимость запуска…");
    setLiveMembers(initialMembers);
    setLiveSynthesis("");

    try {
      const payload = await authorizeCouncilPayload(basePayload);
      setLiveStage("Отправляю запуск Совета…");
      const response = await fetch(`${API_BASE}/council/live`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as CouncilLiveStart & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось запустить Совет ИИ."),
        );
      }
      connectLiveRun(data, initialMembers);
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
      setCouncilLoading(false);
      setLiveRunId(null);
    }
  }

  async function cancelLiveRun() {
    if (!liveRunId || !councilLoading) return;
    setLiveStage("Отправляю команду отмены…");
    try {
      const response = await fetch(
        `${API_BASE}/council/live/${liveRunId}`,
        { method: "DELETE" },
      );
      const data = (await response.json()) as {
        cancellation_requested?: boolean;
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось отменить запуск Совета."),
        );
      }
      if (data.cancellation_requested) {
        setLiveStage(
          "Отмена принята, ожидаю завершения активных запросов…",
        );
      }
    } catch (error) {
      setCouncilError(
        error instanceof Error ? error.message : String(error),
      );
    }
  }

  async function openHistoryRun(runId: string) {
    if (historyLoading || historyActionLoading) return;
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const response = await fetch(
        `${API_BASE}/council/runs/${runId}`,
      );
      const data = (await response.json()) as CouncilHistoryDetail & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось открыть сохранённый отчёт."),
        );
      }
      setHistoryDetail(data);
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setHistoryLoading(false);
    }
  }

  async function postHistoryLiveWithCostApproval(
    actionUrl: string,
    approvalUrl: string,
  ): Promise<CouncilLiveStart> {
    async function postAction(token?: string) {
      const separator = actionUrl.includes("?") ? "&" : "?";
      const url = token
        ? `${actionUrl}${separator}approval_token=${encodeURIComponent(token)}`
        : actionUrl;
      const response = await fetch(url, { method: "POST" });
      const data = (await response.json()) as CouncilLiveStart & {
        detail?: {
          code?: string;
          message?: string;
          estimate?: CouncilCostEstimate;
        } | string;
      };
      return { response, data };
    }

    const first = await postAction();
    if (first.response.ok) return first.data;
    const detail =
      typeof first.data.detail === "object" && first.data.detail !== null
        ? first.data.detail
        : null;
    if (
      first.response.status !== 402 ||
      detail?.code !== "COUNCIL_COST_APPROVAL_REQUIRED" ||
      !detail.estimate
    ) {
      throw new Error(
        apiError(first.data, "Операция Совета не выполнена."),
      );
    }
    if (!window.confirm(costApprovalPrompt(detail.estimate))) {
      throw new Error("Операция отменена: стоимость не подтверждена.");
    }

    const approvalResponse = await fetch(approvalUrl, { method: "POST" });
    const approvalData =
      (await approvalResponse.json()) as CouncilCostApproval & {
        detail?: unknown;
      };
    if (!approvalResponse.ok || !approvalData.token) {
      throw new Error(
        apiError(
          approvalData,
          "Не удалось подтвердить стоимость повторного запуска.",
        ),
      );
    }
    const second = await postAction(approvalData.token);
    if (!second.response.ok) {
      throw new Error(
        apiError(second.data, "Операция Совета не выполнена."),
      );
    }
    return second.data;
  }

  async function replayHistoryRun() {
    if (!historyDetail || historyActionLoading) return;

    const containsMeteredModel = historyDetail.members.some((member) => {
      const requestedModel = member.requested_model ?? member.model;
      return models.find((item) => item.slug === requestedModel)
        ?.metadata?.billing === "metered";
    });
    const confirmed = window.confirm(
      containsMeteredModel
        ? "Повторить этот запуск? В составе есть тарифицируемая модель, поэтому OpenRouter может списать средства."
        : "Повторить этот запуск? Будут выполнены новые запросы к моделям.",
    );
    if (!confirmed) return;

    setHistoryActionLoading(true);
    setHistoryError("");
    setCouncilError("");
    setCouncilResult(null);
    setCouncilLoading(true);
    try {
      const data = await postHistoryLiveWithCostApproval(
        `${API_BASE}/council/runs/${historyDetail.run_id}/replay/live`,
        `${API_BASE}/council/runs/${historyDetail.run_id}/cost/approve-replay`,
      );

      setCouncilQuestion(historyDetail.question);
      setCouncilMode(historyDetail.mode);
      const replayModels = historyDetail.members
        .map((member) => member.requested_model ?? member.model)
        .filter((slug) => models.some((item) => item.slug === slug));
      if (replayModels.length >= 2) {
        setSelectedModels(replayModels);
        setMemberRoles(
          Object.fromEntries(
            historyDetail.members
              .filter((member) => replayModels.includes(member.requested_model ?? member.model))
              .map((member) => [
                member.requested_model ?? member.model,
                member.role,
              ]),
          ) as Record<string, CouncilRole>,
        );
      }
      const replayChair =
        historyDetail.synthesis?.requested_model ??
        historyDetail.synthesis?.model ??
        replayModels[0];
      if (replayChair && models.some((item) => item.slug === replayChair)) {
        setChairModelSlug(replayChair);
      }
      setCouncilSection("new");
      connectLiveRun(
        data,
        historyDetail.members.map((member, index) => ({
          index,
          label: member.label,
          model: member.requested_model ?? member.model,
          status: "waiting",
        })),
      );
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
      setCouncilLoading(false);
    } finally {
      setHistoryActionLoading(false);
    }
  }

  async function retryFailedHistoryRun() {
    if (!historyDetail || historyActionLoading) return;
    const failedMembers = historyDetail.members.filter(
      (member) => member.status === "error",
    );
    if (failedMembers.length === 0) return;

    const containsMeteredModel = failedMembers.some((member) => {
      const requestedModel = member.requested_model ?? member.model;
      return models.find((item) => item.slug === requestedModel)
        ?.metadata?.billing === "metered";
    });
    const confirmed = window.confirm(
      containsMeteredModel
        ? `Повторить только ${failedMembers.length} неуспешных участников? Среди них есть тарифицируемая модель.`
        : `Повторить только ${failedMembers.length} неуспешных участников? Успешные сохранённые ответы будут использованы повторно.`,
    );
    if (!confirmed) return;

    setHistoryActionLoading(true);
    setHistoryError("");
    setCouncilError("");
    setCouncilResult(null);
    setCouncilLoading(true);
    try {
      const data = await postHistoryLiveWithCostApproval(
        `${API_BASE}/council/runs/${historyDetail.run_id}/retry-failed`,
        `${API_BASE}/council/runs/${historyDetail.run_id}/cost/approve-retry-failed`,
      );

      setCouncilQuestion(historyDetail.question);
      setCouncilMode(historyDetail.mode);
      const retryModels = historyDetail.members
        .map((member) => member.requested_model ?? member.model)
        .filter((slug) => models.some((item) => item.slug === slug));
      if (retryModels.length >= 2) {
        setSelectedModels(retryModels);
        setMemberRoles(
          Object.fromEntries(
            historyDetail.members
              .filter((member) => retryModels.includes(member.requested_model ?? member.model))
              .map((member) => [
                member.requested_model ?? member.model,
                member.role,
              ]),
          ) as Record<string, CouncilRole>,
        );
      }
      const retryChair =
        historyDetail.synthesis?.requested_model ??
        historyDetail.synthesis?.model ??
        retryModels[0];
      if (retryChair && models.some((item) => item.slug === retryChair)) {
        setChairModelSlug(retryChair);
      }
      setCouncilSection("new");
      connectLiveRun(
        data,
        historyDetail.members.map((member, index) => ({
          index,
          label: member.label,
          model: member.requested_model ?? member.model,
          status:
            member.status === "success" ? "reused" : "waiting",
        })),
      );
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
      setCouncilLoading(false);
    } finally {
      setHistoryActionLoading(false);
    }
  }

  async function deleteHistoryRun() {
    if (!historyDetail || historyActionLoading) return;
    const confirmed = window.confirm(
      "Удалить этот сохранённый отчёт и ответы всех участников? Отменить удаление нельзя.",
    );
    if (!confirmed) return;

    setHistoryActionLoading(true);
    setHistoryError("");
    try {
      const response = await fetch(
        `${API_BASE}/council/runs/${historyDetail.run_id}`,
        { method: "DELETE" },
      );
      const data = (await response.json()) as {
        deleted?: boolean;
        detail?: unknown;
      };
      if (!response.ok || !data.deleted) {
        throw new Error(
          apiError(data, "Не удалось удалить сохранённый отчёт."),
        );
      }
      setHistoryDetail(null);
      setHistoryRevision((value) => value + 1);
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setHistoryActionLoading(false);
    }
  }

  async function saveRetentionPolicy() {
    if (!retentionPolicy || retentionSaving) return;
    setRetentionSaving(true);
    setRetentionMessage("");
    setHistoryError("");
    try {
      const response = await fetch(`${API_BASE}/council/retention`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          retention_days: retentionPolicy.retention_days,
          auto_delete_enabled:
            retentionPolicy.auto_delete_enabled,
          store_member_answers: retentionPolicy.store_member_answers,
          store_token_usage: retentionPolicy.store_token_usage,
        }),
      });
      const data = (await response.json()) as CouncilRetentionPolicy & {
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось сохранить настройки хранения."),
        );
      }
      setRetentionPolicy(data);
      setRetentionMessage("Настройки хранения сохранены.");
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setRetentionSaving(false);
    }
  }

  async function purgeExpiredHistory() {
    if (!retentionPolicy || retentionSaving) return;
    const confirmed = window.confirm(
      `Удалить все отчёты старше ${retentionPolicy.retention_days} дней в текущем Workspace? Отменить операцию нельзя.`,
    );
    if (!confirmed) return;

    setRetentionSaving(true);
    setRetentionMessage("");
    setHistoryError("");
    try {
      const response = await fetch(
        `${API_BASE}/council/retention/purge`,
        { method: "POST" },
      );
      const data = (await response.json()) as {
        deleted_count?: number;
        detail?: unknown;
      };
      if (!response.ok) {
        throw new Error(
          apiError(data, "Не удалось очистить старую историю."),
        );
      }
      setRetentionMessage(
        `Удалено запусков: ${data.deleted_count ?? 0}.`,
      );
      setHistoryDetail(null);
      setHistoryRevision((value) => value + 1);
    } catch (error) {
      setHistoryError(
        error instanceof Error ? error.message : String(error),
      );
    } finally {
      setRetentionSaving(false);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand-mark">C</div>
        <div>
          <div className="logo">AI Studio</div>
          <div className="version">Enterprise v0.15.0</div>
        </div>

        <nav className="nav" aria-label="Разделы">
          <button
            className={view === "chat" ? "active" : ""}
            onClick={() => setView("chat")}
          >
            <span>01</span>
            Чат
          </button>
          <button
            className={view === "council" ? "active" : ""}
            onClick={() => setView("council")}
          >
            <span>02</span>
            AI Council
          </button>
          <button
            className={view === "sandbox" ? "active" : ""}
            onClick={() => setView("sandbox")}
          >
            <span>03</span>
            Code Sandbox
          </button>
          <button
            className={view === "gateway" ? "active" : ""}
            onClick={() => setView("gateway")}
          >
            <span>04</span>
            Model Gateway
          </button>
          <button disabled>
            <span>05</span>
            Crypto AI
          </button>
          <button disabled>
            <span>06</span>
            Документы
          </button>
          <button
            className={view === "approvals" ? "active" : ""}
            onClick={() => setView("approvals")}
          >
            <span>07</span>
            Approvals
          </button>
          <button
            className={view === "settings" ? "active" : ""}
            onClick={() => setView("settings")}
          >
            <span>08</span>
            Политика
          </button>
        </nav>

        <div className="runtime">
          <span className="runtime-dot" />
          <div>
            <strong>Локальный контур</strong>
            <small>FastAPI · React · SQLite</small>
          </div>
        </div>
      </aside>

      <main className="main">
        {view === "chat" ? (
          <>
            <header className="topbar">
              <div>
                <div className="eyebrow">Рабочее пространство</div>
                <h1>Диалог с моделью</h1>
                <p>Один запрос — один выбранный режим анализа.</p>
              </div>
              <ModeSelect value={mode} onChange={setMode} />
            </header>

            <section className="chat">
              {messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`message ${message.role}`}
                >
                  <div className="role">
                    {message.role === "user"
                      ? "Вы"
                      : message.role === "assistant"
                        ? "AI"
                        : "Система"}
                  </div>
                  <div className="text">{message.text}</div>
                </div>
              ))}
              {chatLoading && (
                <div className="message assistant pending">
                  <div className="role">AI</div>
                  <div className="thinking">
                    <i />
                    <i />
                    <i />
                  </div>
                </div>
              )}
            </section>

            <footer className="composer">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="Опишите задачу или задайте вопрос…"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && event.ctrlKey) {
                    sendMessage();
                  }
                }}
              />
              <div className="composer-actions">
                <small>Ctrl + Enter</small>
                <button onClick={sendMessage} disabled={chatLoading}>
                  {chatLoading ? "Ожидание…" : "Отправить"}
                </button>
              </div>
            </footer>
          </>
        ) : view === "council" ? (
          <>
            <header className="topbar council-topbar">
              <div>
                <div className="eyebrow">Коллективный анализ</div>
                <h1>AI Council</h1>
                <p>
                  Независимые ответы моделей, критика и единый итог.
                </p>
              </div>
              <div className="council-header-actions">
                <div className="council-tabs">
                  <button
                    className={
                      councilSection === "new" ? "active" : ""
                    }
                    onClick={() => setCouncilSection("new")}
                  >
                    Новый анализ
                  </button>
                  <button
                    className={
                      councilSection === "history" ? "active" : ""
                    }
                    onClick={() => setCouncilSection("history")}
                  >
                    История
                  </button>
                </div>
                {councilSection === "new" && (
                  <ModeSelect
                    value={councilMode}
                    onChange={setCouncilMode}
                  />
                )}
              </div>
            </header>

            {councilSection === "new" ? (
              <section className="council-layout">
              <div className="council-setup">
                <div className="panel-heading">
                  <div>
                    <span className="step">Шаг 1</span>
                    <h2>Состав Совета</h2>
                  </div>
                  <span className="counter">
                    {selectedModels.length}/6
                  </span>
                </div>

                <div className="orchestration-mode">
                  <label>
                    <span>Режим оркестрации</span>
                    <select
                      value={executionMode}
                      onChange={(event) =>
                        changeExecutionMode(event.target.value as CouncilExecutionMode)
                      }
                      disabled={councilLoading}
                    >
                      {(Object.keys(EXECUTION_MODE_LABELS) as CouncilExecutionMode[]).map((item) => (
                        <option key={item} value={item}>
                          {EXECUTION_MODE_LABELS[item]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <small>{EXECUTION_MODE_HELP[executionMode]}</small>
                  <span className="orchestration-requirement">
                    {executionMode === "solo"
                      ? "Нужна 1 модель."
                      : executionMode === "review"
                        ? "Нужны ≥2 участника + независимый Reviewer."
                        : executionMode === "arbitration"
                          ? "Нужны ≥2 участника + независимые Reviewer и Arbiter."
                          : executionMode === "delegate"
                            ? "Нужны ≥2 участника; подзадачи ограничены глубиной 1."
                            : "Нужны минимум 2 независимых участника."}
                  </span>
                </div>

                {modelsError && (
                  <div className="alert error">{modelsError}</div>
                )}

                <div className="preset-panel">
                  <label>
                    <span>Сохранённый состав</span>
                    <select
                      value={selectedPresetId}
                      onChange={(event) => applyPreset(event.target.value)}
                      disabled={councilLoading || presetSaving}
                    >
                      <option value="">Новый состав</option>
                      {presets.map((preset) => (
                        <option key={preset.id} value={preset.id}>
                          {preset.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <input
                    value={presetName}
                    onChange={(event) => setPresetName(event.target.value)}
                    disabled={councilLoading || presetSaving}
                    placeholder="Название состава"
                    maxLength={120}
                  />
                  <input
                    value={presetDescription}
                    onChange={(event) =>
                      setPresetDescription(event.target.value)
                    }
                    disabled={councilLoading || presetSaving}
                    placeholder="Описание (необязательно)"
                    maxLength={1000}
                  />
                  <div className="preset-actions">
                    <button
                      onClick={saveCouncilPreset}
                      disabled={
                        presetSaving ||
                        !presetName.trim() ||
                        !councilSelectionValid
                      }
                    >
                      {presetSaving
                        ? "Сохраняю…"
                        : selectedPresetId
                          ? "Обновить"
                          : "Сохранить"}
                    </button>
                    {selectedPresetId && (
                      <button
                        className="secondary"
                        onClick={() => applyPreset("")}
                        disabled={presetSaving}
                      >
                        Новый
                      </button>
                    )}
                    {selectedPresetId && (
                      <button
                        className="danger-subtle"
                        onClick={deleteCouncilPreset}
                        disabled={presetSaving}
                      >
                        Удалить
                      </button>
                    )}
                  </div>
                </div>

                <div className="model-list">
                  {models.map((item, index) => {
                    const selected = selectedModels.includes(item.slug);
                    const selectedIndex = selectedModels.indexOf(item.slug);
                    const role =
                      memberRoles[item.slug] ??
                      COUNCIL_ROLES[
                        Math.max(selectedIndex, 0) % COUNCIL_ROLES.length
                      ];
                    return (
                      <div
                        className="model-entry"
                        key={`${item.provider}:${item.slug}`}
                      >
                        <button
                          className={`model-option ${
                            selected ? "selected" : ""
                          }`}
                          onClick={() => toggleCouncilModel(item.slug)}
                          disabled={councilLoading}
                        >
                          <span className="model-check">
                            {selected ? "✓" : index + 1}
                          </span>
                          <span className="model-copy">
                            <strong>{item.display_name}</strong>
                            <span
                              className={`billing-badge ${
                                item.metadata?.billing === "free"
                                  ? "free"
                                  : "metered"
                              }`}
                            >
                              {item.metadata?.billing === "free"
                                ? "бесплатно"
                                : "по тарифу"}
                            </span>
                            <small>
                              {item.provider} · {item.slug}
                            </small>
                            {selected && <em>{ROLE_LABELS[role]}</em>}
                          </span>
                        </button>
                        {selected && (
                          <label className="member-role-select">
                            <span>Роль</span>
                            <select
                              value={role}
                              onChange={(event) =>
                                setMemberRoles((previous) => ({
                                  ...previous,
                                  [item.slug]: event.target.value as CouncilRole,
                                }))
                              }
                              disabled={councilLoading}
                            >
                              {COUNCIL_ROLES.map((roleOption) => (
                                <option key={roleOption} value={roleOption}>
                                  {ROLE_LABELS[roleOption]}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </div>
                    );
                  })}
                  {!modelsError && models.length === 0 && (
                    <div className="empty-state">
                      Загружаю каталог моделей…
                    </div>
                  )}
                </div>

                <div className="question-block">
                  <span className="step">Шаг 2</span>
                  <label htmlFor="council-question">
                    Вопрос для Совета
                  </label>
                  <textarea
                    id="council-question"
                    value={councilQuestion}
                    onChange={(event) =>
                      setCouncilQuestion(event.target.value)
                    }
                    disabled={councilLoading}
                    placeholder="Опишите задачу, решение или сценарий, который нужно оценить с нескольких точек зрения…"
                  />
                  {executionMode !== "solo" && executionMode !== "arbitration" && (
                    <label className="member-timeout">
                      <span>Председатель / PRIMARY</span>
                      <select
                        value={chairModelInfo?.slug ?? ""}
                        onChange={(event) =>
                          setChairModelSlug(event.target.value)
                        }
                        disabled={councilLoading || models.length === 0}
                      >
                        {models.map((item) => (
                          <option key={`${item.provider}:${item.slug}`} value={item.slug}>
                            {item.display_name} · {
                              item.metadata?.billing === "free"
                                ? "бесплатно"
                                : "по тарифу"
                            }
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {(executionMode === "review" || executionMode === "arbitration") && (
                    <label className="member-timeout">
                      <span>Независимый Reviewer</span>
                      <select
                        value={reviewerModelSlug}
                        onChange={(event) => setReviewerModelSlug(event.target.value)}
                        disabled={councilLoading}
                      >
                        <option value="">Выберите Reviewer</option>
                        {models
                          .filter(
                            (item) =>
                              !selectedModels.includes(item.slug) &&
                              (executionMode !== "review" || item.slug !== chairModelInfo?.slug),
                          )
                          .map((item) => (
                            <option key={`${item.provider}:${item.slug}`} value={item.slug}>
                              {item.display_name} · {item.provider}
                            </option>
                          ))}
                      </select>
                      {!independentReviewerValid && (
                        <small className="orchestration-warning">Reviewer не должен участвовать в написании ответа или быть председателем.</small>
                      )}
                    </label>
                  )}
                  {executionMode === "arbitration" && (
                    <label className="member-timeout">
                      <span>Независимый Arbiter</span>
                      <select
                        value={arbiterModelSlug}
                        onChange={(event) => setArbiterModelSlug(event.target.value)}
                        disabled={councilLoading}
                      >
                        <option value="">Выберите Arbiter</option>
                        {models
                          .filter(
                            (item) =>
                              !selectedModels.includes(item.slug) &&
                              item.slug !== reviewerModelSlug,
                          )
                          .map((item) => (
                            <option key={`${item.provider}:${item.slug}`} value={item.slug}>
                              {item.display_name} · {item.provider}
                            </option>
                          ))}
                      </select>
                      {!independentArbiterValid && (
                        <small className="orchestration-warning">Arbiter должен быть отдельной моделью и не участвовать в Совете или review.</small>
                      )}
                    </label>
                  )}
                  {executionMode === "delegate" && (
                    <label className="member-timeout">
                      <span>Максимум субагентов</span>
                      <select
                        value={delegationMaxCalls}
                        onChange={(event) => setDelegationMaxCalls(Number(event.target.value))}
                        disabled={councilLoading}
                      >
                        {[1, 2, 3, 4, 5, 6, 7, 8].map((value) => (
                          <option key={value} value={value}>{value}</option>
                        ))}
                      </select>
                      <small>Глубина делегирования фиксирована: 1. Субагенты не могут создавать своих субагентов.</small>
                    </label>
                  )}
                  <label className="member-timeout">
                    <span>Тайм-аут одного участника</span>
                    <select
                      value={memberTimeoutSeconds}
                      onChange={(event) =>
                        setMemberTimeoutSeconds(
                          Number(event.target.value),
                        )
                      }
                      disabled={councilLoading}
                    >
                      <option value={30}>30 секунд</option>
                      <option value={60}>60 секунд</option>
                      <option value={90}>90 секунд</option>
                      <option value={120}>120 секунд</option>
                      <option value={180}>180 секунд</option>
                    </select>
                  </label>
                  <div
                    className={`cost-preflight ${
                      costEstimate?.decision ?? "idle"
                    }`}
                  >
                    <div>
                      <strong>Preflight стоимости</strong>
                      <span>
                        {costEstimate
                          ? costEstimate.estimated_cost_usd !== null
                            ? `≈ $${costEstimate.estimated_cost_usd.toFixed(4)}`
                            : costEstimate.estimate_status === "partial"
                              ? `известно ≥ $${costEstimate.known_cost_usd.toFixed(4)}`
                              : "тариф неизвестен"
                          : councilQuestion.trim() && councilSelectionValid
                            ? "рассчитываю…"
                            : "после ввода вопроса"}
                      </span>
                    </div>
                    {costEstimate && (
                      <>
                        <small>
                          За текущий месяц: ≈ $
                          {costEstimate.monthly_estimated_spend_usd.toFixed(4)}
                          {costEstimate.projected_monthly_spend_usd !== null &&
                            ` → $${costEstimate.projected_monthly_spend_usd.toFixed(4)} после запуска`}
                        </small>
                        <span className={`cost-decision ${costEstimate.decision}`}>
                          {costEstimate.decision === "allow"
                            ? "разрешено"
                            : costEstimate.decision === "approval_required"
                              ? "нужно подтверждение"
                              : "заблокировано"}
                        </span>
                        {costEstimate.unknown_models.length > 0 && (
                          <small>
                            Без известного прайса: {
                              costEstimate.unknown_models.join(", ")
                            }
                          </small>
                        )}
                      </>
                    )}
                  </div>

                  {costUsage && (
                    <div className="cost-usage-ledger">
                      <div>
                        <strong>Actual Cost Ledger · {costUsage.month}</strong>
                        <span>
                          факт ${costUsage.actual_spend_usd.toFixed(4)} · зарезервировано ${costUsage.reserved_spend_usd.toFixed(4)}
                        </span>
                      </div>
                      <small>
                        Запуски: {costUsage.completed_runs}
                        {costUsage.reserved_runs > 0 && ` + ${costUsage.reserved_runs} в резерве`} · токены: {costUsage.actual_total_tokens.toLocaleString("ru-RU")}
                        {costUsage.reserved_tokens > 0 && ` + ${costUsage.reserved_tokens.toLocaleString("ru-RU")} в резерве`}
                      </small>
                      <small>
                        Commit: ${costUsage.committed_spend_usd.toFixed(4)}
                        {costUsage.budget_utilization_percent !== null && ` · бюджет ${costUsage.budget_utilization_percent.toFixed(1)}%`}
                        {costUsage.run_utilization_percent !== null && ` · квота запусков ${costUsage.run_utilization_percent.toFixed(1)}%`}
                        {costUsage.token_utilization_percent !== null && ` · квота токенов ${costUsage.token_utilization_percent.toFixed(1)}%`}
                      </small>
                      <button
                        className="route-council"
                        onClick={optimizeCouncilComposition}
                        disabled={routingBusy || !councilSelectionValid}
                      >
                        {routingBusy ? "Оптимизирую…" : "Подобрать экономичный состав"}
                      </button>
                    </div>
                  )}

                  {costPolicy && (
                    <details className="cost-policy">
                      <summary>Лимиты и approvals Workspace</summary>
                      <div className="cost-policy-grid">
                        <label>
                          <span>Бюджет / месяц, $</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={costPolicy.monthly_budget_usd}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                monthly_budget_usd: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Мягкий лимит / запуск, $</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={costPolicy.per_run_soft_limit_usd}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                per_run_soft_limit_usd: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Жёсткий лимит / запуск, $</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={costPolicy.per_run_hard_limit_usd}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                per_run_hard_limit_usd: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Approval от суммы, $</span>
                          <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={costPolicy.approval_threshold_usd}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                approval_threshold_usd: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Запусков / месяц</span>
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={costPolicy.monthly_run_limit}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                monthly_run_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Токенов / месяц</span>
                          <input
                            type="number"
                            min="0"
                            step="1000"
                            value={costPolicy.monthly_token_limit}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                monthly_token_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Запусков / минуту</span>
                          <input
                            type="number"
                            min="0"
                            step="1"
                            value={costPolicy.per_minute_run_limit}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                per_minute_run_limit: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label>
                          <span>Routing: мин. экономия, %</span>
                          <input
                            type="number"
                            min="0"
                            max="100"
                            step="1"
                            value={costPolicy.routing_min_savings_percent}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                routing_min_savings_percent: Number(event.target.value),
                              })
                            }
                          />
                        </label>
                        <label className="wide">
                          <span>Budget-aware routing</span>
                          <select
                            value={costPolicy.routing_mode}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                routing_mode: event.target.value as CouncilBudgetPolicy["routing_mode"],
                              })
                            }
                          >
                            <option value="advisory">Рекомендовать, применять вручную</option>
                            <option value="off">Отключить</option>
                          </select>
                        </label>
                        <label className="wide">
                          <span>Если стоимость тарифицируемой модели неизвестна</span>
                          <select
                            value={costPolicy.unknown_cost_policy}
                            onChange={(event) =>
                              setCostPolicy({
                                ...costPolicy,
                                unknown_cost_policy: event.target.value as
                                  CouncilBudgetPolicy["unknown_cost_policy"],
                              })
                            }
                          >
                            <option value="require_approval">
                              Требовать подтверждение
                            </option>
                            <option value="block">Блокировать</option>
                            <option value="allow">Разрешать</option>
                          </select>
                        </label>
                      </div>
                      <button
                        className="save-cost-policy"
                        onClick={saveCostPolicy}
                        disabled={costControlLoading}
                      >
                        {costControlLoading
                          ? "Сохраняю…"
                          : "Сохранить лимиты"}
                      </button>
                      <small>0 означает, что соответствующий лимит отключён.</small>
                    </details>
                  )}

                  {costControlMessage && (
                    <small className="control-message">
                      {costControlMessage}
                    </small>
                  )}

                  <div className="run-actions">
                    <button
                      className="run-council"
                      onClick={runCouncil}
                      disabled={
                        councilLoading ||
                        !councilQuestion.trim() ||
                        !councilSelectionValid
                      }
                    >
                      {councilLoading
                        ? "Совет анализирует…"
                        : "Запустить Совет"}
                    </button>
                    {councilLoading && liveRunId && (
                      <button
                        className="cancel-council"
                        onClick={cancelLiveRun}
                      >
                        Отменить
                      </button>
                    )}
                  </div>
                  <div
                    className={`usage-notice ${
                      hasMeteredModels ? "metered" : ""
                    }`}
                  >
                    {executionMode === "delegate" ? "До" : "Обычно"} {estimatedOrchestrationCalls} API-запросов в режиме {EXECUTION_MODE_LABELS[executionMode]}.
                    {executionMode === "delegate" && ` Лимит включает до ${delegationMaxCalls} субагентов глубины 1.`}
                    {hasMeteredModels &&
                      " Выбраны модели с оплатой по тарифу провайдера."}
                  </div>
                  {!councilSelectionValid && (
                    <small className="validation-hint">
                      {selectedModelInfo.length < requiredMemberCount
                        ? executionMode === "solo"
                          ? "Выберите одну модель."
                          : "Выберите минимум две разные модели."
                        : !independentReviewerValid
                          ? "Выберите независимого Reviewer, который не входит в состав и не является председателем."
                          : !independentArbiterValid
                            ? "Выберите отдельного Arbiter, который не входит в состав и не совпадает с Reviewer."
                            : "Проверьте конфигурацию режима оркестрации."}
                    </small>
                  )}
                </div>
              </div>

              <div className="council-output">
                {!councilResult &&
                  !councilLoading &&
                  !councilError && (
                    <div className="council-placeholder">
                      <div className="orbit">
                        <span />
                        <span />
                        <span />
                        <b>AI</b>
                      </div>
                      <h2>Здесь появится решение Совета</h2>
                      <p>
                        Каждый участник ответит независимо, затем
                        председатель выделит консенсус и разногласия.
                      </p>
                    </div>
                  )}

                {councilLoading && (
                  <LiveCouncilProgress
                    stage={liveStage}
                    members={liveMembers}
                    synthesis={liveSynthesis}
                  />
                )}

                {councilError && (
                  <div className="alert error council-error">
                    <strong>Совет не завершил анализ</strong>
                    <span>{councilError}</span>
                  </div>
                )}

                {councilResult && (
                  <CouncilReport result={councilResult} />
                )}
              </div>
              </section>
            ) : (
              <section className="history-layout">
                <aside className="history-sidebar">
                  <div className="history-heading">
                    <div>
                      <span className="step">Архив решений</span>
                      <h2>Запуски Совета</h2>
                    </div>
                    <span className="counter">{historyTotal}</span>
                  </div>

                  <div className="history-filters">
                    <input
                      type="search"
                      value={historyQuery}
                      onChange={(event) => {
                        setHistoryOffset(0);
                        setHistoryQuery(event.target.value);
                      }}
                      placeholder="Поиск по вопросу"
                    />
                    <div className="history-filter-row">
                      <select
                        value={historyStatus}
                        onChange={(event) => {
                          setHistoryOffset(0);
                          setHistoryStatus(
                            event.target.value as
                              | CouncilRunStatus
                              | "",
                          );
                        }}
                        aria-label="Статус запуска"
                      >
                        <option value="">Все статусы</option>
                        <option value="completed">Завершён</option>
                        <option value="partial">Частичный</option>
                        <option value="failed">Ошибка</option>
                        <option value="cancelled">Отменён</option>
                      </select>
                      <select
                        value={historyMode}
                        onChange={(event) => {
                          setHistoryOffset(0);
                          setHistoryMode(
                            event.target.value as Mode | "",
                          );
                        }}
                        aria-label="Режим запуска"
                      >
                        <option value="">Все режимы</option>
                        {(Object.keys(MODE_LABELS) as Mode[]).map(
                          (item) => (
                            <option key={item} value={item}>
                              {MODE_LABELS[item]}
                            </option>
                          ),
                        )}
                      </select>
                    </div>
                    <div className="history-filter-row dates">
                      <label>
                        <span>С</span>
                        <input
                          type="date"
                          value={historyDateFrom}
                          onChange={(event) => {
                            setHistoryOffset(0);
                            setHistoryDateFrom(event.target.value);
                          }}
                        />
                      </label>
                      <label>
                        <span>По</span>
                        <input
                          type="date"
                          value={historyDateTo}
                          onChange={(event) => {
                            setHistoryOffset(0);
                            setHistoryDateTo(event.target.value);
                          }}
                        />
                      </label>
                    </div>
                  </div>

                  {retentionPolicy && (
                    <details className="retention-settings">
                      <summary>Хранение и конфиденциальность</summary>
                      <div className="retention-fields">
                        <label className="retention-days">
                          <span>Срок хранения, дней</span>
                          <input
                            type="number"
                            min="1"
                            max="3650"
                            value={retentionPolicy.retention_days}
                            onChange={(event) =>
                              setRetentionPolicy({
                                ...retentionPolicy,
                                retention_days: Math.min(
                                  3650,
                                  Math.max(
                                    1,
                                    Number(event.target.value) || 1,
                                  ),
                                ),
                              })
                            }
                          />
                        </label>
                        <label className="retention-check">
                          <input
                            type="checkbox"
                            checked={
                              retentionPolicy.auto_delete_enabled
                            }
                            onChange={(event) =>
                              setRetentionPolicy({
                                ...retentionPolicy,
                                auto_delete_enabled:
                                  event.target.checked,
                              })
                            }
                          />
                          Удалять просроченные отчёты автоматически
                        </label>
                        <label className="retention-check">
                          <input
                            type="checkbox"
                            checked={
                              retentionPolicy.store_member_answers
                            }
                            onChange={(event) =>
                              setRetentionPolicy({
                                ...retentionPolicy,
                                store_member_answers:
                                  event.target.checked,
                              })
                            }
                          />
                          Сохранять полные ответы участников
                        </label>
                        <label className="retention-check">
                          <input
                            type="checkbox"
                            checked={
                              retentionPolicy.store_token_usage
                            }
                            onChange={(event) =>
                              setRetentionPolicy({
                                ...retentionPolicy,
                                store_token_usage:
                                  event.target.checked,
                              })
                            }
                          />
                          Сохранять статистику токенов
                        </label>
                        <p>
                          Отключение полных ответов и токенов применяется
                          только к новым запускам.
                        </p>
                        <div className="retention-actions">
                          <button
                            onClick={saveRetentionPolicy}
                            disabled={retentionSaving}
                          >
                            {retentionSaving
                              ? "Сохранение…"
                              : "Сохранить"}
                          </button>
                          <button
                            className="purge"
                            onClick={purgeExpiredHistory}
                            disabled={retentionSaving}
                          >
                            Очистить старые
                          </button>
                        </div>
                        {retentionMessage && (
                          <small>{retentionMessage}</small>
                        )}
                      </div>
                    </details>
                  )}

                  {historyError && (
                    <div className="alert error">{historyError}</div>
                  )}

                  <div className="history-list">
                    {historyItems.map((item) => (
                      <button
                        key={item.run_id}
                        className={`history-item ${
                          historyDetail?.run_id === item.run_id
                            ? "active"
                            : ""
                        }`}
                        onClick={() => openHistoryRun(item.run_id)}
                      >
                        <span className="history-item-top">
                          <em className={`run-status ${item.status}`}>
                            {RUN_STATUS_LABELS[item.status]}
                          </em>
                          <time>
                            {formatCouncilDate(item.started_at)}
                          </time>
                        </span>
                        <strong>{item.question_preview}</strong>
                        <span className="history-item-meta">
                          {EXECUTION_MODE_LABELS[item.execution_mode]} · {MODE_LABELS[item.mode]} ·{" "}
                          {item.successful_member_count}/
                          {item.member_count} ответов
                          {item.confidence !== null
                            ? ` · ${item.confidence}%`
                            : ""}
                        </span>
                      </button>
                    ))}
                    {!historyLoading && historyItems.length === 0 && (
                      <div className="empty-state">
                        Сохранённых запусков с такими фильтрами нет.
                      </div>
                    )}
                    {historyLoading && (
                      <div className="history-loading">
                        Обновляю историю…
                      </div>
                    )}
                  </div>

                  <div className="history-pagination">
                    <button
                      disabled={historyOffset === 0 || historyLoading}
                      onClick={() =>
                        setHistoryOffset((value) =>
                          Math.max(0, value - 20),
                        )
                      }
                    >
                      Назад
                    </button>
                    <span>
                      {historyTotal === 0
                        ? "0"
                        : `${historyOffset + 1}–${Math.min(
                            historyOffset + historyItems.length,
                            historyTotal,
                          )}`}
                    </span>
                    <button
                      disabled={!historyHasMore || historyLoading}
                      onClick={() =>
                        setHistoryOffset((value) => value + 20)
                      }
                    >
                      Далее
                    </button>
                  </div>
                </aside>

                <div className="history-detail">
                  {!historyDetail && !historyLoading && (
                    <div className="council-placeholder">
                      <h2>История пока пуста</h2>
                      <p>
                        Завершённые, частичные и неуспешные запуски
                        будут сохраняться отдельно для текущего
                        Workspace.
                      </p>
                    </div>
                  )}

                  {historyDetail && (
                    <>
                      <div className="history-detail-toolbar">
                        <div>
                          <span className="step">
                            Сохранённый отчёт
                          </span>
                          <p>{historyDetail.question}</p>
                        </div>
                        <div>
                          <button
                            className="history-replay"
                            onClick={replayHistoryRun}
                            disabled={historyActionLoading}
                          >
                            {historyActionLoading
                              ? "Подождите…"
                              : "Повторить"}
                          </button>
                          {historyDetail.members.some(
                            (member) => member.status === "error",
                          ) && (
                            <button
                              className="history-retry-failed"
                              onClick={retryFailedHistoryRun}
                              disabled={historyActionLoading}
                            >
                              Повторить ошибки
                            </button>
                          )}
                          <button
                            className="history-delete"
                            onClick={deleteHistoryRun}
                            disabled={historyActionLoading}
                          >
                            Удалить
                          </button>
                        </div>
                      </div>

                      {isReportableHistory(historyDetail) ? (
                        <CouncilReport result={historyDetail} />
                      ) : (
                        <FailedCouncilReport result={historyDetail} />
                      )}
                    </>
                  )}
                </div>
              </section>
            )}
          </>
        ) : view === "gateway" ? (
          <GatewayPanel />
        ) : view === "sandbox" ? (
          <CodeSandboxPanel
            suggestedCouncilRunId={
              councilResult?.run_id ?? historyDetail?.run_id ?? ""
            }
          />
        ) : view === "approvals" ? (
          <PolicyApprovalCenter />
        ) : (
          <WorkspacePolicyPanel />
        )}
      </main>
    </div>
  );
}


type GatewayProviderInfo = {
  provider: string;
  configured: boolean;
  credential_mode: string;
  base_url: string;
  adapter: string;
  health: {
    status: string;
    circuit_open: boolean;
    total_requests: number;
    successful_requests?: number;
    failed_requests?: number;
    consecutive_failures?: number;
    reliability?: number | null;
    ema_latency_ms?: number | null;
    last_error_code?: string | null;
  };
};

type GatewayRouteCandidate = {
  provider: string;
  model: string;
  display_name: string;
  score: number;
  estimated_cost_usd: number | null;
  pricing_known: boolean;
  quality_score: number;
  reliability: number;
  latency_ms: number;
  health_status: string;
};

function GatewayPanel() {
  const [providers, setProviders] = useState<GatewayProviderInfo[]>([]);
  const [strategy, setStrategy] = useState<"balanced" | "cost" | "latency" | "reliability" | "quality">("balanced");
  const [inputTokens, setInputTokens] = useState(2000);
  const [outputTokens, setOutputTokens] = useState(1000);
  const [candidates, setCandidates] = useState<GatewayRouteCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState("");

  async function loadProviders() {
    const response = await fetch(`${API_BASE}/gateway/providers`);
    const data = (await response.json()) as { providers?: GatewayProviderInfo[]; detail?: unknown };
    if (!response.ok) throw new Error(apiError(data, "Не удалось загрузить провайдеры."));
    setProviders(data.providers ?? []);
  }

  async function routeModels() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${API_BASE}/gateway/route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          strategy,
          expected_input_tokens: inputTokens,
          expected_output_tokens: outputTokens,
          limit: 8,
        }),
      });
      const data = (await response.json()) as { candidates?: GatewayRouteCandidate[]; detail?: unknown };
      if (!response.ok) throw new Error(apiError(data, "Router не смог подобрать модели."));
      setCandidates(data.candidates ?? []);
      await loadProviders();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function testGateway() {
    setBusy(true); setError(""); setTestResult("");
    try {
      const response = await fetch(`${API_BASE}/gateway/test`, { method: "POST" });
      const data = (await response.json()) as {
        status?: string; provider?: string; model?: string; latency_ms?: number | null;
        failover_used?: boolean; content?: string; error?: { message?: string };
        detail?: unknown;
      };
      if (!response.ok) throw new Error(apiError(data, "Gateway test failed."));
      setTestResult(data.status === "success"
        ? `${data.provider} · ${data.model} · ${Math.round(data.latency_ms ?? 0)} мс${data.failover_used ? " · failover" : ""}`
        : data.error?.message ?? "Gateway вернул ошибку.");
      await loadProviders();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  useEffect(() => {
    void loadProviders().catch((e) => setError(e instanceof Error ? e.message : String(e)));
    void routeModels();
  }, []);

  return <>
    <header className="topbar">
      <div><div className="eyebrow">P2-009 · Multi-Provider Model Gateway</div><h1>Model Gateway</h1><p>Health-aware routing, стоимость, latency, circuit breaker и явный failover.</p></div>
      <button onClick={testGateway} disabled={busy}>Проверить default route</button>
    </header>
    <section className="gateway-layout">
      <div className="gateway-provider-grid">
        {providers.map((item) => <article key={item.provider} className={`gateway-provider ${item.health.status}`}>
          <div className="gateway-provider-head"><div><span className="step">{item.adapter}</span><h2>{item.provider}</h2></div><span className={`gateway-health ${item.health.status}`}>{item.configured ? item.health.status : "not configured"}</span></div>
          <code>{item.base_url}</code>
          <div className="sandbox-metrics">
            <span><strong>{item.health.total_requests}</strong> запросов</span>
            <span><strong>{item.health.reliability == null ? "—" : `${(item.health.reliability * 100).toFixed(1)}%`}</strong> success</span>
            <span><strong>{item.health.ema_latency_ms == null ? "—" : `${Math.round(item.health.ema_latency_ms)} мс`}</strong> latency</span>
          </div>
          <small>Credentials: {item.credential_mode}{item.health.last_error_code ? ` · ${item.health.last_error_code}` : ""}</small>
        </article>)}
      </div>

      <div className="sandbox-panel gateway-router-panel">
        <div className="panel-heading"><div><span className="step">Intelligent Router</span><h2>Подбор маршрута</h2></div></div>
        <div className="agent-grid">
          <label className="sandbox-field"><span>Стратегия</span><select value={strategy} onChange={(e) => setStrategy(e.target.value as typeof strategy)}><option value="balanced">Balanced</option><option value="cost">Cost</option><option value="latency">Latency</option><option value="reliability">Reliability</option><option value="quality">Quality</option></select></label>
          <label className="sandbox-field"><span>Input tokens</span><input type="number" min="0" value={inputTokens} onChange={(e) => setInputTokens(Math.max(0, Number(e.target.value) || 0))} /></label>
          <label className="sandbox-field"><span>Output tokens</span><input type="number" min="0" value={outputTokens} onChange={(e) => setOutputTokens(Math.max(0, Number(e.target.value) || 0))} /></label>
        </div>
        <button onClick={routeModels} disabled={busy}>{busy ? "Расчёт…" : "Пересчитать маршрут"}</button>
        {testResult && <div className="alert success">{testResult}</div>}
        {error && <div className="alert error">{error}</div>}
        <div className="gateway-route-list">
          {candidates.map((item, index) => <div key={`${item.provider}:${item.model}`} className="gateway-route-item">
            <span className="gateway-rank">{index + 1}</span>
            <div><strong>{item.display_name}</strong><small>{item.provider} · {item.model}</small></div>
            <span>score {item.score.toFixed(3)}</span>
            <span>{item.estimated_cost_usd == null ? "цена unknown" : `≈ $${item.estimated_cost_usd.toFixed(5)}`}</span>
            <span>{Math.round(item.latency_ms)} мс · {(item.reliability * 100).toFixed(1)}%</span>
          </div>)}
          {!busy && candidates.length === 0 && <small>Нет доступных моделей, удовлетворяющих текущей конфигурации.</small>}
        </div>
      </div>
    </section>
  </>;
}


function CodeSandboxPanel({ suggestedCouncilRunId = "" }: { suggestedCouncilRunId?: string }) {
  const [repoPath, setRepoPath] = useState("C:\\Projects\\ai-council-enterprise");
  const [baseRef, setBaseRef] = useState("HEAD");
  const [session, setSession] = useState<CodeSandboxSession | null>(null);
  const [patchPreview, setPatchPreview] = useState("");
  const [approvalToken, setApprovalToken] = useState("");
  const [profiles, setProfiles] = useState<Array<CodeSandboxVerificationItem["profile"]>>(["diff_check"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [agentTask, setAgentTask] = useState("");
  const [agentCouncilRunId, setAgentCouncilRunId] = useState(suggestedCouncilRunId);
  const [agentProvider, setAgentProvider] = useState("openrouter");
  const [agentModel, setAgentModel] = useState("");
  const [agentContextPaths, setAgentContextPaths] = useState("");
  const [agentWritablePaths, setAgentWritablePaths] = useState("");
  const [agentResult, setAgentResult] = useState<CodeAgentRun | null>(null);
  const [verificationBackend, setVerificationBackend] = useState<"docker" | "host">("docker");
  const [runtimeStatus, setRuntimeStatus] = useState<IsolatedRuntimeStatus | null>(null);
  const [runtimeRuns, setRuntimeRuns] = useState<IsolatedRuntimeRun[]>([]);

  async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
    const response = await fetch(url, init);
    const data = (await response.json()) as T & { detail?: unknown };
    if (!response.ok) throw new Error(apiError(data, "Code Sandbox operation failed."));
    return data;
  }

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/code-sandbox/runtime/status`)
      .then(async (response) => {
        const data = (await response.json()) as IsolatedRuntimeStatus;
        if (!cancelled && response.ok) {
          setRuntimeStatus(data);
          setVerificationBackend(data.enabled ? "docker" : "host");
        }
      })
      .catch(() => {
        if (!cancelled) setVerificationBackend("host");
      });
    return () => { cancelled = true; };
  }, []);

  async function createSandbox() {
    if (!repoPath.trim() || busy) return;
    setBusy(true); setError(""); setMessage(""); setPatchPreview(""); setApprovalToken(""); setRuntimeRuns([]);
    try {
      const data = await requestJson<CodeSandboxSession>(`${API_BASE}/code-sandbox/sessions`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo_path: repoPath.trim(), base_ref: baseRef.trim() || "HEAD" }),
      });
      setSession(data);
      setMessage("Изолированный Git worktree создан. Изменения в нём не затрагивают основной репозиторий.");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  function parsePathLines(value: string): string[] {
    return Array.from(new Set(value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean)));
  }

  async function runCodeAgent() {
    if (!session || busy) return;
    const writablePaths = parsePathLines(agentWritablePaths);
    if (writablePaths.length === 0) {
      setError("Укажите хотя бы один writable path. Агент не получает произвольный доступ к репозиторию.");
      return;
    }
    if (!agentTask.trim() && !agentCouncilRunId.trim()) {
      setError("Укажите техническое задание или Council run ID.");
      return;
    }
    setBusy(true); setError(""); setMessage(""); setApprovalToken(""); setAgentResult(null);
    try {
      const data = await requestJson<CodeAgentRunResponse>(`${API_BASE}/code-sandbox/sessions/${session.id}/agent-runs`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task: agentTask.trim(),
          council_run_id: agentCouncilRunId.trim() || null,
          provider: agentProvider.trim() || null,
          model: agentModel.trim() || null,
          context_paths: parsePathLines(agentContextPaths),
          writable_paths: writablePaths,
          verification_profiles: (() => {
            const safeProfiles = profiles.filter((profile) => profile === "diff_check" || profile === "python_compile");
            return safeProfiles.length > 0 ? safeProfiles : ["diff_check"];
          })(),
          verification_timeout_seconds: 180,
          auto_verify: true,
        }),
      });
      setAgentResult(data.run);
      setSession(data.sandbox);
      if (data.inspect) setPatchPreview(data.inspect.patch_preview);
      setMessage(data.run.status === "completed"
        ? "Code Agent подготовил изменения только в worktree. Patch проверен; основной репозиторий не изменён."
        : data.run.error_message || `Code Agent завершился со статусом ${data.run.status}.`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function inspectPatch() {
    if (!session || busy) return;
    setBusy(true); setError(""); setMessage(""); setApprovalToken("");
    try {
      const data = await requestJson<CodeSandboxInspect>(`${API_BASE}/code-sandbox/sessions/${session.id}/inspect`, { method: "POST" });
      setSession(data.session); setPatchPreview(data.patch_preview);
      setMessage(data.session.files_changed ? "Patch fingerprint обновлён. Проверьте риск и diff перед verification." : "Изменений в worktree пока нет.");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function verifyPatch() {
    if (!session || busy) return;
    setBusy(true); setError(""); setMessage(""); setApprovalToken(""); setRuntimeRuns([]);
    try {
      if (verificationBackend === "docker") {
        if (!runtimeStatus?.enabled) throw new Error("Docker isolated runtime недоступен. Запустите Docker Desktop и prepare_p2_010_runtime.bat либо выберите Host (trusted).");
        const data = await requestJson<IsolatedRuntimeRunResponse>(`${API_BASE}/code-sandbox/sessions/${session.id}/runtime-runs`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profiles, timeout_seconds: 300, cpu_limit: 1.0, memory_mb: 1024, pids_limit: 128, collect_artifacts: true }),
        });
        setSession(data.session); setRuntimeRuns(data.runs);
        setMessage(data.session.verification_status === "passed" ? "Isolated verification пройдена внутри Docker runtime." : "Isolated verification завершилась ошибкой.");
      } else {
        const data = await requestJson<CodeSandboxSession>(`${API_BASE}/code-sandbox/sessions/${session.id}/verify`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ profiles, timeout_seconds: 180 }),
        });
        setSession(data); setMessage(data.verification_status === "passed" ? "Host verification пройдена." : "Одна или несколько host-проверок завершились ошибкой.");
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function approvePatch() {
    if (!session || busy) return;
    const confirmed = window.confirm(`Подтвердить применение патча риска ${session.risk_level.toUpperCase()}? Approval одноразовый и привязан к текущему fingerprint.`);
    if (!confirmed) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await requestJson<CodeSandboxApproval>(`${API_BASE}/code-sandbox/sessions/${session.id}/approval`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Approved in Code Sandbox UI" }),
      });
      setApprovalToken(data.token); setMessage(`Human Approval создан до ${formatCouncilDate(data.expires_at)}.`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function applyPatch() {
    if (!session || busy) return;
    const confirmed = window.confirm("Применить проверенный patch к основному рабочему дереву? Git commit автоматически создаваться не будет.");
    if (!confirmed) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await requestJson<CodeSandboxSession>(`${API_BASE}/code-sandbox/sessions/${session.id}/apply`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_token: approvalToken || null }),
      });
      setSession(data); setMessage("Patch применён к основному рабочему дереву. Изменения оставлены незакоммиченными для финального просмотра человеком.");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function closeSandbox() {
    if (!session || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const data = await requestJson<CodeSandboxSession>(`${API_BASE}/code-sandbox/sessions/${session.id}`, { method: "DELETE" });
      setSession(data); setMessage("Worktree удалён. Основной репозиторий не изменялся этой операцией.");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  function toggleProfile(profile: CodeSandboxVerificationItem["profile"]) {
    setProfiles((current) => current.includes(profile) ? (current.length === 1 ? current : current.filter((item) => item !== profile)) : [...current, profile]);
  }

  const terminal = session?.status === "applied" || session?.status === "closed";
  return (
    <>
      <header className="topbar sandbox-topbar">
        <div><div className="eyebrow">P2-010 · Docker Isolated Runtime</div><h1>Code Sandbox</h1><p>Council/task → Code Agent → worktree → isolated verification → Human Approval → safe apply.</p></div>
      </header>
      <section className="sandbox-layout">
        <div className="sandbox-panel">
          <div className="panel-heading"><div><span className="step">Шаг 1</span><h2>Изоляция репозитория</h2></div></div>
          <label className="sandbox-field"><span>Путь к Git-репозиторию</span><input value={repoPath} onChange={(e) => setRepoPath(e.target.value)} disabled={busy || Boolean(session && !terminal)} /></label>
          <label className="sandbox-field"><span>Base ref</span><input value={baseRef} onChange={(e) => setBaseRef(e.target.value)} disabled={busy || Boolean(session && !terminal)} /></label>
          <button onClick={createSandbox} disabled={busy || Boolean(session && !terminal)}>Создать worktree</button>
          {session && <div className="sandbox-paths"><span>Base: <code>{session.base_commit.slice(0, 12)}</code></span><span>Worktree: <code>{session.worktree_path}</code></span></div>}
          <div className="sandbox-warning"><strong>Граница изоляции</strong><span>P2-010 может выполнять pytest/build внутри Docker без сети, с read-only rootfs, read-only исходным worktree и лимитами CPU/RAM/PID. Host mode оставлен только для явно доверенного кода.</span></div>
        </div>

        {session && !terminal && <div className="sandbox-panel agent-panel">
          <div className="panel-heading"><div><span className="step">Шаг 2</span><h2>Ограниченный Code Agent</h2></div></div>
          <div className="sandbox-warning"><strong>Capability boundary</strong><span>У агента нет shell, сети, секретов и произвольного доступа к файлам. Он может изменять только точные writable paths, перечисленные ниже. Apply в основной репозиторий никогда не выполняется автоматически.</span></div>
          <label className="sandbox-field"><span>Задача</span><textarea rows={5} value={agentTask} onChange={(e) => setAgentTask(e.target.value)} placeholder="Например: исправь обработку timeout, сохрани обратную совместимость и не меняй API." disabled={busy} /></label>
          <label className="sandbox-field"><span>Council run ID · необязательно</span><input value={agentCouncilRunId} onChange={(e) => setAgentCouncilRunId(e.target.value)} placeholder="council_... — финальное решение будет добавлено к заданию" disabled={busy} /></label>
          <div className="agent-grid">
            <label className="sandbox-field"><span>Provider</span><input value={agentProvider} onChange={(e) => setAgentProvider(e.target.value)} placeholder="openrouter" disabled={busy} /></label>
            <label className="sandbox-field"><span>Model · пусто = default</span><input value={agentModel} onChange={(e) => setAgentModel(e.target.value)} placeholder="model slug" disabled={busy} /></label>
          </div>
          <label className="sandbox-field"><span>Context paths · по одному на строку</span><textarea rows={4} value={agentContextPaths} onChange={(e) => setAgentContextPaths(e.target.value)} placeholder={'backend/module.py\ntests/test_module.py'} disabled={busy} /></label>
          <label className="sandbox-field"><span>Writable paths · точный allowlist</span><textarea rows={4} value={agentWritablePaths} onChange={(e) => setAgentWritablePaths(e.target.value)} placeholder={'backend/module.py\ntests/test_module.py'} disabled={busy} /></label>
          <button onClick={runCodeAgent} disabled={busy}>Запустить Code Agent</button>
          {agentResult && <div className={`agent-result ${agentResult.status}`}>
            <div><strong>{agentResult.summary || agentResult.status}</strong><span>{agentResult.provider} · {agentResult.model}</span></div>
            <div className="sandbox-metrics"><span><strong>{agentResult.operations.length}</strong> операций</span><span><strong>{agentResult.total_tokens ?? "—"}</strong> токенов</span><span><strong>{agentResult.actual_cost_usd == null ? "unknown" : `$${agentResult.actual_cost_usd.toFixed(6)}`}</strong> стоимость</span></div>
            {agentResult.operations.map((operation) => <div key={`${operation.action}:${operation.path}`} className="agent-operation"><code>{operation.action}</code><strong>{operation.path}</strong><span>{operation.bytes} B</span><small>{operation.reason}</small></div>)}
            {agentResult.error_message && <div className="alert warning">{agentResult.error_message}</div>}
          </div>}
        </div>}

        {session && !terminal && <div className="sandbox-panel">
          <div className="panel-heading"><div><span className="step">Шаг 3</span><h2>Patch и риск</h2></div><span className={`risk-badge ${session.risk_level}`}>{session.risk_level}</span></div>
          <button onClick={inspectPatch} disabled={busy}>Inspect patch</button>
          <div className="sandbox-metrics"><span><strong>{session.files_changed}</strong> файлов</span><span><strong>+{session.insertions}</strong> строк</span><span><strong>-{session.deletions}</strong> строк</span><span><strong>{session.verification_status}</strong> verification</span></div>
          {session.protected_paths.length > 0 && <div className="alert warning">Protected: {session.protected_paths.join(", ")}</div>}
          {session.blocked_paths.length > 0 && <div className="alert error">BLOCKED: {session.blocked_paths.join(", ")}</div>}
          {session.patch_fingerprint && <small className="fingerprint">Fingerprint: {session.patch_fingerprint}</small>}
          {patchPreview && <pre className="patch-preview">{patchPreview}</pre>}
        </div>}

        {session && !terminal && session.patch_fingerprint && <div className="sandbox-panel">
          <div className="panel-heading"><div><span className="step">Шаг 4</span><h2>Verification & Apply</h2></div></div>
          <div className="runtime-control">
            <label className="sandbox-field"><span>Execution backend</span><select value={verificationBackend} onChange={(event) => setVerificationBackend(event.target.value as "docker" | "host")}><option value="docker" disabled={!runtimeStatus?.enabled}>Docker isolated</option><option value="host">Host (trusted)</option></select></label>
            <div className={`runtime-status ${runtimeStatus?.enabled ? "ready" : "offline"}`}>
              <strong>{runtimeStatus?.enabled ? "Docker runtime READY" : "Docker runtime unavailable"}</strong>
              <span>{runtimeStatus?.enabled ? "network=none · read-only source · cap-drop ALL · no-new-privileges" : runtimeStatus?.daemon_error || "Запустите Docker Desktop и подготовьте runtime images."}</span>
            </div>
          </div>
          {runtimeStatus && <div className="runtime-images">{runtimeStatus.images.map((image) => <span key={`${image.profile}:${image.image}`} className={image.present && image.trusted ? "present" : "missing"}><strong>{image.profile}</strong> {image.image} · {!image.present ? "missing" : image.trusted ? "trusted" : "untrusted"}</span>)}</div>}
          <div className="verification-profiles">
            {(["diff_check", "python_compile", "pytest", "frontend_build"] as CodeSandboxVerificationItem["profile"][]).map((profile) => <label key={profile}><input type="checkbox" checked={profiles.includes(profile)} onChange={() => toggleProfile(profile)} /> {profile}</label>)}
          </div>
          <div className="sandbox-actions"><button onClick={verifyPatch} disabled={busy || session.blocked_paths.length > 0}>Запустить verification</button>{session.approval_required && <button className="secondary" onClick={approvePatch} disabled={busy || session.verification_status !== "passed"}>Human Approval</button>}<button onClick={applyPatch} disabled={busy || session.verification_status !== "passed" || session.blocked_paths.length > 0 || (session.approval_required && !approvalToken)}>Применить patch</button><button className="danger-subtle" onClick={closeSandbox} disabled={busy}>Закрыть sandbox</button></div>
          {session.verification.map((item) => <details key={`${item.profile}:${item.runtime_run_id ?? "host"}`} className={`verification-item ${item.status}`}><summary><strong>{item.profile}</strong><span>{item.status} · {item.execution_backend} · {(item.duration_ms / 1000).toFixed(1)} сек.</span></summary>{item.image && <small className="runtime-image-id">Image: {item.image}</small>}<pre>{item.output || "Нет вывода."}</pre></details>)}
          {runtimeRuns.length > 0 && <div className="runtime-run-list">{runtimeRuns.map((run) => <div key={run.id} className={`runtime-run ${run.status}`}><div><strong>{run.profile}</strong><span>{run.status} · {run.memory_mb} MB · {run.cpu_limit} CPU · PID {run.pids_limit}</span></div><code>{run.image}</code>{run.artifact_available && <a href={`${API_BASE}/code-sandbox/runtime-runs/${run.id}/artifacts`} target="_blank" rel="noreferrer">Скачать artifacts ({run.artifact_bytes} B)</a>}</div>)}</div>}
        </div>}

        {session && terminal && <div className="sandbox-panel"><div className="panel-heading"><div><span className="step">Готово</span><h2>{session.status === "applied" ? "Patch применён" : "Sandbox закрыт"}</h2></div></div>{session.status === "applied" && <button className="secondary" onClick={closeSandbox} disabled={busy}>Удалить worktree</button>}</div>}
        {error && <div className="alert error">{error}</div>}
        {message && <div className="alert success">{message}</div>}
      </section>
    </>
  );
}

function LiveCouncilProgress({
  stage,
  members,
  synthesis,
}: {
  stage: string;
  members: LiveMemberProgress[];
  synthesis: string;
}) {
  const statusLabels: Record<LiveMemberProgress["status"], string> = {
    waiting: "ожидает",
    running: "анализирует",
    success: "готово",
    error: "ошибка",
    reused: "сохранённый ответ",
  };

  return (
    <div className="live-progress">
      <div className="live-progress-heading">
        <div className="scan-ring" />
        <div>
          <span className="step">Live Council</span>
          <h2>Идёт параллельный анализ</h2>
          <p>{stage || "Подключаю поток событий…"}</p>
        </div>
      </div>

      <div className="live-member-list">
        {members.map((member) => (
          <div
            key={`${member.index}:${member.model}`}
            className={`live-member ${member.status}`}
          >
            <span>{member.index + 1}</span>
            <div>
              <strong>{member.label}</strong>
              <small>{member.model}</small>
            </div>
            <em>
              {member.error_code
                ? ERROR_LABELS[member.error_code] ??
                  member.error_code
                : statusLabels[member.status]}
            </em>
          </div>
        ))}
      </div>

      {synthesis && (
        <div className="live-synthesis">
          <span>Поток председателя</span>
          <pre>{synthesis}</pre>
        </div>
      )}
    </div>
  );
}

function ModeSelect({
  value,
  onChange,
}: {
  value: Mode;
  onChange: (mode: Mode) => void;
}) {
  return (
    <label className="mode-select">
      <span>Режим</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as Mode)}
      >
        {(Object.keys(MODE_LABELS) as Mode[]).map((item) => (
          <option key={item} value={item}>
            {MODE_LABELS[item]}
          </option>
        ))}
      </select>
    </label>
  );
}

function OrchestrationSummary({ trace }: { trace: CouncilOrchestrationTrace }) {
  const stageLabel: Record<CouncilAuxiliaryResult["stage"], string> = {
    draft: "Draft",
    reviewer: "Reviewer",
    planner: "Planner",
    delegate: "Delegate",
  };
  return (
    <details className="orchestration-trace" open={trace.auxiliary_calls.length > 0}>
      <summary>
        <strong>{EXECUTION_MODE_LABELS[trace.execution_mode]}</strong>
        <span>финализатор: {trace.finalizer_stage}</span>
        {trace.delegation_calls > 0 && (
          <span>субагентов: {trace.delegation_calls} · глубина {trace.delegation_depth}</span>
        )}
      </summary>
      {trace.auxiliary_calls.length > 0 ? (
        <div className="orchestration-trace-list">
          {trace.auxiliary_calls.map((call, index) => (
            <div key={`${call.stage}:${call.ordinal ?? index}:${call.model}`} className={`orchestration-trace-item ${call.status}`}>
              <span>{stageLabel[call.stage]}{call.ordinal !== null ? ` ${call.ordinal + 1}` : ""}</span>
              <strong>{call.label}</strong>
              <small>{call.provider} · {call.model}</small>
              <em>{call.status === "success" ? "готово" : call.error_code ?? "ошибка"}</em>
            </div>
          ))}
        </div>
      ) : (
        <small>Дополнительные стадии не требовались.</small>
      )}
    </details>
  );
}

function CouncilReport({ result }: { result: CouncilResult }) {
  const { synthesis } = result;
  const recoveredFallbackMembers = result.members.filter(
    (member) => member.fallback_used && member.status === "success",
  );

  return (
    <div className="council-report">
      <div className="report-header">
        <div>
          <div className="eyebrow">Итог Совета</div>
          <h2>
            {result.status === "completed"
              ? "Анализ завершён"
              : "Частичный результат"}
          </h2>
        </div>
        {synthesis.confidence !== null && (
          <div className="confidence">
            <strong>{synthesis.confidence}</strong>
            <span>уверенность</span>
          </div>
        )}
      </div>

      <OrchestrationSummary trace={result.orchestration} />

      {result.history_saved && (
        <div className="report-notice saved">
          <strong>Отчёт сохранён</strong>
          <span>
            Полный запуск доступен во вкладке «История» текущего
            Workspace.
          </span>
        </div>
      )}

      {recoveredFallbackMembers.length > 0 && (
        <div className="report-notice fallback">
          <strong>Применён бесплатный резервный маршрут</strong>
          <span>
            Резервный маршрут openrouter/free восстановил ответы участников
            после временной ошибки исходной модели.
          </span>
        </div>
      )}

      {synthesis.fallback_used && synthesis.status !== "fallback" && (
        <div className="report-notice fallback">
          <strong>Синтез восстановлен</strong>
          <span>
            Итог подготовлен через openrouter/free после временной ошибки
            выбранной бесплатной модели.
          </span>
        </div>
      )}

      {synthesis.status === "fallback" && (
        <div className="report-notice warning">
          <strong>Синтезатор не ответил</strong>
          <span>
            Сохранён первый успешный ответ участника; остальные позиции
            доступны ниже.
          </span>
        </div>
      )}

      <div className="final-answer">{synthesis.final_answer}</div>

      <div className="insight-grid">
        <InsightList
          title="Консенсус"
          items={synthesis.consensus}
          tone="positive"
        />
        <InsightList
          title="Разногласия"
          items={synthesis.disagreements}
          tone="warning"
        />
        <InsightList
          title="Рекомендации"
          items={synthesis.recommendations}
          tone="action"
        />
      </div>

      <div className="member-results">
        <h3>Ответы участников</h3>
        {result.members.map((member, index) => (
          <details
            key={`${member.provider}:${
              member.requested_model ?? member.model
            }:${index}`}
            className={`member-card ${member.status}`}
          >
            <summary>
              <span>
                <strong>{member.label}</strong>
                <small>
                  {member.provider} ·{" "}
                  {member.fallback_used
                    ? `${member.requested_model ?? member.model} → ${
                        member.fallback_model ?? member.model
                      }`
                    : member.model}
                </small>
              </span>
              <em>
                {member.status === "success"
                  ? `${
                      member.fallback_used ? "резерв · " : ""
                    }${
                      member.latency_ms !== null
                        ? `${Math.round(member.latency_ms)} мс`
                        : "готово"
                    }`
                  : `ошибка · ${
                      member.error_code
                        ? ERROR_LABELS[member.error_code] ??
                          member.error_code
                        : "нет ответа"
                    }`}
              </em>
            </summary>
            <div className="member-answer">
              {member.status === "error" && member.error_code && (
                <span className="error-code">{member.error_code}</span>
              )}
              {member.status === "success"
                ? member.answer
                : member.error_message ?? "Модель не ответила."}
            </div>
          </details>
        ))}
      </div>

      <div className="report-meta">
        <span>{EXECUTION_MODE_LABELS[result.execution_mode]}</span>
        <span>{result.run_id}</span>
        <span>{(result.duration_ms / 1000).toFixed(1)} сек.</span>
        {result.actual_cost_status && (
          <span>
            Факт: {result.actual_cost_usd !== null && result.actual_cost_usd !== undefined
              ? `$${result.actual_cost_usd.toFixed(4)}`
              : result.actual_cost_status}
            {result.actual_total_tokens !== null && result.actual_total_tokens !== undefined
              ? ` · ${result.actual_total_tokens.toLocaleString("ru-RU")} токенов`
              : ""}
          </span>
        )}
        {result.cost_estimate_status && (
          <span>
            Preflight: {
              result.estimated_cost_usd !== null &&
              result.estimated_cost_usd !== undefined
                ? `≈ $${result.estimated_cost_usd.toFixed(4)}`
                : result.cost_estimate_status === "partial"
                  ? "частичная стоимость"
                  : "стоимость неизвестна"
            }
            {result.cost_approval_id ? " · approved" : ""}
          </span>
        )}
        <span>
          Синтез: {synthesis.provider} ·{" "}
          {synthesis.fallback_used
            ? `${synthesis.requested_model ?? synthesis.model} → ${
                synthesis.fallback_model ?? synthesis.model
              }`
            : synthesis.model}
        </span>
      </div>
    </div>
  );
}

function FailedCouncilReport({
  result,
}: {
  result: CouncilHistoryDetail;
}) {
  const cancelled = result.status === "cancelled";
  return (
    <div className="council-report failed-report">
      <div className="report-header">
        <div>
          <div className="eyebrow">Итог Совета</div>
          <h2>
            {cancelled
              ? "Запуск отменён"
              : "Запуск завершился с ошибкой"}
          </h2>
        </div>
        <em
          className={`run-status ${
            cancelled ? "cancelled" : "failed"
          }`}
        >
          {cancelled ? "Отменён" : "Ошибка"}
        </em>
      </div>

      <div className="report-notice error">
        <strong>
          {result.error_code ??
            (cancelled
              ? "COUNCIL_CANCELLED"
              : "COUNCIL_ALL_MEMBERS_FAILED")}
        </strong>
        <span>
          {result.error_message ??
            (cancelled
              ? "Запуск остановлен пользователем."
              : "Ни один участник Совета не вернул успешный ответ.")}
        </span>
      </div>

      <div className="member-results">
        <h3>Ошибки участников</h3>
        {result.members.map((member, index) => (
          <details
            key={`${member.provider}:${
              member.requested_model ?? member.model
            }:${index}`}
            className="member-card error"
          >
            <summary>
              <span>
                <strong>{member.label}</strong>
                <small>
                  {member.provider} ·{" "}
                  {member.requested_model ?? member.model}
                </small>
              </span>
              <em>
                {member.error_code
                  ? ERROR_LABELS[member.error_code] ??
                    member.error_code
                  : "нет ответа"}
              </em>
            </summary>
            <div className="member-answer">
              {member.error_code && (
                <span className="error-code">{member.error_code}</span>
              )}
              {member.error_message ?? "Модель не ответила."}
            </div>
          </details>
        ))}
      </div>

      <div className="report-meta">
        <span>{result.run_id}</span>
        <span>{(result.duration_ms / 1000).toFixed(1)} сек.</span>
        <span>{formatCouncilDate(result.started_at)}</span>
      </div>
    </div>
  );
}

function isReportableHistory(
  result: CouncilHistoryDetail,
): result is CouncilHistoryDetail & CouncilResult {
  return (
    (result.status === "completed" ||
      result.status === "partial") &&
    result.synthesis !== null
  );
}

function formatCouncilDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function InsightList({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "positive" | "warning" | "action";
}) {
  if (items.length === 0) return null;

  return (
    <section className={`insight ${tone}`}>
      <h3>{title}</h3>
      <ul>
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
