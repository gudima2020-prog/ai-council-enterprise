import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

const API_BASE = "http://127.0.0.1:8000/api";
const MAX_SELECTED_SOURCES = 128;

type WorkspaceInfo = {
  id: string;
  name: string;
  description?: string;
  workspace_type?: string;
  status: string;
};

type ModelInfo = {
  provider: string;
  slug: string;
  display_name: string;
  enabled: boolean;
  supports_json?: boolean;
  context_window?: number | null;
  max_output_tokens?: number | null;
  metadata?: {
    catalog_hidden?: boolean;
  };
};

type DocumentRecord = {
  id: string;
  workspace_id: string;
  original_filename: string;
  safe_filename: string;
  document_format: "pdf" | "docx" | "xlsx" | "txt" | string;
  declared_mime_type: string;
  detected_mime_type: string;
  content_encoding: string | null;
  size_bytes: number;
  content_sha256: string;
  intake_fingerprint: string;
  classification: string;
  storage_state: string;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

type ExtractionRun = {
  id: string;
  document_id: string;
  workspace_id: string;
  status: string;
  parser: string;
  parser_version: string;
  source_sha256: string;
  extracted_text_sha256: string | null;
  total_characters: number;
  unit_count: number;
  chunk_count: number;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

type ExtractionUnit = {
  id: string;
  run_id: string;
  document_id: string;
  workspace_id: string;
  ordinal: number;
  kind: string;
  text: string;
  text_sha256: string;
  character_count: number;
  provenance: Record<string, unknown>;
};

type ExtractionChunk = {
  id: string;
  run_id: string;
  document_id: string;
  workspace_id: string;
  ordinal: number;
  text: string;
  text_sha256: string;
  character_count: number;
  character_start: number;
  character_end: number;
  source_unit_ordinals: number[];
  provenance: Record<string, unknown>;
};

type OCRRun = {
  id: string;
  document_id: string;
  workspace_id: string;
  status: string;
  ocr_version: string;
  source_sha256: string;
  classification: string;
  retention_days: number;
  retention_expires_at: string;
  text_state: string;
  page_count: number;
  selected_page_count: number;
  blank_page_count: number;
  blank_page_numbers: number[];
  total_characters: number;
  engine: string | null;
  engine_version: string | null;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

type OCRPage = {
  id: string;
  run_id: string;
  document_id: string;
  workspace_id: string;
  page_number: number;
  text?: string;
  text_sha256: string;
  character_count: number;
  classification: string;
  retention_expires_at: string;
  warnings: string[];
};

type ExtractionSourceList<T extends ExtractionUnit | ExtractionChunk> = {
  workspace_id: string;
  document_id: string;
  run_id: string;
  items?: T[];
};

type OCRPageList = {
  workspace_id: string;
  document_id: string;
  run_id: string;
  include_text: boolean;
  items?: OCRPage[];
};

type SourceKind = "extraction_chunk" | "extraction_unit" | "ocr_page";

type SelectedSource = {
  key: string;
  document_id: string;
  document_name: string;
  run_id: string;
  source_kind: SourceKind;
  source_id: string;
  label: string;
  text: string;
  text_sha256: string;
  character_count: number;
  provenance: Record<string, unknown>;
};

type ContextBudget = {
  estimator_version: string;
  context_window_tokens: number;
  document_context_tokens: number;
  reserved_output_tokens: number;
  planned_total_tokens: number;
  remaining_tokens: number;
  risk: string;
};

type CitationLocation = Record<string, string | number> & {
  kind?: string;
};

type Citation = {
  citation_id: string;
  document_id: string;
  run_id: string;
  source_kind: SourceKind;
  source_id: string;
  classification: string;
  fragment_text_sha256: string;
  locations: CitationLocation[];
  injection_finding_codes: string[];
};

type ContextManifest = {
  schema_version: string;
  workspace_id: string;
  selection_fingerprint: string;
  effective_classification: string;
  selected_source_count: number;
  fragment_count: number;
  total_source_characters: number;
  context_sha256: string;
  suspicious_source_count: number;
  citations: Citation[];
  source_scans: Array<{
    document_id: string;
    run_id: string;
    source_kind: SourceKind;
    source_id: string;
    scan: {
      status: string;
      finding_count: number;
      truncated: boolean;
      findings: Array<{
        code: string;
        severity: string;
        matched_text_sha256: string;
      }>;
    };
  }>;
  budget: ContextBudget;
  manifest_fingerprint: string;
};

type PreflightResult = {
  context: { manifest: ContextManifest };
  primary_model: {
    provider: string;
    model: string;
    context_window: number;
    max_output_tokens: number;
    supports_json: boolean;
    fingerprint: string;
  };
  reviewer_model: {
    provider: string;
    model: string;
    context_window: number;
    max_output_tokens: number;
    supports_json: boolean;
    fingerprint: string;
  };
  stages: Array<{
    stage: "primary" | "reviewer";
    provider: string;
    provider_trust: string;
    runtime_policy: {
      policy_version: string;
      action: string;
      reason_codes: string[];
      fingerprint: string;
    };
    external_provider_warning: string | null;
  }>;
  reviewer_planned_tokens: number;
  retention_policy: string;
  retention_days: number;
  external_provider_warning_required: boolean;
};

type AnalysisRecord = {
  id: string;
  workspace_id: string;
  workflow: "summary" | "question";
  status: string;
  idempotency_key: string;
  selection_fingerprint: string;
  context_sha256: string;
  effective_classification: string;
  primary: {
    provider: string;
    model: string;
    request_id: string;
  };
  reviewer: {
    provider: string;
    model: string;
    request_id: string;
    verdict: {
      approved?: boolean;
      citation_coverage?: string;
      policy_compliant?: boolean;
      reason_codes?: string[];
    };
    response_sha256: string | null;
  };
  output_text_sha256: string | null;
  output_citation_ids: string[];
  citations: Citation[];
  content_state: string;
  content_purged_at: string | null;
  runtime_policy: Record<string, unknown>;
  approval_evidence: Record<
    string,
    {
      approval_id?: string;
      status?: string;
      expires_at?: string;
      consumed_at?: string;
    }
  >;
  provider_evidence: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  retention_policy: string;
  retention_days: number;
  retention_expires_at: string;
  created_at: string;
  completed_at: string | null;
  failed_at: string | null;
  question?: string | null;
  output_text?: string;
};

type AnalysisExecution = {
  created: boolean;
  reused: boolean;
  approval_required: boolean;
  approval_stage: "primary" | "reviewer" | null;
  analysis: AnalysisRecord;
};

type DocumentAIRequest = {
  workflow: "summary" | "question";
  question?: string;
  sources: Array<{
    document_id: string;
    run_id: string;
    source_kind: SourceKind;
    source_id: string;
  }>;
  provider: string;
  model: string;
  reviewer_provider: string;
  reviewer_model: string;
  max_output_tokens: number;
  reviewer_max_output_tokens: number;
  retention_days?: number;
};

type DocumentsWorkspaceProps = {
  onOpenApprovals: () => void;
};

function errorMessage(value: unknown, fallback: string): string {
  if (typeof value !== "object" || value === null) return fallback;
  const detail = (value as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const firstMessage = detail.find(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as { msg?: unknown }).msg === "string",
    ) as { msg?: string } | undefined;
    if (firstMessage?.msg) return firstMessage.msg;
  }
  if (typeof detail === "object" && detail !== null) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string") return message;
    const nested = (detail as { detail?: unknown }).detail;
    if (typeof nested === "string") return nested;
  }
  const message = (value as { message?: unknown }).message;
  return typeof message === "string" ? message : fallback;
}

function exceptionMessage(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

async function readResponse(response: Response): Promise<unknown> {
  const body = await response.text();
  if (!body) return {};
  try {
    return JSON.parse(body) as unknown;
  } catch {
    return {};
  }
}

async function requestJson<T>(
  url: string,
  init: RequestInit | undefined,
  fallback: string,
): Promise<T> {
  const response = await fetch(url, { ...init, cache: "no-store" });
  const data = await readResponse(response);
  if (!response.ok) {
    throw new Error(errorMessage(data, fallback));
  }
  return data as T;
}

async function requestResponse(
  url: string,
  init: RequestInit | undefined,
): Promise<{ response: Response; data: unknown }> {
  const response = await fetch(url, { ...init, cache: "no-store" });
  return { response, data: await readResponse(response) };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function failedRunMessage(
  run: { error_code: string | null; error_message: string | null },
  fallback: string,
): string {
  const code = run.error_code?.trim();
  const message = run.error_message?.trim();
  if (code && message) return `${code}: ${message}`;
  return code || message || fallback;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(2)} MiB`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ru-RU");
}

function shortHash(value: string | null | undefined): string {
  if (!value) return "—";
  return value.length > 18 ? `${value.slice(0, 12)}…${value.slice(-6)}` : value;
}

function boundedInteger(
  value: string,
  name: string,
  minimum: number,
  maximum: number,
): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name}: допустимо целое число ${minimum}–${maximum}.`);
  }
  return parsed;
}

