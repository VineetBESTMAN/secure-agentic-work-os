import { FormEvent, useEffect, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  ClipboardList,
  Database,
  DollarSign,
  Eye,
  FileUp,
  Plug,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";

type User = {
  user_id: string;
  email: string;
  role: "admin" | "manager" | "employee";
  scopes: string[];
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
  status: "not_configured" | "ready" | "connected";
  account_label: string | null;
  connected_at: string | null;
  scopes: string[];
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
    actions: {
      action_id: string;
      action_type: string;
      description: string;
      requires_approval: boolean;
      scope: string;
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
  export_data: { classification: "internal", limit: 25 },
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export default function App() {
  const [email, setEmail] = useState("admin@demo.local");
  const [password, setPassword] = useState("demo-password");
  const [token, setToken] = useState(() => localStorage.getItem("workos_token") || "");
  const [user, setUser] = useState<User | null>(() => {
    const stored = localStorage.getItem("workos_user");
    return stored ? JSON.parse(stored) : null;
  });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [unsafeDocuments, setUnsafeDocuments] = useState<DocumentRecord[]>([]);
  const [selectedDocument, setSelectedDocument] = useState<DocumentDetail | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [connectors, setConnectors] = useState<ConnectorRecord[]>([]);
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

  async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...init.headers,
      },
    });
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
    const [documentData, approvalData, connectorData, toolData, executionData] = await Promise.all([
      api<DocumentRecord[]>("/api/documents/library"),
      api<ApprovalRecord[]>("/api/approvals"),
      api<ConnectorRecord[]>("/api/connectors"),
      api<MCPToolDefinition[]>("/api/mcp/tools"),
      api<MCPExecutionRecord[]>("/api/mcp/executions"),
    ]);
    setDocuments(documentData);
    setApprovals(approvalData);
    setConnectors(connectorData);
    setMcpTools(toolData);
    setMcpExecutions(executionData);
    setWorkflows(await api<AgentWorkflowRecord[]>("/api/agent/workflows"));
    if (user?.scopes.includes("audit:read")) {
      setAuditEvents(await api<AuditEvent[]>("/api/audit/events"));
    }
    if (user?.role === "admin" || user?.role === "manager") {
      setUnsafeDocuments(await api<DocumentRecord[]>("/api/documents/unsafe"));
      setPolicies(await api<PolicyRecord[]>("/api/policies"));
      setJobs(await api<JobRecord[]>("/api/jobs"));
      setRuntimeSummary(await api<RuntimeSummary>("/api/observability/summary?hours=24"));
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
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) throw new Error("Login failed");
      const body = await response.json();
      setToken(body.access_token);
      setUser(body.user);
      localStorage.setItem("workos_token", body.access_token);
      localStorage.setItem("workos_user", JSON.stringify(body.user));
      setMessage(`Signed in as ${body.user.email}`);
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

  function logout() {
    setToken("");
    setUser(null);
    localStorage.removeItem("workos_token");
    localStorage.removeItem("workos_user");
  }

  const googleConnector = connectors.find((connector) => connector.provider === "google");
  const googleDriveReady = googleConnector?.status === "connected";
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
          <button type="submit" disabled={busy}>
            <ShieldCheck size={16} />
            Sign in
          </button>
        </form>

        {user && (
          <div className="session">
            <strong>{user.email}</strong>
            <span>{user.role}</span>
            <button type="button" onClick={logout}>
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
            <p className="answer">{answer.answer}</p>
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
                  <strong>{connector.display_name}</strong>
                  <span>{connector.account_label || connector.status}</span>
                  <button
                    type="button"
                    onClick={() => authorizeConnector(connector.provider)}
                    disabled={!token || busy}
                  >
                    <Plug size={16} />
                    Authorize
                  </button>
                </article>
              ))}
            </div>
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
