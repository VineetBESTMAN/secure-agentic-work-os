import { FormEvent, useEffect, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ClipboardList,
  Database,
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

type AgentWorkflowRecord = {
  workflow_id: string;
  prompt: string;
  status: "planned" | "waiting_for_approval" | "completed" | "blocked";
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
    const [documentData, approvalData, connectorData] = await Promise.all([
      api<DocumentRecord[]>("/api/documents/library"),
      api<ApprovalRecord[]>("/api/approvals"),
      api<ConnectorRecord[]>("/api/connectors"),
    ]);
    setDocuments(documentData);
    setApprovals(approvalData);
    setConnectors(connectorData);
    setWorkflows(await api<AgentWorkflowRecord[]>("/api/agent/workflows"));
    if (user?.scopes.includes("audit:read")) {
      setAuditEvents(await api<AuditEvent[]>("/api/audit/events"));
    }
    if (user?.role === "admin" || user?.role === "manager") {
      setUnsafeDocuments(await api<DocumentRecord[]>("/api/documents/unsafe"));
      setPolicies(await api<PolicyRecord[]>("/api/policies"));
      setJobs(await api<JobRecord[]>("/api/jobs"));
    }
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
      const document = await api<DocumentRecord>("/api/documents/upload", {
        method: "POST",
        body: formData,
      });
      setMessage(`Uploaded ${document.filename} with ${document.chunk_count} chunks.`);
      setSelectedFile(null);
      await refreshAll();
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
      const result = await api<{ document: DocumentRecord; message: string }>(
        `/api/documents/${documentId}/reindex`,
        { method: "POST" },
      );
      setMessage(result.message);
      await refreshAll();
      await viewDocument(documentId);
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

  async function createSendEmailApproval() {
    setBusy(true);
    try {
      const result = await api<{ status: string; message: string }>("/api/mcp/tool-call", {
        method: "POST",
        body: JSON.stringify({
          tool_name: "send_email",
          scope: "email:send",
          arguments: { to: "client@example.com", subject: "Follow-up" },
        }),
      });
      setMessage(result.message);
      await refreshAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Tool call failed");
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
      const result = await api<{
        job: JobRecord;
        imported_documents: DocumentRecord[];
      }>("/api/connectors/import", {
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
      setMessage(
        `Imported ${result.imported_documents.length} Google Drive item through ${result.job.job_id}.`,
      );
      await refreshAll();
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

  function logout() {
    setToken("");
    setUser(null);
    localStorage.removeItem("workos_token");
    localStorage.removeItem("workos_user");
  }

  const googleConnector = connectors.find((connector) => connector.provider === "google");
  const googleDriveReady = googleConnector?.status === "connected";

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
            <button type="button" onClick={createSendEmailApproval} disabled={!token || busy}>
              <ShieldCheck size={16} />
              Test send-email gate
            </button>
            <div className="item-list">
              {approvals.map((approval) => (
                <article key={approval.approval_id} className="item">
                  <strong>{approval.action_id}</strong>
                  <span>{approval.status}</span>
                  {approval.status === "pending" && (
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
                </article>
              ))}
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
            <div className="item-list">
              {workflows.map((workflow) => (
                <article key={workflow.workflow_id} className="item">
                  <strong>{workflow.status}</strong>
                  <span>{workflow.prompt}</span>
                  <small>{workflow.plan.summary}</small>
                  {workflow.plan.actions.map((action) => (
                    <small key={action.action_id}>
                      {action.action_type} | {action.requires_approval ? "approval" : "safe"}
                    </small>
                  ))}
                </article>
              ))}
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
