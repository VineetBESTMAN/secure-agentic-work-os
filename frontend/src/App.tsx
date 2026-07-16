import { FormEvent, useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Bot,
  CheckCircle2,
  ClipboardList,
  Database,
  DollarSign,
  Eye,
  FileUp,
  Building2,
  Plug,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  XCircle,
  UserPlus,
} from "lucide-react";

type User = {
  user_id: string;
  email: string;
  display_name: string;
  organization_id: string;
  organization_slug: string;
  organization_name: string;
  membership_id: string;
  role: "admin" | "manager" | "employee";
  scopes: string[];
};

type OrganizationSummary = {
  organization_id: string;
  slug: string;
  name: string;
  membership_id: string;
  role: "admin" | "manager" | "employee";
  scopes: string[];
  status: "active" | "suspended";
};

type OrganizationMember = {
  membership_id: string;
  organization_id: string;
  user_id: string;
  email: string;
  display_name: string;
  role: "admin" | "manager" | "employee";
  scopes: string[];
  status: "active" | "suspended";
};

type InvitationRecord = {
  invitation_id: string;
  email: string;
  role: "admin" | "manager" | "employee";
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  invitation_token: string | null;
};

type OIDCProvider = {
  provider_id: string;
  name: string;
  issuer_url: string;
  client_id: string;
  scopes: string[];
  enabled: boolean;
};

type DocumentRecord = {
  document_id: string;
  title: string;
  filename: string;
  classification: string;
  owner_team: string;
  summary: string;
  unsafe: boolean;
  unsafe_reasons: string[];
  chunk_count: number;
  created_at: string | null;
};

type DocumentDetail = DocumentRecord & {
  chunks: {
    chunk_id: string;
    chunk_index: number;
    text: string;
  }[];
};

type RagAnswer = {
  answer: string;
  citations: {
    document_id: string;
    title: string;
    excerpt: string;
    chunk_id: string | null;
    score: number | null;
  }[];
  generation_mode: "openai" | "deterministic";
  model: string;
  grounded: boolean;
  fallback_reason: string | null;
};

type ModelGatewayStatus = {
  provider: "openai" | "deterministic";
  model: string;
  configured: boolean;
  grounded_answers_enabled: boolean;
  llm_planner_enabled: boolean;
  max_input_tokens: number;
  max_output_tokens: number;
  timeout_seconds: number;
  max_retries: number;
};

type ApprovalRecord = {
  approval_id: string;
  action_id: string;
  requested_by: string;
  status: "pending" | "approved" | "rejected";
  reviewed_by: string | null;
  execution_id: string | null;
  arguments_hash: string | null;
};

type AuditEvent = {
  event_id: string;
  actor_id: string;
  event_type: string;
  detail: Record<string, unknown>;
  timestamp: string;
};

type ConnectorRecord = {
  provider: string;
  display_name: string;
  configured: boolean;
  status: "not_configured" | "ready" | "connected" | "error" | "disconnected";
  connector_id: string | null;
  account_label: string | null;
  connected_at: string | null;
  expires_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  scopes: string[];
  resources: string[];
  actions: string[];
};

type ConnectorSyncState = {
  sync_state_id: string;
  connector_id: string;
  provider: string;
  resource: string;
  status: "idle" | "pending" | "running" | "completed" | "failed";
  items_seen: number;
  items_changed: number;
  has_cursor: boolean;
  last_started_at: string | null;
  last_completed_at: string | null;
  last_error: string | null;
};

type WebhookSubscription = {
  subscription_id: string;
  connector_id: string;
  provider: string;
  resource: string;
  target: string | null;
  remote_id: string | null;
  registration_mode: "manual" | "remote";
  status: "active" | "revoked" | "expired";
  callback_url: string;
  expires_at: string | null;
  created_at: string | null;
  secret: string | null;
};

type GoogleDriveFileRecord = {
  file_id: string;
  name: string;
  mime_type: string;
  modified_time: string | null;
  size: number | null;
  web_view_link: string | null;
  importable: boolean;
};

type PolicyRecord = {
  policy_id: string;
  name: string;
  description: string;
  rule_type: string;
  effect: string;
  conditions: Record<string, unknown>;
  enabled: boolean;
};

type JobRecord = {
  job_id: string;
  job_type: string;
  status: "queued" | "running" | "completed" | "failed";
  detail: Record<string, unknown>;
  result: Record<string, unknown>;
};

type AsyncJobResponse = {
  job: JobRecord;
  message: string;
};

type AgentWorkflowRecord = {
  workflow_id: string;
  prompt: string;
  requested_by: string;
  status:
    | "planned"
    | "running"
    | "waiting_for_approval"
    | "completed"
    | "blocked"
    | "failed"
    | "cancelled";
  plan: {
    summary: string;
    planner_mode: "openai" | "deterministic";
    model: string;
    validated: boolean;
    fallback_reason: string | null;
    actions: {
      action_id: string;
      action_type: string;
      description: string;
      requires_approval: boolean;
      scope: string;
      arguments: Record<string, unknown>;
    }[];
  };
  actions: {
    action_instance_id: string;
    sequence: number;
    action_type: string;
    tool_name: string;
    description: string;
    required_scope: string;
    requires_approval: boolean;
    status:
      | "pending"
      | "running"
      | "waiting_for_approval"
      | "completed"
      | "blocked"
      | "failed"
      | "cancelled"
      | "skipped";
    attempt_count: number;
    max_attempts: number;
    result: Record<string, unknown>;
    execution_id: string | null;
    approval_id: string | null;
    error: string | null;
  }[];
  current_action_index: number;
  last_error: string | null;
};

type MCPToolDefinition = {
  name: string;
  description: string;
  required_scope: string;
  approval_required: boolean;
  side_effect: boolean;
  input_schema: Record<string, unknown>;
};

type MCPExecutionRecord = {
  execution_id: string;
  tool_name: string;
  requested_by: string;
  required_scope: string;
  arguments: Record<string, unknown>;
  arguments_hash: string;
  status: "running" | "pending_approval" | "completed" | "blocked" | "rejected" | "failed";
  approval_id: string | null;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
};

type RuntimeSummary = {
  window_hours: number;
  total_operations: number;
  completed_operations: number;
  failed_operations: number;
  blocked_operations: number;
  success_rate: number;
  average_latency_ms: number;
  p95_latency_ms: number;
  input_units: number;
  output_units: number;
  estimated_cost_usd: number;
  breakdown: {
    operation_type: string;
    provider: string;
    model: string;
    operations: number;
    completed: number;
    failed_or_blocked: number;
    average_latency_ms: number;
    estimated_cost_usd: number;
  }[];
  budgets: {
    budget_id: string;
    name: string;
    period: "daily" | "monthly";
    limit_usd: number;
    warning_percent: number;
    enabled: boolean;
    spent_usd: number;
    remaining_usd: number;
    utilization_percent: number;
    state: "ok" | "warning" | "exceeded";
    period_start: string;
    period_end: string;
  }[];
};

type RagEvaluationDataset = {
  dataset_id: string;
  name: string;
  description: string;
  document_ids: string[];
  top_k: number;
  minimum_score: number;
  created_by: string;
  case_count: number;
  created_at: string | null;
};

type RagEvaluationRun = {
  run_id: string;
  comparison_id: string;
  dataset_id: string;
  dataset_name: string;
  provider: "local" | "openai";
  model: string;
  status: "running" | "completed" | "failed" | "skipped";
  case_count: number;
  retrieval_accuracy: number;
  citation_correctness: number;
  groundedness: number;
  hallucination_rate: number;
  average_latency_ms: number;
  p95_latency_ms: number;
  index_latency_ms: number;
  error: string | null;
  created_at: string | null;
};

