from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class UserContext(BaseModel):
    user_id: str
    email: str
    role: Literal["admin", "manager", "employee"]
    scopes: list[str] = Field(default_factory=list)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserContext


class DocumentRecord(BaseModel):
    document_id: str
    title: str
    filename: str = ""
    classification: Literal["public", "internal", "restricted"]
    owner_team: str
    summary: str
    unsafe: bool = False
    unsafe_reasons: list[str] = Field(default_factory=list)
    chunk_count: int = 0
    created_at: str | None = None


class DocumentChunkRecord(BaseModel):
    chunk_id: str
    chunk_index: int
    text: str


class DocumentDetail(DocumentRecord):
    chunks: list[DocumentChunkRecord] = Field(default_factory=list)


class DocumentUpdateRequest(BaseModel):
    title: str | None = None
    classification: Literal["public", "internal", "restricted"] | None = None
    owner_team: str | None = None


class ReindexResponse(BaseModel):
    document: DocumentRecord
    message: str


class Citation(BaseModel):
    document_id: str
    title: str
    excerpt: str
    chunk_id: str | None = None
    score: float | None = None


class RagQuery(BaseModel):
    question: str


class RagAnswer(BaseModel):
    answer: str
    citations: list[Citation]


class ActionProposal(BaseModel):
    action_id: str
    action_type: Literal["search_email", "create_task", "draft_reply", "send_email"]
    description: str
    requires_approval: bool
    scope: str


class AgentPlanRequest(BaseModel):
    prompt: str


class AgentPlanResponse(BaseModel):
    summary: str
    actions: list[ActionProposal]


class ApprovalRecord(BaseModel):
    approval_id: str
    action_id: str
    requested_by: str
    status: Literal["pending", "approved", "rejected"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewed_by: str | None = None


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex}")
    actor_id: str
    event_type: str
    detail: dict[str, object]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MCPToolCallRequest(BaseModel):
    tool_name: str
    scope: str
    arguments: dict[str, object] = Field(default_factory=dict)


class MCPToolCallResponse(BaseModel):
    status: Literal["allowed", "approval_required", "blocked"]
    message: str
    approval_id: str | None = None


class PromptScanResult(BaseModel):
    flagged: bool
    reasons: list[str]


class ConnectorRecord(BaseModel):
    provider: Literal["google", "github", "slack", "notion", "jira"]
    display_name: str
    configured: bool
    status: Literal["not_configured", "ready", "connected"]
    scopes: list[str] = Field(default_factory=list)
    account_label: str | None = None
    connected_at: str | None = None


class OAuthStartResponse(BaseModel):
    provider: str
    configured: bool
    authorization_url: str | None = None
    message: str


class PolicyRecord(BaseModel):
    policy_id: str
    name: str
    description: str
    rule_type: Literal["document_access", "tool_approval", "prompt_safety"]
    effect: Literal["allow", "block", "approval_required"]
    conditions: dict[str, object] = Field(default_factory=dict)
    enabled: bool = True
    created_at: str | None = None


class PolicyCreateRequest(BaseModel):
    name: str
    description: str
    rule_type: Literal["document_access", "tool_approval", "prompt_safety"]
    effect: Literal["allow", "block", "approval_required"]
    conditions: dict[str, object] = Field(default_factory=dict)
    enabled: bool = True


class JobRecord(BaseModel):
    job_id: str
    job_type: str
    status: Literal["queued", "running", "completed", "failed"]
    detail: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] = Field(default_factory=dict)
    created_by: str
    created_at: str | None = None
    updated_at: str | None = None


class ConnectorImportItem(BaseModel):
    filename: str
    content: str
    mime_type: str = "text/plain"
    classification: Literal["public", "internal", "restricted"] = "internal"
    owner_team: str = "workspace"


class ConnectorImportRequest(BaseModel):
    provider: Literal["google", "github", "slack", "notion", "jira"] = "google"
    items: list[ConnectorImportItem]


class ConnectorImportResponse(BaseModel):
    job: JobRecord
    imported_documents: list[DocumentRecord]


class AgentWorkflowRequest(BaseModel):
    prompt: str


class AgentWorkflowRecord(BaseModel):
    workflow_id: str
    prompt: str
    requested_by: str
    status: Literal["planned", "waiting_for_approval", "completed", "blocked"]
    plan: AgentPlanResponse
    created_at: str | None = None
    updated_at: str | None = None
