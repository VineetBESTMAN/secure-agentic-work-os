import { FormEvent, useEffect, useState } from "react";
import {
  CheckCircle2,
  Database,
  FileUp,
  Plug,
  RefreshCw,
  Search,
  ShieldCheck,
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

const API_BASE = "http://127.0.0.1:8000";

export default function App() {
  const [email, setEmail] = useState("admin@demo.local");
  const [password, setPassword] = useState("demo-password");
  const [token, setToken] = useState(() => localStorage.getItem("workos_token") || "");
  const [user, setUser] = useState<User | null>(() => {
    const stored = localStorage.getItem("workos_user");
    return stored ? JSON.parse(stored) : null;
  });
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [connectors, setConnectors] = useState<ConnectorRecord[]>([]);
  const [query, setQuery] = useState("What does this document say about urgent work?");
  const [answer, setAnswer] = useState<RagAnswer | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [classification, setClassification] = useState("internal");
  const [ownerTeam, setOwnerTeam] = useState("general");
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
    if (user?.scopes.includes("audit:read")) {
      setAuditEvents(await api<AuditEvent[]>("/api/audit/events"));
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

  function logout() {
    setToken("");
    setUser(null);
    localStorage.removeItem("workos_token");
    localStorage.removeItem("workos_user");
  }

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
      </section>
    </main>
  );
}