const MCP_ARGUMENT_TEMPLATES: Record<string, Record<string, unknown>> = {
  search_documents: { question: "What requires manager approval?" },
  create_task: {
    title: "Review client renewal",
    description: "Confirm the contract summary before follow-up.",
    due_date: "2026-07-21",
  },
  send_email: {
    to: "client@example.com",
    subject: "Renewal follow-up",
    body: "The approved summary is ready for review.",
  },
  create_calendar_event: {
    summary: "Renewal review",
    description: "Review the approved renewal summary.",
    start: "2026-07-20T10:00:00+05:30",
    end: "2026-07-20T10:30:00+05:30",
    timezone: "Asia/Kolkata",
    attendees: ["client@example.com"],
  },
  send_slack_message: {
    channel: "C0123456789",
    text: "The approved renewal summary is ready.",
  },
  create_github_issue: {
    repository: "owner/repository",
    title: "Review renewal automation",
    body: "Created after Work OS approval.",
    labels: ["work-os"],
  },
  create_jira_issue: {
    project_key: "OPS",
    summary: "Review renewal automation",
    description: "Created after Work OS approval.",
    issue_type: "Task",
  },
  create_notion_page: {
    parent_id: "replace-with-page-or-database-id",
    parent_type: "page_id",
    title: "Renewal review",
    content: "Created after Work OS approval.",
  },
  export_data: { classification: "internal", limit: 25 },
};

const DEFAULT_EVALUATION_CASES = JSON.stringify(
  [
    {
      question: "What fact should be retrieved from the selected document?",
      expected_document_ids: ["replace-with-document-id"],
      expected_chunk_ids: [],
      expected_facts: ["replace with an evidence phrase from the document"],
      reference_answer: "Replace with the expected grounded answer.",
      unanswerable: false,
    },
    {
      question: "Ask a deliberately unanswerable control question.",
      expected_document_ids: [],
      expected_chunk_ids: [],
      expected_facts: [],
      reference_answer: "",
      unanswerable: true,
    },
  ],
  null,
  2,
);

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
let refreshPromise: Promise<string> | null = null;

