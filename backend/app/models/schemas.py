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
    action_type: Literal[
        "search_email",
        "search_documents",
        "create_task",
        "draft_reply",
        "send_email",
    ]
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
    reviewed_at: datetime | None = None
    execution_id: str | None = None
    arguments_hash: str | None = None


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
    scope: str | None = None
    arguments: dict[str, object] = Field(default_factory=dict)


class MCPToolCallResponse(BaseModel):
    status: Literal[
        "allowed",
        "completed",
        "approval_required",
        "blocked",
        "rejected",
        "failed",
    ]
    message: str
    approval_id: str | None = None
    execution_id: str | None = None
    result: dict[str, object] = Field(default_factory=dict)


class MCPExecutionRequest(BaseModel):
    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class MCPToolDefinition(BaseModel):
    name: str
    description: str
    required_scope: str
    approval_required: bool
    side_effect: bool
    input_schema: dict[str, object]


class MCPExecutionRecord(BaseModel):
    execution_id: str
    tool_name: str
    requested_by: str
    required_scope: str
    arguments: dict[str, object]
    arguments_hash: str
    idempotency_key: str | None = None
    status: Literal[
        "running",
        "pending_approval",
        "completed",
        "blocked",
        "rejected",
        "failed",
    ]
    approval_id: str | None = None
    result: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


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


class AsyncJobResponse(BaseModel):
    job: JobRecord
    message: str


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


class GoogleDriveFileRecord(BaseModel):
    file_id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    size: int | None = None
    web_view_link: str | None = None
    importable: bool = True


class GoogleDriveFileListResponse(BaseModel):
    files: list[GoogleDriveFileRecord]
    next_page_token: str | None = None


class GoogleDriveImportRequest(BaseModel):
    file_ids: list[str] = Field(min_length=1, max_length=10)
    classification: Literal["public", "internal", "restricted"] = "internal"
    owner_team: str = "workspace"


class AgentWorkflowRequest(BaseModel):
    prompt: str


class WorkflowActionRecord(BaseModel):
    action_instance_id: str
    workflow_id: str
    sequence: int
    action_type: str
    tool_name: str
    description: str
    required_scope: str
    requires_approval: bool
    status: Literal[
        "pending",
        "running",
        "waiting_for_approval",
        "completed",
        "blocked",
        "failed",
        "cancelled",
        "skipped",
    ]
    attempt_count: int = 0
    max_attempts: int = 3
    idempotency_key: str
    input: dict[str, object] = Field(default_factory=dict)
    result: dict[str, object] = Field(default_factory=dict)
    execution_id: str | None = None
    approval_id: str | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None


class AgentWorkflowRecord(BaseModel):
    workflow_id: str
    prompt: str
    requested_by: str
    status: Literal[
        "planned",
        "running",
        "waiting_for_approval",
        "completed",
        "blocked",
        "failed",
        "cancelled",
    ]
    plan: AgentPlanResponse
    actions: list[WorkflowActionRecord] = Field(default_factory=list)
    current_action_index: int = 0
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    cancelled_at: str | None = None


class RuntimeObservation(BaseModel):
    observation_id: str
    trace_id: str
    operation_type: Literal["embedding", "rag_query", "mcp_tool"]
    actor_id: str
    provider: str
    model: str
    status: Literal["completed", "failed", "blocked", "rejected", "cancelled"]
    latency_ms: float = Field(ge=0)
    input_units: int = Field(default=0, ge=0)
    output_units: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0, ge=0)
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ObservationBreakdown(BaseModel):
    operation_type: str
    provider: str
    model: str
    operations: int
    completed: int
    failed_or_blocked: int
    average_latency_ms: float
    estimated_cost_usd: float


class CostBudgetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    period: Literal["daily", "monthly"] = "daily"
    limit_usd: float = Field(gt=0, le=1_000_000)
    warning_percent: int = Field(default=80, ge=1, le=100)
    enabled: bool = True


class CostBudgetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    period: Literal["daily", "monthly"] | None = None
    limit_usd: float | None = Field(default=None, gt=0, le=1_000_000)
    warning_percent: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None


class CostBudgetRecord(BaseModel):
    budget_id: str
    name: str
    period: Literal["daily", "monthly"]
    limit_usd: float
    warning_percent: int
    enabled: bool
    created_by: str
    spent_usd: float
    remaining_usd: float
    utilization_percent: float
    state: Literal["ok", "warning", "exceeded"]
    period_start: datetime
    period_end: datetime
    created_at: str | None = None
    updated_at: str | None = None


class RuntimeSummary(BaseModel):
    window_hours: int
    total_operations: int
    completed_operations: int
    failed_operations: int
    blocked_operations: int
    success_rate: float
    average_latency_ms: float
    p95_latency_ms: float
    input_units: int
    output_units: int
    estimated_cost_usd: float
    breakdown: list[ObservationBreakdown] = Field(default_factory=list)
    budgets: list[CostBudgetRecord] = Field(default_factory=list)