function parsePageNumbers(value: string): number[] | null {
  const normalized = value.trim();
  if (!normalized) return null;
  const pages = new Set<number>();
  for (const token of normalized.split(",")) {
    const part = token.trim();
    const match = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!match) {
      throw new Error("Страницы OCR задаются как 1,3-5,8.");
    }
    const start = Number(match[1]);
    const end = Number(match[2] ?? match[1]);
    if (start < 1 || end < start || end > 10_000) {
      throw new Error("Диапазон страниц OCR недопустим.");
    }
    for (let page = start; page <= end; page += 1) {
      pages.add(page);
      if (pages.size > 100) {
        throw new Error(
          "За один OCR-запуск можно выбрать не более 100 страниц.",
        );
      }
    }
  }
  return [...pages].sort((left, right) => left - right);
}

function createIdempotencyKey(): string {
  const value = globalThis.crypto?.randomUUID?.();
  if (value) return `document-ai-${value}`;
  return `document-ai-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function sourceKey(
  documentId: string,
  runId: string,
  kind: SourceKind,
  sourceId: string,
): string {
  return `${documentId}:${runId}:${kind}:${sourceId}`;
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "Ожидает",
    running: "Выполняется",
    completed: "Завершено",
    failed: "Ошибка",
    rejected: "Отклонено reviewer",
    awaiting_primary_approval: "Нужно primary approval",
    primary_running: "Primary выполняется",
    awaiting_reviewer: "Ожидает reviewer",
    awaiting_reviewer_approval: "Нужно reviewer approval",
    reviewer_running: "Reviewer выполняется",
    active: "Активно",
    purged: "Очищено retention",
  };
  return labels[status] ?? status;
}

function statusTone(status: string): string {
  if (status === "completed" || status === "active") return "success";
  if (status === "failed" || status === "rejected" || status === "purged") {
    return "danger";
  }
  return "warning";
}

function formatLocation(location: CitationLocation): string {
  return Object.entries(location)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

function SourceCard({
  source,
  selected,
  disabled,
  onToggle,
}: {
  source: SelectedSource;
  selected: boolean;
  disabled: boolean;
  onToggle: (source: SelectedSource) => void;
}) {
  return (
    <article className={`document-source-card ${selected ? "selected" : ""}`}>
      <label className="document-source-select">
        <input
          type="checkbox"
          checked={selected}
          disabled={disabled}
          onChange={() => onToggle(source)}
        />
        <span>
          <strong>{source.label}</strong>
          <small>
            {source.character_count.toLocaleString("ru-RU")} знаков ·{" "}
            {shortHash(source.text_sha256)}
          </small>
        </span>
      </label>
      <details>
        <summary>Точный preview и provenance</summary>
        <pre className="document-text-preview">
          {source.text || "Текст отсутствует."}
        </pre>
        <pre className="document-provenance-preview">
          {JSON.stringify(source.provenance, null, 2)}
        </pre>
      </details>
    </article>
  );
}

function AnalysisView({ analysis }: { analysis: AnalysisRecord }) {
  const verdict = analysis.reviewer.verdict;
  return (
    <section className="document-analysis-result">
      <div className="document-result-heading">
        <div>
          <span className="step">Governed result</span>
          <h3>
            {analysis.workflow === "summary" ? "Резюме" : "Ответ на вопрос"}
          </h3>
        </div>
        <span className={`document-status ${statusTone(analysis.status)}`}>
          {statusLabel(analysis.status)}
        </span>
      </div>

      {analysis.status === "completed" && analysis.output_text ? (
        <div className="document-final-answer">{analysis.output_text}</div>
      ) : (
        <div className="document-gate-note">
          {analysis.content_state === "purged"
            ? "Текст результата удалён согласно retention policy; evidence и hashes сохранены."
            : analysis.status === "rejected"
              ? "Primary output не выдан: независимый reviewer отклонил результат."
              : analysis.status === "failed"
                ? "Результат не выдан из-за fail-closed ошибки."
                : "Primary output закрыт до успешного независимого reviewer verdict."}
        </div>
      )}

      {analysis.error_code && (
        <div className="document-alert danger">
          <strong>{analysis.error_code}</strong>
          <span>
            {analysis.error_message ?? "Document AI завершился с ошибкой."}
          </span>
        </div>
      )}

      <div className="document-evidence-grid">
        <span>
          <small>Classification</small>
          <strong>{analysis.effective_classification}</strong>
        </span>
        <span>
          <small>Content state</small>
          <strong>{analysis.content_state}</strong>
        </span>
        <span>
          <small>Primary</small>
          <strong>
            {analysis.primary.provider} / {analysis.primary.model}
          </strong>
        </span>
        <span>
          <small>Reviewer</small>
          <strong>
            {analysis.reviewer.provider} / {analysis.reviewer.model}
          </strong>
        </span>
        <span>
          <small>Citation coverage</small>
          <strong>{verdict.citation_coverage ?? "—"}</strong>
        </span>
        <span>
          <small>Policy compliant</small>
          <strong>
            {verdict.policy_compliant === undefined
              ? "—"
              : verdict.policy_compliant
                ? "Да"
                : "Нет"}
          </strong>
        </span>
      </div>

      {analysis.citations.length > 0 && (
        <div className="document-citation-list">
          <h4>Проверяемые citations</h4>
          {analysis.citations.map((citation) => (
            <article key={citation.citation_id}>
              <div className="document-citation-heading">
                <strong>{citation.citation_id}</strong>
                <span>{citation.source_kind}</span>
              </div>
              <dl className="document-citation-identity">
                <div>
                  <dt>Document</dt>
                  <dd>
                    <code>{citation.document_id}</code>
                  </dd>
                </div>
                <div>
                  <dt>Run</dt>
                  <dd>
                    <code>{citation.run_id}</code>
                  </dd>
                </div>
                <div>
                  <dt>Source</dt>
                  <dd>
                    <code>{citation.source_id}</code>
                  </dd>
                </div>
              </dl>
              <span className="document-citation-location">
                {citation.locations.map(formatLocation).join(" | ") ||
                  "Location отсутствует"}
              </span>
              <div className="document-citation-hash">
                <small>Fragment SHA-256</small>
                <code>{citation.fragment_text_sha256}</code>
              </div>
            </article>
          ))}
        </div>
      )}

      <details className="document-evidence-details">
        <summary>Decision evidence</summary>
        <pre>
          {JSON.stringify(
            {
              context_sha256: analysis.context_sha256,
              selection_fingerprint: analysis.selection_fingerprint,
              output_text_sha256: analysis.output_text_sha256,
              output_citation_ids: analysis.output_citation_ids,
              reviewer: analysis.reviewer,
              runtime_policy: analysis.runtime_policy,
              approval_evidence: analysis.approval_evidence,
              provider_evidence: analysis.provider_evidence,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

export default function DocumentsWorkspace({
  onOpenApprovals,
}: DocumentsWorkspaceProps) {
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([]);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("");
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [selectedDocumentId, setSelectedDocumentId] = useState("");
  const [registryRevision, setRegistryRevision] = useState(0);
  const [analysisRevision, setAnalysisRevision] = useState(0);
  const [initialLoading, setInitialLoading] = useState(true);
  const [registryLoading, setRegistryLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadInputKey, setUploadInputKey] = useState(0);

  const [extractionRuns, setExtractionRuns] = useState<ExtractionRun[]>([]);
  const [selectedExtractionRunId, setSelectedExtractionRunId] = useState("");
  const [extractionUnits, setExtractionUnits] = useState<ExtractionUnit[]>([]);
  const [extractionChunks, setExtractionChunks] = useState<ExtractionChunk[]>(
    [],
  );
  const [ocrRuns, setOCRRuns] = useState<OCRRun[]>([]);
  const [selectedOCRRunId, setSelectedOCRRunId] = useState("");
  const [ocrPages, setOCRPages] = useState<OCRPage[]>([]);
  const [derivedLoading, setDerivedLoading] = useState(false);
  const derivedRequestGeneration = useRef(0);
  const [ocrPagesInput, setOCRPagesInput] = useState("");
  const [ocrRetentionDays, setOCRRetentionDays] = useState("");
  const [selectedSources, setSelectedSources] = useState<SelectedSource[]>([]);

  const [workspaceSection, setWorkspaceSection] = useState<
    "processing" | "analysis" | "history"
  >("processing");
  const [workflow, setWorkflow] = useState<"summary" | "question">("summary");
  const [question, setQuestion] = useState("");
  const [primaryModelSlug, setPrimaryModelSlug] = useState("");
  const [reviewerModelSlug, setReviewerModelSlug] = useState("");
  const [maxOutputTokens, setMaxOutputTokens] = useState("1000");
  const [reviewerMaxOutputTokens, setReviewerMaxOutputTokens] = useState("512");
  const [analysisRetentionDays, setAnalysisRetentionDays] = useState("");
  const [preflight, setPreflight] = useState<{
    specKey: string;
    result: PreflightResult;
  } | null>(null);
  const [analysisExecution, setAnalysisExecution] =
    useState<AnalysisExecution | null>(null);
  const [analysisRuns, setAnalysisRuns] = useState<AnalysisRecord[]>([]);
  const [historyDetail, setHistoryDetail] = useState<AnalysisRecord | null>(
    null,
  );
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const interactionLocked =
    busy || analysisBusy || derivedLoading || registryLoading;
  const [externalAcknowledged, setExternalAcknowledged] = useState(false);
  const [idempotencyKey, setIdempotencyKey] = useState(createIdempotencyKey);
  const [primaryApprovalId, setPrimaryApprovalId] = useState("");
  const [primaryApprovalToken, setPrimaryApprovalToken] = useState("");
  const [reviewerApprovalId, setReviewerApprovalId] = useState("");
  const [reviewerApprovalToken, setReviewerApprovalToken] = useState("");

  const selectedDocument = useMemo(
    () => documents.find((item) => item.id === selectedDocumentId) ?? null,
    [documents, selectedDocumentId],
  );
  const eligibleModels = useMemo(
    () =>
      models.filter(
        (item) =>
          item.enabled &&
          item.supports_json === true &&
          item.metadata?.catalog_hidden !== true,
      ),
    [models],
  );
  const primaryModel = useMemo(
    () => eligibleModels.find((item) => item.slug === primaryModelSlug) ?? null,
    [eligibleModels, primaryModelSlug],
  );
  const reviewerModel = useMemo(
    () =>
      eligibleModels.find((item) => item.slug === reviewerModelSlug) ?? null,
    [eligibleModels, reviewerModelSlug],
  );
  const sourcePayload = useMemo(
    () =>
      selectedSources.map((source) => ({
        document_id: source.document_id,
        run_id: source.run_id,
        source_kind: source.source_kind,
        source_id: source.source_id,
      })),
    [selectedSources],
  );
  const analysisSpecKey = useMemo(
    () =>
      JSON.stringify({
        workspace_id: selectedWorkspaceId,
        workflow,
        question: workflow === "question" ? question : null,
        sources: sourcePayload,
        primary: primaryModelSlug,
        reviewer: reviewerModelSlug,
        max_output_tokens: maxOutputTokens,
        reviewer_max_output_tokens: reviewerMaxOutputTokens,
        retention_days: analysisRetentionDays,
      }),
    [
      analysisRetentionDays,
      maxOutputTokens,
      primaryModelSlug,
      question,
      reviewerMaxOutputTokens,
      reviewerModelSlug,
      selectedWorkspaceId,
      sourcePayload,
      workflow,
    ],
  );

  function selectWorkspace(workspaceId: string) {
    derivedRequestGeneration.current += 1;
    setDerivedLoading(false);
    setSelectedWorkspaceId(workspaceId);
    setDocuments([]);
    setSelectedDocumentId("");
    setSelectedSources([]);
    setExtractionRuns([]);
    setSelectedExtractionRunId("");
    setExtractionUnits([]);
    setExtractionChunks([]);
    setOCRRuns([]);
    setSelectedOCRRunId("");
    setOCRPages([]);
    setAnalysisRuns([]);
    setHistoryDetail(null);
    setPreflight(null);
    setAnalysisExecution(null);
    setExternalAcknowledged(false);
    setPrimaryApprovalId("");
    setPrimaryApprovalToken("");
    setReviewerApprovalId("");
    setReviewerApprovalToken("");
    setUploadFile(null);
    setUploadInputKey((value) => value + 1);
  }

  function selectDocument(documentId: string) {
    derivedRequestGeneration.current += 1;
    setDerivedLoading(false);
    setSelectedDocumentId(documentId);
    setExtractionRuns([]);
    setSelectedExtractionRunId("");
    setExtractionUnits([]);
    setExtractionChunks([]);
    setOCRRuns([]);
    setSelectedOCRRunId("");
    setOCRPages([]);
  }

  useEffect(() => {
    let cancelled = false;
    async function loadInitialData() {
      setInitialLoading(true);
      try {
        const [workspaceData, modelData] = await Promise.all([
          requestJson<{ workspaces?: WorkspaceInfo[] }>(
            `${API_BASE}/workspaces?active_only=true`,
            undefined,
            "Не удалось загрузить Workspaces.",
          ),
          requestJson<{ models?: ModelInfo[] }>(
            `${API_BASE}/models?enabled_only=true`,
            undefined,
            "Не удалось загрузить модели.",
          ),
        ]);
        if (cancelled) return;
        const activeWorkspaces = workspaceData.workspaces ?? [];
        const availableModels = modelData.models ?? [];
        const jsonModels = availableModels.filter(
          (item) =>
            item.enabled &&
            item.supports_json === true &&
            item.metadata?.catalog_hidden !== true,
        );
        setWorkspaces(activeWorkspaces);
        setModels(availableModels);
        selectWorkspace(activeWorkspaces[0]?.id ?? "");
        setPrimaryModelSlug(jsonModels[0]?.slug ?? "");
        setReviewerModelSlug(jsonModels[1]?.slug ?? "");
      } catch (caught) {
        if (!cancelled) setError(exceptionMessage(caught));
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    }
    void loadInitialData();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedWorkspaceId) {
      setDocuments([]);
      setAnalysisRuns([]);
      return;
    }
    let cancelled = false;
    async function loadWorkspaceData() {
      setRegistryLoading(true);
      setHistoryDetail(null);
      try {
        const workspacePath = encodeURIComponent(selectedWorkspaceId);
        const [documentResult, analysisResult] = await Promise.allSettled([
          requestJson<{ items?: DocumentRecord[] }>(
            `${API_BASE}/workspaces/${workspacePath}/documents?limit=500`,
            undefined,
            "Не удалось загрузить реестр документов.",
          ),
          requestJson<{ items?: AnalysisRecord[] }>(
            `${API_BASE}/workspaces/${workspacePath}/document-ai/runs?limit=100`,
            undefined,
            "Не удалось загрузить историю Document AI.",
          ),
        ]);
        if (cancelled) return;
        if (documentResult.status === "rejected") {
          throw documentResult.reason;
        }
        const documentData = documentResult.value;
        const nextDocuments = documentData.items ?? [];
        setDocuments(nextDocuments);
        if (analysisResult.status === "fulfilled") {
          setAnalysisRuns(analysisResult.value.items ?? []);
        } else {
          setAnalysisRuns([]);
          setError(
            `Реестр загружен, но история Document AI недоступна: ${exceptionMessage(analysisResult.reason)}`,
          );
        }
        setSelectedDocumentId((current) =>
          nextDocuments.some((item) => item.id === current)
            ? current
            : (nextDocuments[0]?.id ?? ""),
        );
      } catch (caught) {
        if (!cancelled) setError(exceptionMessage(caught));
      } finally {
        if (!cancelled) setRegistryLoading(false);
      }
    }
    void loadWorkspaceData();
    return () => {
      cancelled = true;
    };
  }, [analysisRevision, registryRevision, selectedWorkspaceId]);

  useEffect(() => {
    if (!selectedWorkspaceId || !selectedDocumentId) {
      setExtractionRuns([]);
      setOCRRuns([]);
      return;
    }
    let cancelled = false;
    async function loadDocumentRuns() {
      setDerivedLoading(true);
      setExtractionUnits([]);
      setExtractionChunks([]);
      setOCRPages([]);
      try {
        const workspacePath = encodeURIComponent(selectedWorkspaceId);
        const documentPath = encodeURIComponent(selectedDocumentId);
        const [extractionData, ocrData] = await Promise.all([
          requestJson<{ items?: ExtractionRun[] }>(
            `${API_BASE}/workspaces/${workspacePath}/documents/${documentPath}/extractions?limit=100`,
            undefined,
            "Не удалось загрузить extraction runs.",
          ),
          requestJson<{ items?: OCRRun[] }>(
            `${API_BASE}/workspaces/${workspacePath}/documents/${documentPath}/ocr-runs?limit=100`,
            undefined,
            "Не удалось загрузить OCR runs.",
          ),
        ]);
        if (cancelled) return;
        const nextExtractions = extractionData.items ?? [];
        const nextOCR = ocrData.items ?? [];
        setExtractionRuns(nextExtractions);
        setOCRRuns(nextOCR);
        setSelectedExtractionRunId((current) =>
          nextExtractions.some((item) => item.id === current)
            ? current
            : (nextExtractions[0]?.id ?? ""),
        );
        setSelectedOCRRunId((current) =>
          nextOCR.some((item) => item.id === current)
            ? current
            : (nextOCR[0]?.id ?? ""),
        );
      } catch (caught) {
        if (!cancelled) setError(exceptionMessage(caught));
      } finally {
        if (!cancelled) setDerivedLoading(false);
      }
    }
    void loadDocumentRuns();
    return () => {
      cancelled = true;
    };
  }, [registryRevision, selectedDocumentId, selectedWorkspaceId]);

  useEffect(() => {
    setPreflight(null);
    setAnalysisExecution(null);
    setExternalAcknowledged(false);
    setIdempotencyKey(createIdempotencyKey());
    setPrimaryApprovalId("");
    setPrimaryApprovalToken("");
    setReviewerApprovalId("");
    setReviewerApprovalToken("");
  }, [analysisSpecKey]);

  function clearNotices() {
    setError("");
    setMessage("");
  }

  function selectExtractionRun(runId: string) {
    derivedRequestGeneration.current += 1;
    setDerivedLoading(false);
    setSelectedExtractionRunId(runId);
    setExtractionUnits([]);
    setExtractionChunks([]);
  }

  function selectOCRRun(runId: string) {
    derivedRequestGeneration.current += 1;
    setDerivedLoading(false);
    setSelectedOCRRunId(runId);
    setOCRPages([]);
  }

  async function uploadDocument() {
    if (!selectedWorkspaceId || !uploadFile || interactionLocked) return;
    clearNotices();
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", uploadFile, uploadFile.name);
      form.append("metadata_json", "{}");
      const data = await requestJson<{
        created: boolean;
        restored: boolean;
        document: DocumentRecord;
      }>(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/documents`,
        { method: "POST", body: form },
        "Не удалось загрузить документ.",
      );
      selectDocument(data.document.id);
      setUploadFile(null);
      setUploadInputKey((value) => value + 1);
      setMessage(
        data.restored
          ? "Документ восстановлен в Workspace registry."
          : data.created
            ? "Документ безопасно добавлен в Workspace registry."
            : "Идентичный документ уже зарегистрирован; использована существующая запись.",
      );
      setRegistryRevision((value) => value + 1);
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function deleteDocument() {
    if (!selectedWorkspaceId || !selectedDocument || interactionLocked) {
      return;
    }
    if (
      !window.confirm(
        `Удалить «${selectedDocument.original_filename}» и весь derived content этого документа?`,
      )
    )
      return;
    clearNotices();
    setBusy(true);
    try {
      const data = await requestJson<{
        deleted: boolean;
        storage_deleted: boolean;
        derived_deleted: Record<string, number>;
      }>(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/documents/${encodeURIComponent(selectedDocument.id)}`,
        { method: "DELETE" },
        "Не удалось удалить документ.",
      );
      setSelectedSources((current) =>
        current.filter((source) => source.document_id !== selectedDocument.id),
      );
      setHistoryDetail(null);
      setAnalysisExecution(null);
      selectDocument("");
      setMessage(
        `Документ удалён. Derived records: ${Object.values(data.derived_deleted).reduce((sum, value) => sum + value, 0)}.`,
      );
      setRegistryRevision((value) => value + 1);
      setAnalysisRevision((value) => value + 1);
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function startExtraction() {
    if (!selectedWorkspaceId || !selectedDocument || interactionLocked) {
      return;
    }
    clearNotices();
    setBusy(true);
    try {
      type ExtractionEnvelope = {
        created: boolean;
        reused: boolean;
        extraction: ExtractionRun;
      };
      const { response, data: value } = await requestResponse(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/documents/${encodeURIComponent(selectedDocument.id)}/extractions`,
        { method: "POST" },
      );
      if (!isObject(value) || !isObject(value.extraction)) {
        if (!response.ok) {
          throw new Error(
            errorMessage(value, "Не удалось выполнить extraction."),
          );
        }
        throw new Error("Extraction API вернул некорректный envelope.");
      }
      const data = value as unknown as ExtractionEnvelope;
      selectExtractionRun(data.extraction.id);
      setRegistryRevision((value) => value + 1);
      if (!response.ok || data.extraction.status === "failed") {
        setError(
          failedRunMessage(
            data.extraction,
            "Extraction завершён с безопасно сохранённой ошибкой.",
          ),
        );
        return;
      }
      setMessage(
        data.extraction.status === "completed"
          ? `Extraction завершён: ${data.extraction.unit_count} units, ${data.extraction.chunk_count} chunks.`
          : `Extraction: ${statusLabel(data.extraction.status)}.`,
      );
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function loadExtractionSources() {
    if (
      !selectedWorkspaceId ||
      !selectedDocument ||
      !selectedExtractionRunId ||
      interactionLocked
    )
      return;
    const requestWorkspaceId = selectedWorkspaceId;
    const requestDocumentId = selectedDocument.id;
    const requestRunId = selectedExtractionRunId;
    const requestGeneration = ++derivedRequestGeneration.current;
    clearNotices();
    setDerivedLoading(true);
    try {
      const base = `${API_BASE}/workspaces/${encodeURIComponent(requestWorkspaceId)}/documents/${encodeURIComponent(requestDocumentId)}/extractions/${encodeURIComponent(requestRunId)}`;
      const [unitData, chunkData] = await Promise.all([
        requestJson<ExtractionSourceList<ExtractionUnit>>(
          `${base}/units?limit=500`,
          undefined,
          "Не удалось загрузить extraction units.",
        ),
        requestJson<ExtractionSourceList<ExtractionChunk>>(
          `${base}/chunks?limit=500`,
          undefined,
          "Не удалось загрузить extraction chunks.",
        ),
      ]);
      if (derivedRequestGeneration.current !== requestGeneration) return;
      const scopeMatches = [unitData, chunkData].every(
        (result) =>
          result.workspace_id === requestWorkspaceId &&
          result.document_id === requestDocumentId &&
          result.run_id === requestRunId,
      );
      const units = unitData.items ?? [];
      const chunks = chunkData.items ?? [];
      if (
        !scopeMatches ||
        units.some(
          (item) =>
            item.workspace_id !== requestWorkspaceId ||
            item.document_id !== requestDocumentId ||
            item.run_id !== requestRunId,
        ) ||
        chunks.some(
          (item) =>
            item.workspace_id !== requestWorkspaceId ||
            item.document_id !== requestDocumentId ||
            item.run_id !== requestRunId,
        )
      ) {
        throw new Error(
          "Extraction preview отклонён: API вернул данные другого Workspace/document/run.",
        );
      }
      setExtractionUnits(units);
      setExtractionChunks(chunks);
      setMessage("Derived text загружен локально для preview и явного выбора.");
    } catch (caught) {
      if (derivedRequestGeneration.current === requestGeneration) {
        setError(exceptionMessage(caught));
      }
    } finally {
      if (derivedRequestGeneration.current === requestGeneration) {
        setDerivedLoading(false);
      }
    }
  }

  async function startOCR() {
    if (!selectedWorkspaceId || !selectedDocument || interactionLocked) {
      return;
    }
    clearNotices();
    setBusy(true);
    try {
      if (selectedDocument.document_format !== "pdf") {
        throw new Error("OCR доступен только для PDF.");
      }
      const pageNumbers = parsePageNumbers(ocrPagesInput);
      const retentionDays = ocrRetentionDays.trim()
        ? boundedInteger(ocrRetentionDays, "OCR retention", 1, 3650)
        : undefined;
      type OCREnvelope = {
        created: boolean;
        reused: boolean;
        ocr_run: OCRRun;
      };
      const { response, data: value } = await requestResponse(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/documents/${encodeURIComponent(selectedDocument.id)}/ocr-runs`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            page_numbers: pageNumbers,
            ...(retentionDays === undefined
              ? {}
              : { retention_days: retentionDays }),
          }),
        },
      );
      if (!isObject(value) || !isObject(value.ocr_run)) {
        if (!response.ok) {
          throw new Error(errorMessage(value, "Не удалось выполнить OCR."));
        }
        throw new Error("OCR API вернул некорректный envelope.");
      }
      const data = value as unknown as OCREnvelope;
      selectOCRRun(data.ocr_run.id);
      setRegistryRevision((value) => value + 1);
      if (!response.ok || data.ocr_run.status === "failed") {
        setError(
          failedRunMessage(
            data.ocr_run,
            "OCR завершён с безопасно сохранённой ошибкой.",
          ),
        );
        return;
      }
      setMessage(
        data.ocr_run.status === "completed"
          ? `OCR завершён: ${data.ocr_run.selected_page_count} страниц.`
          : `OCR: ${statusLabel(data.ocr_run.status)}.`,
      );
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function loadOCRSources() {
    if (
      !selectedWorkspaceId ||
      !selectedDocument ||
      !selectedOCRRunId ||
      interactionLocked
    )
      return;
    const requestWorkspaceId = selectedWorkspaceId;
    const requestDocumentId = selectedDocument.id;
    const requestRunId = selectedOCRRunId;
    const requestGeneration = ++derivedRequestGeneration.current;
    clearNotices();
    setDerivedLoading(true);
    try {
      const data = await requestJson<OCRPageList>(
        `${API_BASE}/workspaces/${encodeURIComponent(requestWorkspaceId)}/documents/${encodeURIComponent(requestDocumentId)}/ocr-runs/${encodeURIComponent(requestRunId)}/pages?include_text=true&limit=100`,
        undefined,
        "Не удалось загрузить OCR pages.",
      );
      if (derivedRequestGeneration.current !== requestGeneration) return;
      const pages = data.items ?? [];
      if (
        data.workspace_id !== requestWorkspaceId ||
        data.document_id !== requestDocumentId ||
        data.run_id !== requestRunId ||
        data.include_text !== true ||
        pages.some(
          (item) =>
            item.workspace_id !== requestWorkspaceId ||
            item.document_id !== requestDocumentId ||
            item.run_id !== requestRunId,
        )
      ) {
        throw new Error(
          "OCR preview отклонён: API вернул данные другого Workspace/document/run.",
        );
      }
      setOCRPages(pages);
      setMessage("OCR text загружен локально для preview и явного выбора.");
    } catch (caught) {
      if (derivedRequestGeneration.current === requestGeneration) {
        setError(exceptionMessage(caught));
      }
    } finally {
      if (derivedRequestGeneration.current === requestGeneration) {
        setDerivedLoading(false);
      }
    }
  }

  function toggleSource(source: SelectedSource) {
    if (interactionLocked) return;
    clearNotices();
    setSelectedSources((current) => {
      if (current.some((item) => item.key === source.key)) {
        return current.filter((item) => item.key !== source.key);
      }
      if (current.length >= MAX_SELECTED_SOURCES) {
        setError(
          `Можно выбрать не более ${MAX_SELECTED_SOURCES} persisted sources.`,
        );
        return current;
      }
      return [...current, source];
    });
  }

  function sourceDocumentName(documentId: string): string {
    return (
      documents.find((document) => document.id === documentId)?.safe_filename ??
      documentId
    );
  }

  function makeUnitSource(unit: ExtractionUnit): SelectedSource {
    return {
      key: sourceKey(unit.document_id, unit.run_id, "extraction_unit", unit.id),
      document_id: unit.document_id,
      document_name: sourceDocumentName(unit.document_id),
      run_id: unit.run_id,
      source_kind: "extraction_unit",
      source_id: unit.id,
      label: `Unit ${unit.ordinal + 1} · ${unit.kind}`,
      text: unit.text,
      text_sha256: unit.text_sha256,
      character_count: unit.character_count,
      provenance: unit.provenance,
    };
  }

  function makeChunkSource(chunk: ExtractionChunk): SelectedSource {
    return {
      key: sourceKey(
        chunk.document_id,
        chunk.run_id,
        "extraction_chunk",
        chunk.id,
      ),
      document_id: chunk.document_id,
      document_name: sourceDocumentName(chunk.document_id),
      run_id: chunk.run_id,
      source_kind: "extraction_chunk",
      source_id: chunk.id,
      label: `Chunk ${chunk.ordinal + 1} · ${chunk.character_start}–${chunk.character_end}`,
      text: chunk.text,
      text_sha256: chunk.text_sha256,
      character_count: chunk.character_count,
      provenance: {
        ...chunk.provenance,
        source_unit_ordinals: chunk.source_unit_ordinals,
      },
    };
  }

  function makeOCRSource(page: OCRPage): SelectedSource {
    return {
      key: sourceKey(page.document_id, page.run_id, "ocr_page", page.id),
      document_id: page.document_id,
      document_name: sourceDocumentName(page.document_id),
      run_id: page.run_id,
      source_kind: "ocr_page",
      source_id: page.id,
      label: `OCR page ${page.page_number}`,
      text: page.text ?? "",
      text_sha256: page.text_sha256,
      character_count: page.character_count,
      provenance: {
        page_number: page.page_number,
        classification: page.classification,
        retention_expires_at: page.retention_expires_at,
        warnings: page.warnings,
      },
    };
  }

  function buildAIRequest(): DocumentAIRequest {
    if (!selectedWorkspaceId) throw new Error("Выберите Workspace.");
    if (selectedSources.length < 1) {
      throw new Error("Явно выберите хотя бы один persisted source.");
    }
    if (!primaryModel || !reviewerModel) {
      throw new Error("Выберите primary и reviewer models с JSON capability.");
    }
    if (primaryModel.slug === reviewerModel.slug) {
      throw new Error("Primary и reviewer должны быть разными моделями.");
    }
    const normalizedQuestion = question.trim();
    if (workflow === "question" && !normalizedQuestion) {
      throw new Error("Введите вопрос к выбранным источникам.");
    }
    const retentionDays = analysisRetentionDays.trim()
      ? boundedInteger(analysisRetentionDays, "Analysis retention", 1, 3650)
      : undefined;
    return {
      workflow,
      ...(workflow === "question" ? { question: normalizedQuestion } : {}),
      sources: sourcePayload,
      provider: primaryModel.provider,
      model: primaryModel.slug,
      reviewer_provider: reviewerModel.provider,
      reviewer_model: reviewerModel.slug,
      max_output_tokens: boundedInteger(
        maxOutputTokens,
        "Primary output",
        1,
        32_768,
      ),
      reviewer_max_output_tokens: boundedInteger(
        reviewerMaxOutputTokens,
        "Reviewer output",
        1,
        8_192,
      ),
      ...(retentionDays === undefined ? {} : { retention_days: retentionDays }),
    };
  }

  async function runPreflight() {
    if (interactionLocked) return;
    clearNotices();
    setAnalysisBusy(true);
    try {
      const request = buildAIRequest();
      const result = await requestJson<PreflightResult>(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/document-ai/preflight`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        },
        "Document AI preflight не пройден.",
      );
      setPreflight({ specKey: analysisSpecKey, result });
      setExternalAcknowledged(false);
      setMessage(
        "Preflight завершён: context manifest и Runtime Policy доступны для проверки.",
      );
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setAnalysisBusy(false);
    }
  }

  async function executeAnalysis() {
    if (interactionLocked) return;
    clearNotices();
    setAnalysisBusy(true);
    try {
      if (!preflight || preflight.specKey !== analysisSpecKey) {
        throw new Error("Сначала выполните актуальный preflight.");
      }
      if (
        preflight.result.external_provider_warning_required &&
        !externalAcknowledged
      ) {
        throw new Error(
          "Подтвердите передачу выбранного контекста внешним providers.",
        );
      }
      const request = buildAIRequest();
      const currentStage = analysisExecution?.approval_stage;
      if (
        currentStage === "primary" &&
        (!primaryApprovalId || !primaryApprovalToken)
      ) {
        throw new Error(
          "Для продолжения нужны primary approval id и one-time token.",
        );
      }
      if (
        currentStage === "reviewer" &&
        (!reviewerApprovalId || !reviewerApprovalToken)
      ) {
        throw new Error(
          "Для продолжения нужны reviewer approval id и one-time token.",
        );
      }
      const submittedPrimaryToken = Boolean(
        primaryApprovalId && primaryApprovalToken,
      );
      const submittedReviewerToken = Boolean(
        reviewerApprovalId && reviewerApprovalToken,
      );
      const response = await fetch(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/document-ai/runs`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...request,
            idempotency_key: idempotencyKey,
            external_provider_acknowledged: externalAcknowledged,
            ...(primaryApprovalId && primaryApprovalToken
              ? {
                  primary_approval_id: primaryApprovalId,
                  primary_approval_token: primaryApprovalToken,
                }
              : {}),
            ...(reviewerApprovalId && reviewerApprovalToken
              ? {
                  reviewer_approval_id: reviewerApprovalId,
                  reviewer_approval_token: reviewerApprovalToken,
                }
              : {}),
          }),
        },
      );
      if (submittedPrimaryToken) setPrimaryApprovalToken("");
      if (submittedReviewerToken) setReviewerApprovalToken("");
      const value = await readResponse(response);
      const result = value as AnalysisExecution;
      if ((!response.ok && !result.analysis) || !result.analysis) {
        throw new Error(
          errorMessage(value, "Document AI execution не выполнен."),
        );
      }
      setAnalysisExecution(result);
      setAnalysisRevision((revision) => revision + 1);
      if (result.approval_required && result.approval_stage) {
        const evidence =
          result.analysis.approval_evidence[result.approval_stage];
        const approvalId = evidence?.approval_id ?? "";
        if (result.approval_stage === "primary") {
          setPrimaryApprovalId(approvalId);
        } else {
          setPrimaryApprovalId("");
          setPrimaryApprovalToken("");
          setReviewerApprovalId(approvalId);
        }
        setMessage(
          `${result.approval_stage === "primary" ? "Primary" : "Reviewer"} требует Human Control approval. Одобрите exact scope в разделе Approvals и вставьте one-time token.`,
        );
      } else if (result.analysis.status === "completed") {
        setPrimaryApprovalToken("");
        setReviewerApprovalToken("");
        setMessage("Document AI завершён и одобрен независимым reviewer.");
      } else {
        setMessage(`Document AI: ${statusLabel(result.analysis.status)}.`);
      }
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setAnalysisBusy(false);
    }
  }

  async function loadAnalysisDetail(analysisRunId: string) {
    if (!selectedWorkspaceId || interactionLocked) return;
    clearNotices();
    setAnalysisBusy(true);
    try {
      const detail = await requestJson<AnalysisRecord>(
        `${API_BASE}/workspaces/${encodeURIComponent(selectedWorkspaceId)}/document-ai/runs/${encodeURIComponent(analysisRunId)}?include_content=true`,
        undefined,
        "Не удалось загрузить Document AI run.",
      );
      setHistoryDetail(detail);
    } catch (caught) {
      setError(exceptionMessage(caught));
    } finally {
      setAnalysisBusy(false);
    }
  }

  function handleUploadChange(event: ChangeEvent<HTMLInputElement>) {
    setUploadFile(event.target.files?.[0] ?? null);
    clearNotices();
  }

  const extractionSources = [
    ...extractionChunks.map(makeChunkSource),
    ...extractionUnits.map(makeUnitSource),
  ];
  const ocrSources = ocrPages.map(makeOCRSource);
  const selectedExtractionRun = extractionRuns.find(
    (run) => run.id === selectedExtractionRunId,
  );
  const selectedOCRRun = ocrRuns.find((run) => run.id === selectedOCRRunId);
  const currentAnalysis = analysisExecution?.analysis ?? null;
  const approvalStage = analysisExecution?.approval_stage ?? null;
  const preflightCurrent =
    preflight?.specKey === analysisSpecKey ? preflight.result : null;

  return (
    <>
      <header className="topbar document-topbar">
        <div>
          <div className="eyebrow">P3-001 · Governed workspace</div>
          <h1>Документы</h1>
          <p>
            Локальная обработка, точный provenance и контролируемый AI Gateway.
          </p>
        </div>
        <label className="document-workspace-select">
          <span>Workspace</span>
          <select
            value={selectedWorkspaceId}
            onChange={(event) => selectWorkspace(event.target.value)}
            disabled={initialLoading || interactionLocked}
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
      </header>

      {error && (
        <div className="document-alert danger" role="alert">
          <strong>Операция остановлена</strong>
          <span>{error}</span>
        </div>
      )}
      {message && (
        <div className="document-alert success" role="status">
          <strong>Готово</strong>
          <span>{message}</span>
        </div>
      )}

      <section className="documents-layout">
        <aside className="document-registry-panel">
          <div className="panel-heading">
            <div>
              <span className="step">Workspace registry</span>
              <h2>Документы</h2>
            </div>
            <span className="document-count">{documents.length}</span>
          </div>

          <div className="document-upload-box">
            <input
              key={uploadInputKey}
              type="file"
              accept=".pdf,.docx,.xlsx,.txt,application/pdf,text/plain"
              onChange={handleUploadChange}
              disabled={!selectedWorkspaceId || interactionLocked}
            />
            <button
              className="primary"
              onClick={() => void uploadDocument()}
              disabled={
                !uploadFile || !selectedWorkspaceId || interactionLocked
              }
            >
              {busy ? "Операция…" : "Безопасно загрузить"}
            </button>
            <small>
              PDF, DOCX, XLSX или TXT. Classification назначает Workspace
              Policy.
            </small>
          </div>

          <div className="document-registry-list">
            {(initialLoading || registryLoading) && (
              <div className="document-empty">Загрузка registry…</div>
            )}
            {!initialLoading && !registryLoading && documents.length === 0 && (
              <div className="document-empty">
                В этом Workspace пока нет документов.
              </div>
            )}
            {documents.map((document) => (
              <button
                key={document.id}
                className={document.id === selectedDocumentId ? "active" : ""}
                aria-pressed={document.id === selectedDocumentId}
                disabled={interactionLocked}
                onClick={() => selectDocument(document.id)}
              >
                <span className={`document-format ${document.document_format}`}>
                  {document.document_format.toUpperCase()}
                </span>
                <span>
                  <strong>{document.original_filename}</strong>
                  <small>
                    {formatBytes(document.size_bytes)} ·{" "}
                    {document.classification}
                  </small>
                </span>
              </button>
            ))}
          </div>
        </aside>

        <div className="document-main-panel">
          {!selectedDocument ? (
            <div className="document-empty large">
              <strong>Выберите или загрузите документ</strong>
              <span>
                Derived content и AI context не формируются автоматически.
              </span>
            </div>
          ) : (
            <>
              <section className="document-detail-card">
                <div className="document-detail-heading">
                  <div>
                    <span
                      className={`document-format ${selectedDocument.document_format}`}
                    >
                      {selectedDocument.document_format.toUpperCase()}
                    </span>
                    <div>
                      <h2>{selectedDocument.original_filename}</h2>
                      <code>{selectedDocument.id}</code>
                    </div>
                  </div>
                  <button
                    className="document-delete"
                    onClick={() => void deleteDocument()}
                    disabled={interactionLocked}
                  >
                    Удалить
                  </button>
                </div>
                <div className="document-meta-grid">
                  <span>
                    <small>Classification</small>
                    <strong>{selectedDocument.classification}</strong>
                  </span>
                  <span>
                    <small>Storage</small>
                    <strong>{selectedDocument.storage_state}</strong>
                  </span>
                  <span>
                    <small>Размер</small>
                    <strong>{formatBytes(selectedDocument.size_bytes)}</strong>
                  </span>
                  <span>
                    <small>Загружен</small>
                    <strong>{formatDate(selectedDocument.created_at)}</strong>
                  </span>
                  <span className="wide">
                    <small>Content SHA-256</small>
                    <code>{selectedDocument.content_sha256}</code>
                  </span>
                </div>
              </section>

              <div
                className="document-tabs"
                role="tablist"
                aria-label="Documents workspace sections"
              >
                <button
                  role="tab"
                  aria-selected={workspaceSection === "processing"}
                  className={workspaceSection === "processing" ? "active" : ""}
                  onClick={() => setWorkspaceSection("processing")}
                >
                  Обработка и sources
                </button>
                <button
                  role="tab"
                  aria-selected={workspaceSection === "analysis"}
                  className={workspaceSection === "analysis" ? "active" : ""}
                  onClick={() => setWorkspaceSection("analysis")}
                >
                  Summary / Question <span>{selectedSources.length}</span>
                </button>
                <button
                  role="tab"
                  aria-selected={workspaceSection === "history"}
                  className={workspaceSection === "history" ? "active" : ""}
                  onClick={() => setWorkspaceSection("history")}
                >
                  История AI <span>{analysisRuns.length}</span>
                </button>
              </div>

              {workspaceSection === "processing" && (
                <div className="document-processing-stack" role="tabpanel">
                  <section className="document-process-card">
                    <div className="panel-heading">
                      <div>
                        <span className="step">Deterministic parser</span>
                        <h2>Extraction</h2>
                      </div>
                      <button
                        className="primary"
                        onClick={() => void startExtraction()}
                        disabled={interactionLocked}
                      >
                        Извлечь текст
                      </button>
                    </div>
                    <div className="document-run-controls">
                      <select
                        value={selectedExtractionRunId}
                        disabled={interactionLocked}
                        onChange={(event) =>
                          selectExtractionRun(event.target.value)
                        }
                      >
                        {extractionRuns.length === 0 && (
                          <option value="">Нет extraction runs</option>
                        )}
                        {extractionRuns.map((run) => (
                          <option key={run.id} value={run.id}>
                            {statusLabel(run.status)} ·{" "}
                            {formatDate(run.created_at)} · {run.parser}
                          </option>
                        ))}
                      </select>
                      <button
                        className="secondary"
                        onClick={() => void loadExtractionSources()}
                        disabled={
                          !selectedExtractionRunId ||
                          selectedExtractionRun?.status !== "completed" ||
                          derivedLoading
                        }
                      >
                        Preview units/chunks
                      </button>
                    </div>
                    {selectedExtractionRun && (
                      <div className="document-run-summary">
                        <span
                          className={`document-status ${statusTone(selectedExtractionRun.status)}`}
                        >
                          {statusLabel(selectedExtractionRun.status)}
                        </span>
                        <span>{selectedExtractionRun.unit_count} units</span>
                        <span>{selectedExtractionRun.chunk_count} chunks</span>
                        <span>
                          {selectedExtractionRun.total_characters.toLocaleString(
                            "ru-RU",
                          )}{" "}
                          знаков
                        </span>
                        {selectedExtractionRun.error_code && (
                          <strong>{selectedExtractionRun.error_code}</strong>
                        )}
                      </div>
                    )}
                    {extractionSources.length > 0 && (
                      <div className="document-source-grid">
                        {extractionSources.map((source) => (
                          <SourceCard
                            key={source.key}
                            source={source}
                            selected={selectedSources.some(
                              (item) => item.key === source.key,
                            )}
                            disabled={interactionLocked}
                            onToggle={toggleSource}
                          />
                        ))}
                      </div>
                    )}
                  </section>

                  <section className="document-process-card">
                    <div className="panel-heading">
                      <div>
                        <span className="step">Isolated local runtime</span>
                        <h2>PDF OCR</h2>
                      </div>
                      <button
                        className="primary"
                        onClick={() => void startOCR()}
                        disabled={
                          interactionLocked ||
                          selectedDocument.document_format !== "pdf"
                        }
                      >
                        Запустить OCR
                      </button>
                    </div>
                    {selectedDocument.document_format !== "pdf" ? (
                      <div className="document-gate-note">
                        OCR доступен только для PDF; для этого формата
                        используйте deterministic extraction.
                      </div>
                    ) : (
                      <>
                        <div className="document-ocr-options">
                          <label>
                            <span>Страницы</span>
                            <input
                              value={ocrPagesInput}
                              onChange={(event) =>
                                setOCRPagesInput(event.target.value)
                              }
                              placeholder="пусто = все, либо 1,3-5"
                            />
                          </label>
                          <label>
                            <span>Retention days</span>
                            <input
                              type="number"
                              min="1"
                              max="3650"
                              value={ocrRetentionDays}
                              onChange={(event) =>
                                setOCRRetentionDays(event.target.value)
                              }
                              placeholder="по classification"
                            />
                          </label>
                        </div>
                        <div className="document-run-controls">
                          <select
                            value={selectedOCRRunId}
                            disabled={interactionLocked}
                            onChange={(event) =>
                              selectOCRRun(event.target.value)
                            }
                          >
                            {ocrRuns.length === 0 && (
                              <option value="">Нет OCR runs</option>
                            )}
                            {ocrRuns.map((run) => (
                              <option key={run.id} value={run.id}>
                                {statusLabel(run.status)} ·{" "}
                                {formatDate(run.created_at)} ·{" "}
                                {run.selected_page_count} pages
                              </option>
                            ))}
                          </select>
                          <button
                            className="secondary"
                            onClick={() => void loadOCRSources()}
                            disabled={
                              !selectedOCRRunId ||
                              selectedOCRRun?.status !== "completed" ||
                              selectedOCRRun?.text_state !== "active" ||
                              derivedLoading
                            }
                          >
                            Preview OCR pages
                          </button>
                        </div>
                        {selectedOCRRun && (
                          <div className="document-run-summary">
                            <span
                              className={`document-status ${statusTone(selectedOCRRun.status)}`}
                            >
                              {statusLabel(selectedOCRRun.status)}
                            </span>
                            <span>
                              {selectedOCRRun.selected_page_count} pages
                            </span>
                            <span>
                              {selectedOCRRun.total_characters.toLocaleString(
                                "ru-RU",
                              )}{" "}
                              знаков
                            </span>
                            <span>text: {selectedOCRRun.text_state}</span>
                            <span>
                              до{" "}
                              {formatDate(selectedOCRRun.retention_expires_at)}
                            </span>
                            {selectedOCRRun.error_code && (
                              <strong>{selectedOCRRun.error_code}</strong>
                            )}
                          </div>
                        )}
                        {ocrSources.length > 0 && (
                          <div className="document-source-grid">
                            {ocrSources.map((source) => (
                              <SourceCard
                                key={source.key}
                                source={source}
                                selected={selectedSources.some(
                                  (item) => item.key === source.key,
                                )}
                                disabled={interactionLocked}
                                onToggle={toggleSource}
                              />
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </section>

                  <section className="document-selected-sources">
                    <div className="panel-heading">
                      <div>
                        <span className="step">Explicit context</span>
                        <h2>Выбранные persisted sources</h2>
                      </div>
                      <span className="document-count">
                        {selectedSources.length}/{MAX_SELECTED_SOURCES}
                      </span>
                    </div>
                    {selectedSources.length === 0 ? (
                      <div className="document-empty">
                        Ничего не выбрано. AI context остаётся пустым.
                      </div>
                    ) : (
                      <div className="document-selection-list">
                        {selectedSources.map((source) => (
                          <article key={source.key}>
                            <span>
                              <strong>{source.document_name}</strong>
                              <small>
                                {source.label} · {source.source_kind}
                              </small>
                            </span>
                            <button
                              onClick={() => toggleSource(source)}
                              disabled={interactionLocked}
                            >
                              Убрать
                            </button>
                          </article>
                        ))}
                      </div>
                    )}
                    <button
                      className="primary"
                      disabled={
                        selectedSources.length === 0 || interactionLocked
                      }
                      onClick={() => setWorkspaceSection("analysis")}
                    >
                      Перейти к AI preflight
                    </button>
                  </section>
                </div>
              )}

              {workspaceSection === "analysis" && (
                <div className="document-analysis-stack" role="tabpanel">
                  <section className="document-process-card">
                    <div className="panel-heading">
                      <div>
                        <span className="step">Step 1</span>
                        <h2>Workflow и модели</h2>
                      </div>
                    </div>
                    <div className="document-ai-form-grid">
                      <label>
                        <span>Workflow</span>
                        <select
                          value={workflow}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setWorkflow(
                              event.target.value as "summary" | "question",
                            )
                          }
                        >
                          <option value="summary">Summary</option>
                          <option value="question">Question</option>
                        </select>
                      </label>
                      <label>
                        <span>Primary model</span>
                        <select
                          value={primaryModelSlug}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setPrimaryModelSlug(event.target.value)
                          }
                        >
                          <option value="">Выберите модель</option>
                          {eligibleModels.map((model) => (
                            <option key={model.slug} value={model.slug}>
                              {model.display_name} · {model.provider}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        <span>Independent reviewer</span>
                        <select
                          value={reviewerModelSlug}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setReviewerModelSlug(event.target.value)
                          }
                        >
                          <option value="">Выберите reviewer</option>
                          {eligibleModels
                            .filter((model) => model.slug !== primaryModelSlug)
                            .map((model) => (
                              <option key={model.slug} value={model.slug}>
                                {model.display_name} · {model.provider}
                              </option>
                            ))}
                        </select>
                      </label>
                      <label>
                        <span>Primary output tokens</span>
                        <input
                          type="number"
                          min="1"
                          max="32768"
                          value={maxOutputTokens}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setMaxOutputTokens(event.target.value)
                          }
                        />
                      </label>
                      <label>
                        <span>Reviewer output tokens</span>
                        <input
                          type="number"
                          min="1"
                          max="8192"
                          value={reviewerMaxOutputTokens}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setReviewerMaxOutputTokens(event.target.value)
                          }
                        />
                      </label>
                      <label>
                        <span>Retention days</span>
                        <input
                          type="number"
                          min="1"
                          max="3650"
                          value={analysisRetentionDays}
                          disabled={interactionLocked}
                          onChange={(event) =>
                            setAnalysisRetentionDays(event.target.value)
                          }
                          placeholder="по classification"
                        />
                      </label>
                    </div>
                    {workflow === "question" && (
                      <label className="document-question-field">
                        <span>Доверенный вопрос пользователя</span>
                        <textarea
                          value={question}
                          disabled={interactionLocked}
                          onChange={(event) => setQuestion(event.target.value)}
                          maxLength={8000}
                          placeholder="Вопрос будет отделён от недоверенного document context."
                        />
                      </label>
                    )}
                    <div className="document-selection-summary">
                      <strong>
                        Explicit sources: {selectedSources.length}
                      </strong>
                      <span>
                        Автоматическое добавление соседних documents, runs или
                        pages запрещено.
                      </span>
                    </div>
                    <button
                      className="primary"
                      onClick={() => void runPreflight()}
                      disabled={
                        interactionLocked || selectedSources.length === 0
                      }
                    >
                      {analysisBusy ? "Проверка…" : "Выполнить preflight"}
                    </button>
                  </section>

                  {preflightCurrent && (
                    <section className="document-process-card">
                      <div className="panel-heading">
                        <div>
                          <span className="step">Step 2</span>
                          <h2>Context manifest и Runtime Policy</h2>
                        </div>
                        <span
                          className={`policy-classification-badge ${preflightCurrent.context.manifest.effective_classification}`}
                        >
                          {
                            preflightCurrent.context.manifest
                              .effective_classification
                          }
                        </span>
                      </div>
                      <div className="document-evidence-grid">
                        <span>
                          <small>Sources</small>
                          <strong>
                            {
                              preflightCurrent.context.manifest
                                .selected_source_count
                            }
                          </strong>
                        </span>
                        <span>
                          <small>Fragments</small>
                          <strong>
                            {preflightCurrent.context.manifest.fragment_count}
                          </strong>
                        </span>
                        <span>
                          <small>Characters</small>
                          <strong>
                            {preflightCurrent.context.manifest.total_source_characters.toLocaleString(
                              "ru-RU",
                            )}
                          </strong>
                        </span>
                        <span>
                          <small>Planned tokens</small>
                          <strong>
                            {preflightCurrent.context.manifest.budget.planned_total_tokens.toLocaleString(
                              "ru-RU",
                            )}
                          </strong>
                        </span>
                        <span>
                          <small>Remaining</small>
                          <strong>
                            {preflightCurrent.context.manifest.budget.remaining_tokens.toLocaleString(
                              "ru-RU",
                            )}
                          </strong>
                        </span>
                        <span>
                          <small>Injection warnings</small>
                          <strong>
                            {
                              preflightCurrent.context.manifest
                                .suspicious_source_count
                            }
                          </strong>
                        </span>
                      </div>
                      {preflightCurrent.context.manifest
                        .suspicious_source_count > 0 && (
                        <div className="document-alert warning">
                          <strong>Prompt-injection warning</strong>
                          <span>
                            Локальный scanner нашёл подозрительные инструкции.
                            Это предупреждение, а не доказательство безопасности
                            или атаки.
                          </span>
                        </div>
                      )}
                      <div className="document-stage-grid">
                        {preflightCurrent.stages.map((stage) => (
                          <article key={stage.stage}>
                            <strong>{stage.stage}</strong>
                            <span>
                              {stage.provider} · {stage.provider_trust}
                            </span>
                            <span>Policy: {stage.runtime_policy.action}</span>
                            <small>
                              {stage.runtime_policy.reason_codes.join(", ") ||
                                "Без reason codes"}
                            </small>
                            {stage.external_provider_warning && (
                              <small className="document-stage-warning">
                                {stage.external_provider_warning}
                              </small>
                            )}
                          </article>
                        ))}
                      </div>
                      <details className="document-evidence-details">
                        <summary>Manifest citations и scans</summary>
                        <pre>
                          {JSON.stringify(
                            {
                              selection_fingerprint:
                                preflightCurrent.context.manifest
                                  .selection_fingerprint,
                              context_sha256:
                                preflightCurrent.context.manifest
                                  .context_sha256,
                              manifest_fingerprint:
                                preflightCurrent.context.manifest
                                  .manifest_fingerprint,
                              budget: preflightCurrent.context.manifest.budget,
                              citations:
                                preflightCurrent.context.manifest.citations,
                              source_scans:
                                preflightCurrent.context.manifest.source_scans,
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>

                      {preflightCurrent.external_provider_warning_required && (
                        <label className="document-external-warning">
                          <input
                            type="checkbox"
                            checked={externalAcknowledged}
                            disabled={interactionLocked}
                            onChange={(event) =>
                              setExternalAcknowledged(event.target.checked)
                            }
                          />
                          <span>
                            <strong>
                              Я подтверждаю передачу выбранного document context
                              внешним providers и generated primary answer
                              внешнему reviewer
                            </strong>
                            <small>
                              Primary получает только явно выбранные persisted
                              sources. Reviewer получает этот же context,
                              citation IDs и сгенерированный primary answer.
                              Обе стадии проходят Runtime Policy и AI Gateway.
                            </small>
                          </span>
                        </label>
                      )}
                      <button
                        className="primary"
                        onClick={() => void executeAnalysis()}
                        disabled={
                          interactionLocked ||
                          (preflightCurrent.external_provider_warning_required &&
                            !externalAcknowledged)
                        }
                      >
                        {analysisBusy
                          ? "Выполнение…"
                          : "Запустить governed analysis"}
                      </button>
                    </section>
                  )}

                  {approvalStage && currentAnalysis && (
                    <section className="document-process-card document-approval-required-card">
                      <div className="panel-heading">
                        <div>
                          <span className="step">Step 3 · Human Control</span>
                          <h2>
                            {approvalStage === "primary"
                              ? "Primary approval"
                              : "Reviewer approval"}
                          </h2>
                        </div>
                        <span className="document-status warning">
                          Approval required
                        </span>
                      </div>
                      <p>
                        Exact-scope approval уже создан. Откройте Approvals,
                        одобрите его и сохраните показанный один раз token.
                      </p>
                      <button className="secondary" onClick={onOpenApprovals}>
                        Открыть Approvals в новой вкладке
                      </button>
                      <div className="document-approval-grid">
                        <label>
                          <span>Approval ID</span>
                          <input
                            value={
                              approvalStage === "primary"
                                ? primaryApprovalId
                                : reviewerApprovalId
                            }
                            disabled={interactionLocked}
                            onChange={(event) =>
                              approvalStage === "primary"
                                ? setPrimaryApprovalId(event.target.value)
                                : setReviewerApprovalId(event.target.value)
                            }
                          />
                        </label>
                        <label>
                          <span>One-time token</span>
                          <input
                            type="password"
                            autoComplete="off"
                            value={
                              approvalStage === "primary"
                                ? primaryApprovalToken
                                : reviewerApprovalToken
                            }
                            disabled={interactionLocked}
                            onChange={(event) =>
                              approvalStage === "primary"
                                ? setPrimaryApprovalToken(event.target.value)
                                : setReviewerApprovalToken(event.target.value)
                            }
                          />
                        </label>
                      </div>
                      <button
                        className="primary"
                        onClick={() => void executeAnalysis()}
                        disabled={interactionLocked}
                      >
                        {analysisBusy
                          ? "Проверка approval…"
                          : `Продолжить ${approvalStage}`}
                      </button>
                    </section>
                  )}

                  {currentAnalysis && (
                    <AnalysisView analysis={currentAnalysis} />
                  )}
                </div>
              )}

              {workspaceSection === "history" && (
                <div className="document-history-layout" role="tabpanel">
                  <section className="document-history-list">
                    <div className="panel-heading">
                      <div>
                        <span className="step">Persisted evidence</span>
                        <h2>Document AI runs</h2>
                      </div>
                      <button
                        className="secondary"
                        disabled={interactionLocked}
                        onClick={() => {
                          setHistoryDetail(null);
                          setAnalysisExecution(null);
                          setAnalysisRevision((value) => value + 1);
                        }}
                      >
                        Обновить
                      </button>
                    </div>
                    {analysisRuns.length === 0 && (
                      <div className="document-empty">AI runs отсутствуют.</div>
                    )}
                    {analysisRuns.map((run) => (
                      <button
                        key={run.id}
                        className={historyDetail?.id === run.id ? "active" : ""}
                        disabled={interactionLocked}
                        onClick={() => void loadAnalysisDetail(run.id)}
                      >
                        <span>
                          <strong>
                            {run.workflow === "summary"
                              ? "Summary"
                              : "Question"}
                          </strong>
                          <small>{formatDate(run.created_at)}</small>
                        </span>
                        <span
                          className={`document-status ${statusTone(run.status)}`}
                        >
                          {statusLabel(run.status)}
                        </span>
                      </button>
                    ))}
                  </section>
                  <div>
                    {historyDetail ? (
                      <AnalysisView analysis={historyDetail} />
                    ) : (
                      <div className="document-empty large">
                        <strong>Выберите AI run</strong>
                        <span>
                          Raw content возвращается только по явному
                          include_content запросу.
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </>
  );
}