export default function App() {
  const [email, setEmail] = useState("admin@demo.local");
  const [password, setPassword] = useState("demo-password");
  const [organizationSlug, setOrganizationSlug] = useState("");
  const [token, setToken] = useState(() => localStorage.getItem("workos_token") || "");
  const [refreshToken, setRefreshToken] = useState(
    () => localStorage.getItem("workos_refresh_token") || "",
  );
  const [user, setUser] = useState<User | null>(() => {
    const stored = localStorage.getItem("workos_user");
    return stored ? JSON.parse(stored) : null;
  });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  const [members, setMembers] = useState<OrganizationMember[]>([]);
  const [invitations, setInvitations] = useState<InvitationRecord[]>([]);
  const [oidcProviders, setOidcProviders] = useState<OIDCProvider[]>([]);
  const [newOrganizationName, setNewOrganizationName] = useState("");
  const [newOrganizationSlug, setNewOrganizationSlug] = useState("");
  const [invitationEmail, setInvitationEmail] = useState("");
  const [invitationRole, setInvitationRole] = useState<"admin" | "manager" | "employee">(
    "employee",
  );
  const [latestInvitationToken, setLatestInvitationToken] = useState("");
  const [oidcName, setOidcName] = useState("");
  const [oidcIssuer, setOidcIssuer] = useState("");
  const [oidcClientId, setOidcClientId] = useState("");
  const [oidcClientSecret, setOidcClientSecret] = useState("");
  const [unsafeDocuments, setUnsafeDocuments] = useState<DocumentRecord[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<DocumentDetail | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [connectors, setConnectors] = useState<ConnectorRecord[]>([]);
  const [connectorSyncStates, setConnectorSyncStates] = useState<ConnectorSyncState[]>([]);
  const [webhookSubscriptions, setWebhookSubscriptions] = useState<WebhookSubscription[]>([]);
  const [latestWebhookSetup, setLatestWebhookSetup] = useState<WebhookSubscription | null>(null);
  const [driveFiles, setDriveFiles] = useState<GoogleDriveFileRecord[]>([]);
  const [driveNextPageToken, setDriveNextPageToken] = useState<string | null>(null);
  const [driveSearch, setDriveSearch] = useState("");
  const [selectedDriveFileIds, setSelectedDriveFileIds] = useState<string[]>([]);
  const [policies, setPolicies] = useState<PolicyRecord[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [workflows, setWorkflows] = useState<AgentWorkflowRecord[]>([]);
  const [mcpTools, setMcpTools] = useState<MCPToolDefinition[]>([]);
  const [mcpExecutions, setMcpExecutions] = useState<MCPExecutionRecord[]>([]);
  const [runtimeSummary, setRuntimeSummary] = useState<RuntimeSummary | null>(null);
  const [modelGateway, setModelGateway] = useState<ModelGatewayStatus | null>(null);
  const [evaluationDatasets, setEvaluationDatasets] = useState<RagEvaluationDataset[]>([]);
  const [evaluationRuns, setEvaluationRuns] = useState<RagEvaluationRun[]>([]);
  const [evaluationName, setEvaluationName] = useState("RAG quality baseline");
  const [evaluationDescription, setEvaluationDescription] = useState(
    "Curated retrieval, citation, groundedness, and hallucination regression cases.",
  );
  const [evaluationDocumentIds, setEvaluationDocumentIds] = useState("");
  const [evaluationTopK, setEvaluationTopK] = useState(3);
  const [evaluationMinimumScore, setEvaluationMinimumScore] = useState(0);
  const [evaluationCases, setEvaluationCases] = useState(DEFAULT_EVALUATION_CASES);
  const [selectedMcpTool, setSelectedMcpTool] = useState("search_documents");
  const [mcpArguments, setMcpArguments] = useState(
    JSON.stringify(MCP_ARGUMENT_TEMPLATES.search_documents, null, 2),
  );
  const [query, setQuery] = useState("What does this document say about urgent work?");
  const [agentPrompt, setAgentPrompt] = useState("Find urgent client work and send a reply");
  const [connectorContent, setConnectorContent] = useState(
    "Google Drive note: client renewal needs a follow-up task this week.",
  );
  const [answer, setAnswer] = useState<RagAnswer | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [classification, setClassification] = useState("internal");
  const [ownerTeam, setOwnerTeam] = useState("general");
  const [editTitle, setEditTitle] = useState("");
  const [editClassification, setEditClassification] = useState("internal");
  const [editOwnerTeam, setEditOwnerTeam] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  function persistSession(body: {
    access_token: string;
    refresh_token: string;
    user: User;
  }) {
    setToken(body.access_token);
    setRefreshToken(body.refresh_token);
    setUser(body.user);
    localStorage.setItem("workos_token", body.access_token);
    localStorage.setItem("workos_refresh_token", body.refresh_token);
    localStorage.setItem("workos_user", JSON.stringify(body.user));
  }

  function clearSession() {
    setToken("");
    setRefreshToken("");
    setUser(null);
    setOrganizations([]);
    setMembers([]);
    setInvitations([]);
    setOidcProviders([]);
    setConnectorSyncStates([]);
    setWebhookSubscriptions([]);
    setLatestWebhookSetup(null);
    setModelGateway(null);
    localStorage.removeItem("workos_token");
    localStorage.removeItem("workos_refresh_token");
    localStorage.removeItem("workos_user");
  }

  async function renewAccessToken(): Promise<string> {
    if (!refreshPromise) {
      refreshPromise = (async () => {
        const storedRefresh = localStorage.getItem("workos_refresh_token");
        if (!storedRefresh) throw new Error("Your session has expired. Sign in again.");
        const response = await fetch(`${API_BASE}/api/auth/refresh`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: storedRefresh }),
        });
        if (!response.ok) {
          clearSession();
          throw new Error("Your session has expired. Sign in again.");
        }
        const body = await response.json();
        persistSession(body);
        return body.access_token as string;
      })().finally(() => {
        refreshPromise = null;
      });
    }
    return refreshPromise;
  }

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const request = (accessToken: string) => fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });
    let response = await request(token);
    if (response.status === 401 && refreshToken) {
      response = await request(await renewAccessToken());
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(body.detail || response.statusText);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return response.json();
  }

  async function refreshAll() {
    if (!token) return;
    const [
      documentData,
      approvalData,
      connectorData,
      connectorSyncData,
      webhookData,
      toolData,
      executionData,
      organizationData,
      modelGatewayData,
    ] = await Promise.all([
      api<DocumentRecord[]>("/api/documents/library"),
      api<ApprovalRecord[]>("/api/approvals"),
      api<ConnectorRecord[]>("/api/connectors"),
      api<ConnectorSyncState[]>("/api/connectors/sync-states"),
      api<WebhookSubscription[]>("/api/connectors/webhook-subscriptions"),
      api<MCPToolDefinition[]>("/api/mcp/tools"),
      api<MCPExecutionRecord[]>("/api/mcp/executions"),
      api<OrganizationSummary[]>("/api/organizations"),
      api<ModelGatewayStatus>("/api/models/status"),
    ]);
    setDocuments(documentData);
    setApprovals(approvalData);
    setConnectors(connectorData);
    setConnectorSyncStates(connectorSyncData);
    setWebhookSubscriptions(webhookData);
    setMcpTools(toolData);
    setMcpExecutions(executionData);
    setOrganizations(organizationData);
    setModelGateway(modelGatewayData);
    setWorkflows(await api<AgentWorkflowRecord[]>("/api/agent/workflows"));
    if (user?.scopes.includes("audit:read")) {
      setAuditEvents(await api<AuditEvent[]>("/api/audit/events"));
    }
    if (user?.role === "admin" || user?.role === "manager") {
      setUnsafeDocuments(await api<DocumentRecord[]>("/api/documents/unsafe"));
      setPolicies(await api<PolicyRecord[]>("/api/policies"));
      setJobs(await api<JobRecord[]>("/api/jobs"));
      setRuntimeSummary(await api<RuntimeSummary>("/api/observability/summary?hours=24"));
      setEvaluationDatasets(
        await api<RagEvaluationDataset[]>("/api/rag-evaluations/datasets"),
      );
      setEvaluationRuns(await api<RagEvaluationRun[]>("/api/rag-evaluations/runs?limit=50"));
      setMembers(await api<OrganizationMember[]>("/api/organizations/current/members"));
      setInvitations(
        await api<InvitationRecord[]>("/api/organizations/current/invitations"),
      );
      if (user?.role === "admin") {
        setOidcProviders(
          await api<OIDCProvider[]>("/api/organizations/current/oidc-providers"),
        );
      }
    } else {
      setMembers([]);
      setInvitations([]);
      setOidcProviders([]);
    }
  }

  async function watchJob(jobId: string, onCompleted?: () => Promise<void>) {
    for (let attempt = 0; attempt < 300; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      try {
        const job = await api<JobRecord>(`/api/jobs/${jobId}`);
        setJobs((current) => [job, ...current.filter((item) => item.job_id !== job.job_id)]);
        if (job.status === "completed") {
          setMessage(String(job.result.message || `Job ${job.job_id} completed.`));
          await refreshAll();
          if (onCompleted) await onCompleted();
          return;
        }
        if (job.status === "failed") {
          setMessage(String(job.result.error || `Job ${job.job_id} failed.`));
          await refreshAll();
          return;
        }
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "Could not refresh background job");
        return;
      }
    }
    setMessage(`Job ${jobId} is still running. Use Refresh to check it later.`);
  }

  useEffect(() => {
    refreshAll().catch((error) => setMessage(error.message));
  }, [token]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          organization_slug: organizationSlug.trim() || null,
        }),
      });
      if (!response.ok) throw new Error("Login failed");
      const body = await response.json();
      persistSession(body);
      setMessage(`Signed in to ${body.user.organization_name} as ${body.user.email}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  async function uploadDocument(event: FormEvent) {
    event.preventDefault();
    if (!selectedFile) {
      setMessage("Choose a file first.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("classification", classification);
      formData.append("owner_team", ownerTeam);
      const result = await api<AsyncJobResponse>("/api/documents/upload/async", {
        method: "POST",
        body: formData,
      });
      setJobs((current) => [result.job, ...current]);
      setMessage(`${result.message} Job: ${result.job.job_id}`);
      setSelectedFile(null);
      void watchJob(result.job.job_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function askQuestion(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const result = await api<RagAnswer>("/api/documents/query", {
        method: "POST",
        body: JSON.stringify({ question: query }),
      });
      setAnswer(result);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Query failed");
    } finally {
      setBusy(false);
    }
  }

  async function viewDocument(documentId: string) {
    setBusy(true);
    try {
      const detail = await api<DocumentDetail>(`/api/documents/${documentId}`);
      setSelectedDocument(detail);
      setEditTitle(detail.title);
      setEditClassification(detail.classification);
      setEditOwnerTeam(detail.owner_team);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load document");
    } finally {
      setBusy(false);
    }
  }

  async function updateDocument(event: FormEvent) {
    event.preventDefault();
    if (!selectedDocument) return;
    setBusy(true);
    try {
      const updated = await api<DocumentRecord>(`/api/documents/${selectedDocument.document_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: editTitle,
          classification: editClassification,
          owner_team: editOwnerTeam,
        }),
      });
      setMessage(`Updated ${updated.title}`);
      await refreshAll();
      await viewDocument(updated.document_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function reindexDocument(documentId: string) {
    setBusy(true);
    try {
      const result = await api<AsyncJobResponse>(
        `/api/documents/${documentId}/reindex-async`,
        { method: "POST" },
      );
      setJobs((current) => [result.job, ...current]);
      setMessage(`${result.message} Job: ${result.job.job_id}`);
      void watchJob(result.job.job_id, () => viewDocument(documentId));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Reindex failed");
    } finally {
      setBusy(false);
    }
  }

  async function deleteDocument(documentId: string) {
    const confirmed = window.confirm("Delete this document and its chunks?");
    if (!confirmed) return;
    setBusy(true);
    try {
      await api<void>(`/api/documents/${documentId}`, { method: "DELETE" });
      setSelectedDocument(null);
      setMessage("Document deleted.");
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(approvalId: string, approved: boolean) {
    setBusy(true);
    try {
      await api(`/api/approvals/${approvalId}/decision`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  }

  function chooseMcpTool(toolName: string) {
    setSelectedMcpTool(toolName);
    setMcpArguments(JSON.stringify(MCP_ARGUMENT_TEMPLATES[toolName] || {}, null, 2));
  }

  async function runMcpTool(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const argumentsPayload = JSON.parse(mcpArguments) as Record<string, unknown>;
      const execution = await api<MCPExecutionRecord>("/api/mcp/executions", {
        method: "POST",
        body: JSON.stringify({
          tool_name: selectedMcpTool,
          arguments: argumentsPayload,
        }),
      });
      const nextStep =
        execution.status === "pending_approval"
          ? " Switch accounts and approve it as a different manager or admin."
          : "";
      setMessage(
        `${execution.tool_name} execution ${execution.execution_id} is ${execution.status}.${nextStep}`,
      );
      await refreshAll();
    } catch (error) {
      const detail = error instanceof SyntaxError ? "Arguments must be valid JSON." : String(error);
      setMessage(detail);
    } finally {
      setBusy(false);
    }
  }

  async function authorizeConnector(provider: string) {
    setBusy(true);
    try {
      const result = await api<{
        authorization_url: string | null;
        message: string;
      }>(`/api/connectors/${provider}/authorize`, { method: "POST" });
      setMessage(result.message);
      if (result.authorization_url) {
        window.open(result.authorization_url, "_blank", "noopener,noreferrer");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Connector failed");
    } finally {
      setBusy(false);
    }
  }

  async function syncConnector(connector: ConnectorRecord) {
    setBusy(true);
    try {
      const result = await api<{
        job: JobRecord;
        states: ConnectorSyncState[];
      }>(`/api/connectors/${connector.provider}/sync`, {
        method: "POST",
        body: JSON.stringify({ resources: connector.resources }),
      });
      setConnectorSyncStates((current) => [
        ...current.filter((state) => state.connector_id !== connector.connector_id),
        ...result.states,
      ]);
      setMessage(
        `${connector.display_name} synced ${String(result.job.result.items_changed || 0)} changed items.`,
      );
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Connector sync failed");
    } finally {
      setBusy(false);
    }
  }

  async function disconnectConnector(connector: ConnectorRecord) {
    if (!window.confirm(`Disconnect ${connector.display_name} and revoke its stored credentials?`)) {
      return;
    }
    setBusy(true);
    try {
      const result = await api<{ message: string }>(`/api/connectors/${connector.provider}`, {
        method: "DELETE",
      });
      setMessage(result.message);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Connector disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  async function createWebhookSetup(connector: ConnectorRecord) {
    const resource = connector.resources[0];
    if (!resource) {
      setMessage(`${connector.display_name} has no webhook-capable resource.`);
      return;
    }
    setBusy(true);
    try {
      const setup = await api<WebhookSubscription>(
        `/api/connectors/${connector.provider}/webhook-subscriptions`,
        {
          method: "POST",
          body: JSON.stringify({ resource, register_remote: false }),
        },
      );
      setLatestWebhookSetup(setup);
      setWebhookSubscriptions((current) => [setup, ...current]);
      setMessage(
        `Webhook endpoint created for ${connector.display_name}. Copy the one-time secret now.`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Webhook setup failed");
    } finally {
      setBusy(false);
    }
  }

  async function importGoogleDriveNote() {
    setBusy(true);
    try {
      const result = await api<AsyncJobResponse>("/api/connectors/import/async", {
        method: "POST",
        body: JSON.stringify({
          provider: "google",
          items: [
            {
              filename: "google-drive-note.txt",
              content: connectorContent,
              classification: "internal",
              owner_team: "workspace",
            },
          ],
        }),
      });
      setJobs((current) => [result.job, ...current]);
      setMessage(`${result.message} Job: ${result.job.job_id}`);
      void watchJob(result.job.job_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  async function loadGoogleDriveFiles(pageToken: string | null = null) {
    setBusy(true);
    try {
      const params = new URLSearchParams({ page_size: "10" });
      if (driveSearch.trim()) {
        params.set("search", driveSearch.trim());
      }
      if (pageToken) {
        params.set("page_token", pageToken);
      }
      const result = await api<{
        files: GoogleDriveFileRecord[];
        next_page_token: string | null;
      }>(`/api/connectors/google/drive/files?${params.toString()}`);

      setDriveFiles((current) => (pageToken ? [...current, ...result.files] : result.files));
      setDriveNextPageToken(result.next_page_token);
      if (!pageToken) {
        setSelectedDriveFileIds([]);
      }
      setMessage(`Loaded ${result.files.length} Google Drive files.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Drive file load failed");
    } finally {
      setBusy(false);
    }
  }

  function toggleDriveFile(fileId: string) {
    setSelectedDriveFileIds((current) =>
      current.includes(fileId)
        ? current.filter((selectedId) => selectedId !== fileId)
        : [...current, fileId],
    );
  }

  async function importSelectedGoogleDriveFiles() {
    if (selectedDriveFileIds.length === 0) {
      setMessage("Select at least one Google Drive file first.");
      return;
    }

    setBusy(true);
    try {
      const result = await api<{
        job: JobRecord;
        imported_documents: DocumentRecord[];
      }>("/api/connectors/google/drive/import", {
        method: "POST",
        body: JSON.stringify({
          file_ids: selectedDriveFileIds,
          classification: "internal",
          owner_team: "workspace",
        }),
      });
      setMessage(
        `Imported ${result.imported_documents.length} real Google Drive files through ${result.job.job_id}.`,
      );
      setSelectedDriveFileIds([]);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Drive import failed");
    } finally {
      setBusy(false);
    }
  }

  async function createAgentWorkflow(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const workflow = await api<AgentWorkflowRecord>("/api/agent/workflows", {
        method: "POST",
        body: JSON.stringify({ prompt: agentPrompt }),
      });
      setMessage(`Workflow ${workflow.workflow_id} is ${workflow.status}.`);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Workflow failed");
    } finally {
      setBusy(false);
    }
  }

  async function runWorkflowCommand(
    workflowId: string,
    command: "resume" | "retry" | "cancel",
  ) {
    setBusy(true);
    try {
      const workflow = await api<AgentWorkflowRecord>(
        `/api/agent/workflows/${workflowId}/${command}`,
        { method: "POST" },
      );
      setWorkflows((current) =>
        current.map((item) => (item.workflow_id === workflowId ? workflow : item)),
      );
      setMessage(`Workflow ${workflowId} is ${workflow.status}.`);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Could not ${command} workflow`);
    } finally {
      setBusy(false);
    }
  }

  async function createEvaluationDataset(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const cases = JSON.parse(evaluationCases) as unknown;
      if (!Array.isArray(cases)) {
        throw new SyntaxError("Evaluation cases must be a JSON array.");
      }
      const documentIds = evaluationDocumentIds
        .split(/[\n,]/)
        .map((value) => value.trim())
        .filter(Boolean);
      const dataset = await api<RagEvaluationDataset>("/api/rag-evaluations/datasets", {
        method: "POST",
        body: JSON.stringify({
          name: evaluationName,
          description: evaluationDescription,
          document_ids: documentIds,
          top_k: evaluationTopK,
          minimum_score: evaluationMinimumScore,
          cases,
        }),
      });
      setMessage(`Created evaluation dataset ${dataset.name} with ${dataset.case_count} cases.`);
      await refreshAll();
    } catch (error) {
      setMessage(
        error instanceof SyntaxError
          ? error.message
          : error instanceof Error
            ? error.message
            : "Could not create evaluation dataset",
      );
    } finally {
      setBusy(false);
    }
  }

  async function runEvaluation(datasetId: string) {
    setBusy(true);
    setMessage("");
    try {
      const comparison = await api<{
        comparison_id: string;
        runs: RagEvaluationRun[];
      }>(`/api/rag-evaluations/datasets/${datasetId}/runs`, {
        method: "POST",
        body: JSON.stringify({ providers: ["local", "openai"] }),
      });
      const outcomes = comparison.runs
        .map((run) => `${run.provider}: ${run.status}`)
        .join(", ");
      setMessage(`Evaluation ${comparison.comparison_id} completed (${outcomes}).`);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "RAG evaluation failed");
    } finally {
      setBusy(false);
    }
  }

  async function switchOrganization(organizationId: string) {
    if (!organizationId || organizationId === user?.organization_id) return;
    setBusy(true);
    try {
      const session = await api<{
        access_token: string;
        refresh_token: string;
        user: User;
      }>("/api/auth/switch-organization", {
        method: "POST",
        body: JSON.stringify({ organization_id: organizationId }),
      });
      persistSession(session);
      setSelectedDocument(null);
      setAnswer(null);
      setMessage(`Switched to ${session.user.organization_name}.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not switch organization");
    } finally {
      setBusy(false);
    }
  }

  async function createOrganization(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const organization = await api<OrganizationSummary>("/api/organizations", {
        method: "POST",
        body: JSON.stringify({
          name: newOrganizationName.trim(),
          slug: newOrganizationSlug.trim().toLowerCase(),
        }),
      });
      setNewOrganizationName("");
      setNewOrganizationSlug("");
      setOrganizations((current) => [...current, organization]);
      await switchOrganization(organization.organization_id);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not create organization");
    } finally {
      setBusy(false);
    }
  }

  async function inviteMember(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const invitation = await api<InvitationRecord>(
        "/api/organizations/current/invitations",
        {
          method: "POST",
          body: JSON.stringify({ email: invitationEmail, role: invitationRole }),
        },
      );
      setInvitations((current) => [invitation, ...current]);
      setLatestInvitationToken(invitation.invitation_token || "");
      setInvitationEmail("");
      setMessage(`Invitation created for ${invitation.email}. Share the token securely.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not create invitation");
    } finally {
      setBusy(false);
    }
  }

  async function toggleMemberStatus(member: OrganizationMember) {
    setBusy(true);
    try {
      const updated = await api<OrganizationMember>(
        `/api/organizations/current/members/${member.membership_id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            status: member.status === "active" ? "suspended" : "active",
          }),
        },
      );
      setMembers((current) =>
        current.map((item) =>
          item.membership_id === updated.membership_id ? updated : item,
        ),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update membership");
    } finally {
      setBusy(false);
    }
  }

  async function createOidcProvider(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const provider = await api<OIDCProvider>(
        "/api/organizations/current/oidc-providers",
        {
          method: "POST",
          body: JSON.stringify({
            name: oidcName,
            issuer_url: oidcIssuer,
            client_id: oidcClientId,
            client_secret: oidcClientSecret,
            scopes: ["openid", "email", "profile"],
          }),
        },
      );
      setOidcProviders((current) => [...current, provider]);
      setOidcName("");
      setOidcIssuer("");
      setOidcClientId("");
      setOidcClientSecret("");
      setMessage(`OIDC provider ${provider.name} configured.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not configure OIDC");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    try {
      if (token) {
        await api<void>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
      }
    } catch {
      // Local cleanup still prevents this browser from reusing the session.
    } finally {
      clearSession();
    }
  }

  const googleConnector = connectors.find((connector) => connector.provider === "google");
  const googleDriveReady = googleConnector?.status === "connected";
  const canManageConnectors = Boolean(user?.scopes.includes("connectors:manage"));
  const canSyncConnectors = Boolean(user?.scopes.includes("connectors:sync"));
  const selectedMcpDefinition = mcpTools.find((tool) => tool.name === selectedMcpTool);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div>
          <div className="brand">Secure Work OS</div>
          <div className="subtle">Local enterprise AI workspace</div>
        </div>

        <form onSubmit={login} className="login-form">
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <label>
            Organization slug (optional)
            <input
              value={organizationSlug}
              onChange={(event) => setOrganizationSlug(event.target.value)}
              placeholder="default"
            />
          </label>
          <button type="submit" disabled={busy}>
            <ShieldCheck size={16} />
            Sign in
          </button>
        </form>

        {user && (
          <div className="session">
            <strong>{user.email}</strong>
            <span>{user.display_name || user.role}</span>
            <label>
              Organization
              <select
                value={user.organization_id}
                disabled={busy}
                onChange={(event) => void switchOrganization(event.target.value)}
              >
                {organizations.map((organization) => (
                  <option
                    key={organization.organization_id}
                    value={organization.organization_id}
                    disabled={organization.status !== "active"}
                  >
                    {organization.name} · {organization.role}
                  </option>
                ))}
              </select>
            </label>
            <span>{user.role} · {user.organization_slug}</span>
            <button type="button" onClick={() => void logout()}>
              <XCircle size={16} />
              Sign out
            </button>
          </div>
        )}
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>Operations Console</h1>
            <p>{message || "Upload real files, query them, and review gated actions."}</p>
          </div>
          <button type="button" onClick={refreshAll} disabled={!token || busy}>
            <RefreshCw size={16} />
            Refresh
          </button>
        </header>

        {user && (
          <section className="panel identity-panel">
            <div className="panel-title">
              <Building2 size={18} />
              <h2>Organization & Identity</h2>
            </div>
            <p>
              Active tenant: <strong>{user.organization_name}</strong>. Roles, scopes,
              documents, workflows, connectors, evaluations, and audit data are isolated to
              this organization.
            </p>
            <form className="split three" onSubmit={createOrganization}>
              <label>
                New organization name
                <input
                  required
                  value={newOrganizationName}
                  onChange={(event) => setNewOrganizationName(event.target.value)}
                  placeholder="Acme Operations"
                />
              </label>
              <label>
                URL-safe slug
                <input
                  required
                  pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                  value={newOrganizationSlug}
                  onChange={(event) => setNewOrganizationSlug(event.target.value)}
                  placeholder="acme-operations"
                />
              </label>
              <button type="submit" disabled={busy}>
                <Building2 size={16} />
                Create and switch
              </button>
            </form>

            {(user.role === "admin" || user.role === "manager") && (
              <div className="columns">
                <div>
                  <h3>Members</h3>
                  <div className="item-list compact">
                    {members.map((member) => (
                      <article key={member.membership_id} className="item">
                        <strong>{member.display_name || member.email}</strong>
                        <span>
                          {member.email} · {member.role} · {member.status}
                        </span>
                        {user.role === "admin" && member.user_id !== user.user_id && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => void toggleMemberStatus(member)}
                          >
                            {member.status === "active" ? "Suspend" : "Reactivate"}
                          </button>
                        )}
                      </article>
                    ))}
                  </div>
                </div>
                <div>
                  <h3>Invitations</h3>
                  {user.role === "admin" && (
                    <form className="stack" onSubmit={inviteMember}>
                      <input
                        type="email"
                        required
                        value={invitationEmail}
                        onChange={(event) => setInvitationEmail(event.target.value)}
                        placeholder="person@company.com"
                      />
                      <select
                        value={invitationRole}
                        onChange={(event) =>
                          setInvitationRole(
                            event.target.value as "admin" | "manager" | "employee",
                          )
                        }
                      >
                        <option value="employee">Employee</option>
                        <option value="manager">Manager</option>
                        <option value="admin">Admin</option>
                      </select>
                      <button type="submit" disabled={busy}>
                        <UserPlus size={16} />
                        Create invitation
                      </button>
                    </form>
                  )}
                  {latestInvitationToken && (
                    <code className="invitation-token">{latestInvitationToken}</code>
                  )}
                  <div className="item-list compact">
                    {invitations.map((invitation) => (
                      <article key={invitation.invitation_id} className="item">
                        <strong>{invitation.email}</strong>
                        <span>
                          {invitation.role} · {invitation.status}
                        </span>
                      </article>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {user.role === "admin" && (
              <details>
                <summary>Optional OIDC / SSO</summary>
                <form className="split" onSubmit={createOidcProvider}>
                  <input
                    required
                    value={oidcName}
                    onChange={(event) => setOidcName(event.target.value)}
                    placeholder="Corporate Identity"
                  />
                  <input
                    required
                    type="url"
                    value={oidcIssuer}
                    onChange={(event) => setOidcIssuer(event.target.value)}
                    placeholder="https://id.company.com"
                  />
                  <input
                    required
                    value={oidcClientId}
                    onChange={(event) => setOidcClientId(event.target.value)}
                    placeholder="Client ID"
                  />
                  <input
                    required
                    type="password"
                    value={oidcClientSecret}
                    onChange={(event) => setOidcClientSecret(event.target.value)}
                    placeholder="Client secret"
                  />
                  <button type="submit" disabled={busy}>
                    <ShieldCheck size={16} />
                    Configure SSO
                  </button>
                </form>
                <div className="item-list compact">
                  {oidcProviders.map((provider) => (
                    <article key={provider.provider_id} className="item">
                      <strong>{provider.name}</strong>
                      <span>{provider.issuer_url}</span>
                    </article>
                  ))}
                </div>
              </details>
            )}
          </section>
        )}

        <section className="panel-grid">
          <section className="panel primary-panel">
            <div className="panel-title">
              <FileUp size={18} />
              <h2>Document Ingestion</h2>
            </div>
            <form onSubmit={uploadDocument} className="stack">
              <input
                type="file"
                accept=".txt,.md,.csv,.json,.eml,.pdf,.docx"
                onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
              />
              <div className="split">
                <label>
                  Classification
                  <select
                    value={classification}
                    onChange={(event) => setClassification(event.target.value)}
                  >
                    <option value="public">Public</option>
                    <option value="internal">Internal</option>
                    <option value="restricted">Restricted</option>
                  </select>
                </label>
                <label>
                  Team
                  <input
                    value={ownerTeam}
                    onChange={(event) => setOwnerTeam(event.target.value)}
                  />
                </label>
              </div>
              <button type="submit" disabled={!token || busy}>
                <FileUp size={16} />
                Upload
              </button>
            </form>
          </section>

          <section className="panel primary-panel">
            <div className="panel-title">
              <Search size={18} />
              <h2>RAG Query</h2>
            </div>
            <form onSubmit={askQuestion} className="stack">
              <textarea value={query} onChange={(event) => setQuery(event.target.value)} />
              <button type="submit" disabled={!token || busy}>
                <Search size={16} />
                Ask
              </button>
            </form>
          </section>
        </section>

        {answer && (
          <section className="panel">
            <div className="panel-title">
              <ShieldCheck size={18} />
              <h2>Answer</h2>
            </div>
            <div className="security-summary">
              <span>{answer.grounded ? "Citation validated" : "Grounding unavailable"}</span>
              <span>{answer.generation_mode} / {answer.model}</span>
            </div>
            <p className="answer">{answer.answer}</p>
            {answer.fallback_reason && <em>{answer.fallback_reason}</em>}
            <div className="citation-list">
              {answer.citations.map((citation) => (
                <article key={citation.chunk_id || citation.document_id} className="item">
                  <strong>{citation.title}</strong>
                  <span>{citation.excerpt}</span>
                  {citation.score !== null && <small>Similarity score: {citation.score}</small>}
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="panel security-console">
          <div className="panel-title console-heading">
            <div>
              <ShieldCheck size={20} />
              <div>
                <h2>Security MCP Console</h2>
                <small>Authenticated Streamable HTTP: {API_BASE}/protocol/mcp</small>
              </div>
            </div>
            <span className="protocol-badge">MCP 2025-11-25</span>
          </div>

          <div className="security-summary">
            <span>Server-owned scopes</span>
            <span>Immutable payload hashes</span>
            <span>Human approval resume</span>
            <span>Prompt safety scan</span>
          </div>

          <form className="mcp-runner" onSubmit={runMcpTool}>
            <div className="stack">
              <label>
                Tool
                <select
                  value={selectedMcpTool}
                  onChange={(event) => chooseMcpTool(event.target.value)}
                >
                  {mcpTools.map((tool) => (
                    <option key={tool.name} value={tool.name}>
                      {tool.name}
                    </option>
                  ))}
                </select>
              </label>
              {selectedMcpDefinition && (
                <div className="tool-contract">
                  <p>{selectedMcpDefinition.description}</p>
                  <small>
                    Scope: {selectedMcpDefinition.required_scope} | {" "}
                    {selectedMcpDefinition.approval_required ? "approval required" : "runs immediately"}
                  </small>
                </div>
              )}
            </div>
            <div className="stack">
              <label>
                Validated JSON arguments
                <textarea
                  className="code-input"
                  value={mcpArguments}
                  onChange={(event) => setMcpArguments(event.target.value)}
                />
              </label>
              <button type="submit" disabled={!token || busy || !selectedMcpDefinition}>
                <ShieldCheck size={16} />
                Run through security gateway
              </button>
            </div>
          </form>

          <div className="execution-list">
            {mcpExecutions.slice(0, 12).map((execution) => (
              <article key={execution.execution_id} className="execution-card">
                <div className="execution-title">
                  <strong>{execution.tool_name}</strong>
                  <span className={`status-pill status-${execution.status}`}>
                    {execution.status.replace("_", " ")}
                  </span>
                </div>
                <small>{execution.execution_id}</small>
                <code title={execution.arguments_hash}>sha256:{execution.arguments_hash.slice(0, 16)}...</code>
                {execution.approval_id && <small>Approval: {execution.approval_id}</small>}
                {execution.error && <em>{execution.error}</em>}
                {Object.keys(execution.result).length > 0 && (
                  <pre>{JSON.stringify(execution.result, null, 2)}</pre>
                )}
              </article>
            ))}
            {mcpExecutions.length === 0 && (
              <p className="empty">Run a tool to create the first secured execution.</p>
            )}
          </div>
        </section>

        {runtimeSummary && (
          <section className="panel observability-console">
            <div className="panel-title console-heading">
              <div>
                <Activity size={20} />
                <div>
                  <h2>Runtime Governance</h2>
                  <small>Persistent cost, latency, reliability, and budget telemetry</small>
                </div>
              </div>
              <span className="protocol-badge">Last {runtimeSummary.window_hours}h</span>
            </div>

            <div className="metric-grid">
              <article className="metric-card">
                <small>Operations</small>
                <strong>{runtimeSummary.total_operations}</strong>
                <span>{runtimeSummary.success_rate.toFixed(1)}% completed</span>
              </article>
              <article className="metric-card">
                <small>Average latency</small>
                <strong>{runtimeSummary.average_latency_ms.toFixed(1)} ms</strong>
                <span>P95 {runtimeSummary.p95_latency_ms.toFixed(1)} ms</span>
              </article>
              <article className="metric-card">
                <small>Governance outcomes</small>
                <strong>{runtimeSummary.blocked_operations} blocked</strong>
                <span>{runtimeSummary.failed_operations} failed</span>
              </article>
              <article className="metric-card">
                <small>Estimated provider cost</small>
                <strong>${runtimeSummary.estimated_cost_usd.toFixed(6)}</strong>
                <span>{runtimeSummary.input_units.toLocaleString()} input units</span>
              </article>
            </div>

            {modelGateway && (
              <div className="security-summary">
                <span>
                  Gateway: {modelGateway.provider} / {modelGateway.model}
                </span>
                <span>
                  Grounded answers: {modelGateway.grounded_answers_enabled ? "enabled" : "disabled"}
                </span>
                <span>
                  LLM planner: {modelGateway.llm_planner_enabled ? "enabled" : "deterministic"}
                </span>
                <span>
                  {modelGateway.max_input_tokens} input / {modelGateway.max_output_tokens} output tokens
                </span>
              </div>
            )}

            <div className="governance-grid">
              <div className="stack">
                <div className="panel-title">
                  <DollarSign size={17} />
                  <h2>Cost Budgets</h2>
                </div>
                {runtimeSummary.budgets.map((budget) => (
                  <article key={budget.budget_id} className="budget-card">
                    <div>
                      <strong>{budget.name}</strong>
                      <span className={`status-pill budget-${budget.state}`}>
                        {budget.enabled ? budget.state : "disabled"}
                      </span>
                    </div>
                    <progress
                      value={Math.min(budget.utilization_percent, 100)}
                      max={100}
                    />
                    <small>
                      ${budget.spent_usd.toFixed(6)} of ${budget.limit_usd.toFixed(2)} {budget.period}
                    </small>
                  </article>
                ))}
                {runtimeSummary.budgets.length === 0 && (
                  <p className="empty">No cost budgets configured.</p>
                )}
              </div>

              <div className="stack">
                <div className="panel-title">
                  <Activity size={17} />
                  <h2>Operation Breakdown</h2>
                </div>
                <div className="item-list compact telemetry-list">
                  {runtimeSummary.breakdown.map((item) => (
                    <article
                      key={`${item.operation_type}-${item.provider}-${item.model}`}
                      className="item"
                    >
                      <strong>{item.operation_type.replaceAll("_", " ")}</strong>
                      <span>{item.provider} / {item.model}</span>
                      <small>
                        {item.operations} runs | {item.average_latency_ms.toFixed(1)} ms avg | ${item.estimated_cost_usd.toFixed(6)}
                      </small>
                    </article>
                  ))}
                  {runtimeSummary.breakdown.length === 0 && (
                    <p className="empty">Run a query or MCP tool to populate telemetry.</p>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {(user?.role === "admin" || user?.role === "manager") && (
          <section className="panel evaluation-console">
            <div className="panel-title console-heading">
              <div>
                <BarChart3 size={20} />
                <div>
                  <h2>RAG Quality Evaluation</h2>
                  <small>
                    Persistent retrieval, citation, groundedness, hallucination, and latency tests
                  </small>
                </div>
              </div>
              <span className="protocol-badge">Local vs OpenAI</span>
            </div>

            <div className="evaluation-layout">
              <form className="stack evaluation-form" onSubmit={createEvaluationDataset}>
                <div className="panel-title">
                  <Save size={17} />
                  <h2>Create Dataset</h2>
                </div>
                <div className="split">
                  <label>
                    Name
                    <input
                      value={evaluationName}
                      onChange={(event) => setEvaluationName(event.target.value)}
                    />
                  </label>
                  <label>
                    Corpus document IDs
                    <input
                      value={evaluationDocumentIds}
                      onChange={(event) => setEvaluationDocumentIds(event.target.value)}
                      placeholder="Comma-separated; blank uses all accessible documents"
                    />
                  </label>
                </div>
                <label>
                  Description
                  <input
                    value={evaluationDescription}
                    onChange={(event) => setEvaluationDescription(event.target.value)}
                  />
                </label>
                <div className="split">
                  <label>
                    Top K
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={evaluationTopK}
                      onChange={(event) => setEvaluationTopK(Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Minimum similarity
                    <input
                      type="number"
                      min={-1}
                      max={1}
                      step={0.05}
                      value={evaluationMinimumScore}
                      onChange={(event) => setEvaluationMinimumScore(Number(event.target.value))}
                    />
                  </label>
                </div>
                <label>
                  Cases JSON
                  <textarea
                    className="code-input evaluation-cases"
                    value={evaluationCases}
                    onChange={(event) => setEvaluationCases(event.target.value)}
                  />
                </label>
                <small>
                  Answerable cases require expected evidence, facts, and a reference answer.
                  Unanswerable cases must leave those fields empty.
                </small>
                <button type="submit" disabled={busy || !evaluationName.trim()}>
                  <Save size={16} />
                  Save evaluation dataset
                </button>
              </form>

              <div className="stack">
                <div className="panel-title">
                  <Database size={17} />
                  <h2>Datasets</h2>
                </div>
                <div className="item-list evaluation-datasets">
                  {evaluationDatasets.map((dataset) => (
                    <article key={dataset.dataset_id} className="item">
                      <strong>{dataset.name}</strong>
                      <span>{dataset.description || "No description"}</span>
                      <small>
                        {dataset.case_count} cases | top {dataset.top_k} | score &gt; {dataset.minimum_score}
                      </small>
                      <code>{dataset.dataset_id}</code>
                      <button type="button" onClick={() => runEvaluation(dataset.dataset_id)} disabled={busy}>
                        <Play size={16} />
                        Compare local and OpenAI
                      </button>
                    </article>
                  ))}
                  {evaluationDatasets.length === 0 && (
                    <p className="empty">Create a curated dataset to establish a quality baseline.</p>
                  )}
                </div>
              </div>
            </div>

            <div className="evaluation-runs">
              {evaluationRuns.slice(0, 12).map((run) => (
                <article key={run.run_id} className="evaluation-run-card">
                  <div className="execution-title">
                    <div>
                      <strong>{run.dataset_name}</strong>
                      <small>{run.provider} / {run.model}</small>
                    </div>
                    <span className={`status-pill status-${run.status}`}>{run.status}</span>
                  </div>
                  <div className="quality-metrics">
                    <span><strong>{run.retrieval_accuracy.toFixed(1)}%</strong> retrieval</span>
                    <span><strong>{run.citation_correctness.toFixed(1)}%</strong> citations</span>
                    <span><strong>{run.groundedness.toFixed(1)}%</strong> grounded</span>
                    <span><strong>{run.hallucination_rate.toFixed(1)}%</strong> hallucination</span>
                  </div>
                  <small>
                    {run.average_latency_ms.toFixed(1)} ms average | {run.p95_latency_ms.toFixed(1)} ms P95 | {run.case_count} cases
                  </small>
                  {run.error && <em>{run.error}</em>}
                </article>
              ))}
              {evaluationRuns.length === 0 && (
                <p className="empty">No evaluation runs recorded yet.</p>
              )}
            </div>
          </section>
        )}

        <section className="columns">
          <section className="panel">
            <div className="panel-title">
              <Database size={18} />
              <h2>Documents</h2>
            </div>
            <div className="item-list">
              {documents.map((document) => (
                <article key={document.document_id} className="item">
                  <strong>{document.title}</strong>
                  <code>{document.document_id}</code>
                  <span>{document.summary}</span>
                  <small>
                    {document.classification} | {document.owner_team} | {document.chunk_count} chunks
                  </small>
                  {document.unsafe && <em>Flagged: {document.unsafe_reasons.join(", ")}</em>}
                  <div className="button-row">
                    <button type="button" onClick={() => viewDocument(document.document_id)}>
                      <Eye size={16} />
                      View
                    </button>
                    <button type="button" onClick={() => reindexDocument(document.document_id)}>
                      <RotateCcw size={16} />
                      Reindex
                    </button>
                    <button type="button" onClick={() => deleteDocument(document.document_id)}>
                      <Trash2 size={16} />
                      Delete
                    </button>
                  </div>
                </article>
              ))}
              {documents.length === 0 && <p className="empty">No documents uploaded yet.</p>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-title">
              <ShieldCheck size={18} />
              <h2>Approvals</h2>
            </div>
            <div className="item-list">
              {approvals.map((approval) => (
                <article key={approval.approval_id} className="item">
                  <strong>{approval.action_id}</strong>
                  <span>{approval.status}</span>
                  <small>Requested by: {approval.requested_by}</small>
                  {approval.execution_id && <small>Execution: {approval.execution_id}</small>}
                  {approval.arguments_hash && (
                    <code title={approval.arguments_hash}>
                      sha256:{approval.arguments_hash.slice(0, 16)}...
                    </code>
                  )}
                  {approval.status === "pending" &&
                    (user?.role === "admin" || user?.role === "manager") &&
                    approval.requested_by !== user?.user_id && (
                    <div className="button-row">
                      <button type="button" onClick={() => decideApproval(approval.approval_id, true)}>
                        <CheckCircle2 size={16} />
                        Approve
                      </button>
                      <button type="button" onClick={() => decideApproval(approval.approval_id, false)}>
                        <XCircle size={16} />
                        Reject
                      </button>
                    </div>
                  )}
                  {approval.status === "pending" && approval.requested_by === user?.user_id && (
                    <small>Self-approval is blocked. Sign in as a different manager or admin.</small>
                  )}
                </article>
              ))}
              {approvals.length === 0 && <p className="empty">No approvals visible.</p>}
            </div>
          </section>
        </section>

        {selectedDocument && (
          <section className="panel">
            <div className="panel-title">
              <Eye size={18} />
              <h2>Source Viewer</h2>
            </div>
            <form className="stack" onSubmit={updateDocument}>
              <div className="split three">
                <label>
                  Title
                  <input
                    value={editTitle}
                    onChange={(event) => setEditTitle(event.target.value)}
                  />
                </label>
                <label>
                  Classification
                  <select
                    value={editClassification}
                    onChange={(event) => setEditClassification(event.target.value)}
                  >
                    <option value="public">Public</option>
                    <option value="internal">Internal</option>
                    <option value="restricted">Restricted</option>
                  </select>
                </label>
                <label>
                  Team
                  <input
                    value={editOwnerTeam}
                    onChange={(event) => setEditOwnerTeam(event.target.value)}
                  />
                </label>
              </div>
              <button type="submit" disabled={busy}>
                <Save size={16} />
                Save metadata
              </button>
            </form>
            <div className="item-list source-list">
              {selectedDocument.chunks.map((chunk) => (
                <article key={chunk.chunk_id} className="item">
                  <strong>Chunk {chunk.chunk_index + 1}</strong>
                  <span>{chunk.text}</span>
                </article>
              ))}
            </div>
          </section>
        )}

        <section className="columns">
          <section className="panel">
            <div className="panel-title">
              <Plug size={18} />
              <h2>Connectors</h2>
            </div>
            <div className="item-list">
              {connectors.map((connector) => (
                <article key={connector.provider} className="item">
                  <div className="connector-heading">
                    <strong>{connector.display_name}</strong>
                    <span className={`status-pill status-${connector.status}`}>
                      {connector.status.replaceAll("_", " ")}
                    </span>
                  </div>
                  <span>{connector.account_label || "No connected account"}</span>
                  <small>Sync: {connector.resources.join(", ") || "none"}</small>
                  <small>Actions: {connector.actions.join(", ") || "none"}</small>
                  {connector.last_sync_at && <small>Last sync: {connector.last_sync_at}</small>}
                  {connector.last_error && <small className="error-text">{connector.last_error}</small>}
                  <div className="button-row connector-actions">
                    <button
                      type="button"
                      onClick={() => authorizeConnector(connector.provider)}
                      disabled={!token || busy || !canManageConnectors}
                    >
                      <Plug size={16} />
                      {connector.status === "connected" ? "Reconnect" : "Authorize"}
                    </button>
                    {connector.status === "connected" && (
                      <>
                        <button
                          type="button"
                          onClick={() => syncConnector(connector)}
                          disabled={busy || !canSyncConnectors || connector.resources.length === 0}
                        >
                          <RefreshCw size={16} />
                          Sync now
                        </button>
                        <button
                          type="button"
                          onClick={() => createWebhookSetup(connector)}
                          disabled={busy || !canManageConnectors || connector.resources.length === 0}
                        >
                          Webhook
                        </button>
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => disconnectConnector(connector)}
                          disabled={busy || !canManageConnectors}
                        >
                          <XCircle size={16} />
                          Disconnect
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
            {connectorSyncStates.length > 0 && (
              <div className="item-list compact connector-sync-list">
                {connectorSyncStates.map((state) => (
                  <article key={state.sync_state_id} className="item">
                    <div className="connector-heading">
                      <strong>{state.provider} / {state.resource}</strong>
                      <span className={`status-pill status-${state.status}`}>{state.status}</span>
                    </div>
                    <small>
                      {state.items_changed} changed of {state.items_seen} seen
                      {state.has_cursor ? " · incremental cursor active" : ""}
                    </small>
                    {state.last_error && <small className="error-text">{state.last_error}</small>}
                  </article>
                ))}
              </div>
            )}
            {latestWebhookSetup && (
              <article className="item webhook-setup">
                <strong>One-time webhook setup</strong>
                <small>Callback URL</small>
                <code>{latestWebhookSetup.callback_url}</code>
                <small>Signing secret — shown only once</small>
                <code>{latestWebhookSetup.secret}</code>
                <button type="button" onClick={() => setLatestWebhookSetup(null)}>
                  I saved these values
                </button>
              </article>
            )}
            {webhookSubscriptions.length > 0 && (
              <small>
                {webhookSubscriptions.filter((subscription) => subscription.status === "active").length}
                {" active verified webhook endpoint(s)"}
              </small>
            )}
            <label>
              Search Google Drive
              <input
                value={driveSearch}
                onChange={(event) => setDriveSearch(event.target.value)}
                placeholder="contracts, renewal, policy..."
              />
            </label>
            <div className="button-row">
              <button
                type="button"
                onClick={() => loadGoogleDriveFiles()}
                disabled={!token || busy || !googleDriveReady}
              >
                <Search size={16} />
                Load Drive files
              </button>
              <button
                type="button"
                onClick={importSelectedGoogleDriveFiles}
                disabled={!token || busy || selectedDriveFileIds.length === 0}
              >
                <Plug size={16} />
                Import selected
              </button>
            </div>
            {!googleDriveReady && (
              <small>Authorize Google Workspace before listing real Drive files.</small>
            )}
            <div className="item-list compact">
              {driveFiles.map((file) => (
                <article key={file.file_id} className="item drive-file">
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={selectedDriveFileIds.includes(file.file_id)}
                      disabled={!file.importable}
                      onChange={() => toggleDriveFile(file.file_id)}
                    />
                    <span>
                      <strong>{file.name}</strong>
                      {!file.importable && <em> Unsupported file type</em>}
                    </span>
                  </label>
                  <small>{file.mime_type}</small>
                  {file.modified_time && <small>Modified {file.modified_time}</small>}
                  {file.web_view_link && (
                    <a href={file.web_view_link} target="_blank" rel="noreferrer">
                      Open in Drive
                    </a>
                  )}
                </article>
              ))}
              {driveFiles.length === 0 && <p className="empty">No Drive files loaded yet.</p>}
            </div>
            {driveNextPageToken && (
              <button
                type="button"
                onClick={() => loadGoogleDriveFiles(driveNextPageToken)}
                disabled={!token || busy}
              >
                <RefreshCw size={16} />
                Load more Drive files
              </button>
            )}
            <label>
              Manual Google Drive-style note
              <textarea
                value={connectorContent}
                onChange={(event) => setConnectorContent(event.target.value)}
              />
            </label>
            <button type="button" onClick={importGoogleDriveNote} disabled={!token || busy}>
              <Plug size={16} />
              Import Drive note
            </button>
          </section>

          <section className="panel">
            <div className="panel-title">
              <Database size={18} />
              <h2>Audit</h2>
            </div>
            <div className="item-list compact">
              {auditEvents.map((event) => (
                <article key={event.event_id} className="item">
                  <strong>{event.event_type}</strong>
                  <span>{JSON.stringify(event.detail)}</span>
                </article>
              ))}
              {auditEvents.length === 0 && <p className="empty">No audit events loaded.</p>}
            </div>
          </section>
        </section>

        <section className="columns">
          <section className="panel">
            <div className="panel-title">
              <Bot size={18} />
              <h2>Agent Workflows</h2>
            </div>
            <form className="stack" onSubmit={createAgentWorkflow}>
              <textarea
                value={agentPrompt}
                onChange={(event) => setAgentPrompt(event.target.value)}
              />
              <button type="submit" disabled={!token || busy}>
                <Bot size={16} />
                Create workflow
              </button>
            </form>
            <div className="item-list workflow-list">
              {workflows.map((workflow) => {
                const completedActions = workflow.actions.filter(
                  (action) => action.status === "completed" || action.status === "skipped",
                ).length;
                const canResume = ["planned", "running", "waiting_for_approval"].includes(
                  workflow.status,
                );
                const canCancel = !["completed", "cancelled"].includes(workflow.status);
                return (
                  <article key={workflow.workflow_id} className="workflow-card">
                    <div className="workflow-heading">
                      <div>
                        <span className={`status-pill status-${workflow.status}`}>
                          {workflow.status.replaceAll("_", " ")}
                        </span>
                        <strong>{workflow.prompt}</strong>
                      </div>
                      <small>
                        {completedActions}/{workflow.actions.length} actions
                      </small>
                    </div>
                    <progress
                      value={completedActions}
                      max={Math.max(workflow.actions.length, 1)}
                    />
                    <small>{workflow.plan.summary}</small>
                    <small>
                      Planner: {workflow.plan.planner_mode} / {workflow.plan.model} | {workflow.plan.validated ? "server validated" : "unvalidated"}
                    </small>
                    {workflow.plan.fallback_reason && <em>{workflow.plan.fallback_reason}</em>}
                    <div className="workflow-timeline">
                      {workflow.actions.map((action) => (
                        <div key={action.action_instance_id} className="workflow-action">
                          <div className={`workflow-marker status-${action.status}`}>
                            {action.status === "completed" ? (
                              <CheckCircle2 size={16} />
                            ) : ["blocked", "failed", "cancelled"].includes(action.status) ? (
                              <XCircle size={16} />
                            ) : (
                              action.sequence + 1
                            )}
                          </div>
                          <div className="workflow-action-body">
                            <div className="workflow-action-title">
                              <strong>{action.tool_name.replaceAll("_", " ")}</strong>
                              <span className={`status-pill status-${action.status}`}>
                                {action.status.replaceAll("_", " ")}
                              </span>
                            </div>
                            <small>{action.description}</small>
                            <small>
                              {action.required_scope} | attempt {action.attempt_count}/
                              {action.max_attempts}
                            </small>
                            {action.execution_id && <code>MCP {action.execution_id}</code>}
                            {action.approval_id && <code>Approval {action.approval_id}</code>}
                            {Object.keys(action.result).length > 0 && (
                              <code>{JSON.stringify(action.result)}</code>
                            )}
                            {action.error && <em>{action.error}</em>}
                          </div>
                        </div>
                      ))}
                    </div>
                    {workflow.last_error && <em>{workflow.last_error}</em>}
                    <div className="button-row">
                      {canResume && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runWorkflowCommand(workflow.workflow_id, "resume")}
                        >
                          <RefreshCw size={15} />
                          Resume
                        </button>
                      )}
                      {workflow.status === "failed" && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runWorkflowCommand(workflow.workflow_id, "retry")}
                        >
                          <RotateCcw size={15} />
                          Retry failed action
                        </button>
                      )}
                      {canCancel && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => void runWorkflowCommand(workflow.workflow_id, "cancel")}
                        >
                          <XCircle size={15} />
                          Cancel
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
              {workflows.length === 0 && <p className="empty">No workflows created yet.</p>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-title">
              <ClipboardList size={18} />
              <h2>Policies And Jobs</h2>
            </div>
            <div className="item-list compact">
              {policies.map((policy) => (
                <article key={policy.policy_id} className="item">
                  <strong>{policy.name}</strong>
                  <span>
                    {policy.rule_type} | {policy.effect} | {policy.enabled ? "enabled" : "disabled"}
                  </span>
                </article>
              ))}
              {jobs.map((job) => (
                <article key={job.job_id} className="item">
                  <strong>{job.job_type}</strong>
                  <span>
                    {job.status} | {JSON.stringify(job.result)}
                  </span>
                  <progress
                    value={typeof job.result.progress === "number" ? job.result.progress : 0}
                    max={100}
                  />
                </article>
              ))}
              {policies.length === 0 && jobs.length === 0 && (
                <p className="empty">No policy or job data loaded.</p>
              )}
            </div>
          </section>
        </section>

        {unsafeDocuments.length > 0 && (
          <section className="panel">
            <div className="panel-title">
              <ShieldCheck size={18} />
              <h2>Unsafe Document Review</h2>
            </div>
            <div className="item-list">
              {unsafeDocuments.map((document) => (
                <article key={document.document_id} className="item">
                  <strong>{document.title}</strong>
                  <span>{document.unsafe_reasons.join(", ")}</span>
                  <div className="button-row">
                    <button type="button" onClick={() => viewDocument(document.document_id)}>
                      <Eye size={16} />
                      Inspect
                    </button>
                    <button type="button" onClick={() => deleteDocument(document.document_id)}>
                      <Trash2 size={16} />
                      Delete
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
