from typing import Literal

from fastapi import HTTPException
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.models.schemas import MCPExecutionRequest, UserContext
from app.services.mcp_gateway import mcp_gateway_service


class WorkOSJWTVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            user = decode_access_token(token)
        except HTTPException:
            return None
        return AccessToken(
            token=token,
            client_id=user.user_id,
            scopes=user.scopes,
            subject=user.user_id,
            claims=user.model_dump(mode="json"),
        )


settings = get_settings()
security_mcp = FastMCP(
    name="Secure Agentic Work OS",
    instructions=(
        "Use server-advertised tools only. Side effects are persisted, audited, and "
        "may pause for a separate human approval bound to the exact arguments."
    ),
    token_verifier=WorkOSJWTVerifier(),
    auth=AuthSettings(
        issuer_url=settings.mcp_issuer_url,
        resource_server_url=settings.mcp_server_url,
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp",
)


def _current_user() -> UserContext:
    token = get_access_token()
    if token is None or token.claims is None:
        raise PermissionError("An authenticated Work OS bearer token is required.")
    return UserContext.model_validate(token.claims)


def _execute(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
    execution = mcp_gateway_service.request_execution(
        MCPExecutionRequest(tool_name=tool_name, arguments=arguments),
        _current_user(),
    )
    return execution.model_dump(mode="json")


@security_mcp.tool(
    description="Search accessible workspace documents and return cited results.",
    meta={
        "security": {
            "required_scope": "documents:read",
            "approval_required": False,
            "side_effect": False,
        }
    },
    structured_output=True,
)
def search_documents(question: str) -> dict[str, object]:
    return _execute("search_documents", {"question": question})


@security_mcp.tool(
    description="Create a persistent workspace task.",
    meta={
        "security": {
            "required_scope": "tasks:write",
            "approval_required": False,
            "side_effect": True,
        }
    },
    structured_output=True,
)
def create_task(
    title: str,
    description: str = "",
    due_date: str | None = None,
) -> dict[str, object]:
    return _execute(
        "create_task",
        {"title": title, "description": description, "due_date": due_date},
    )


@security_mcp.tool(
    description="Request an approval-gated email send through the connected Gmail account.",
    meta={
        "security": {
            "required_scope": "email:send",
            "approval_required": True,
            "side_effect": True,
        }
    },
    structured_output=True,
)
def send_email(to: str, subject: str, body: str = "") -> dict[str, object]:
    return _execute("send_email", {"to": to, "subject": subject, "body": body})


@security_mcp.tool(
    description="Request an approval-gated Google Calendar event creation.",
    meta={"security": {"required_scope": "connectors:act", "approval_required": True, "side_effect": True}},
    structured_output=True,
)
def create_calendar_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    timezone: str = "UTC",
    attendees: list[str] | None = None,
) -> dict[str, object]:
    return _execute(
        "create_calendar_event",
        {
            "summary": summary,
            "start": start,
            "end": end,
            "description": description,
            "timezone": timezone,
            "attendees": attendees or [],
        },
    )


@security_mcp.tool(
    description="Request an approval-gated Slack message.",
    meta={"security": {"required_scope": "connectors:act", "approval_required": True, "side_effect": True}},
    structured_output=True,
)
def send_slack_message(channel: str, text: str) -> dict[str, object]:
    return _execute("send_slack_message", {"channel": channel, "text": text})


@security_mcp.tool(
    description="Request approval to create a GitHub issue.",
    meta={"security": {"required_scope": "connectors:act", "approval_required": True, "side_effect": True}},
    structured_output=True,
)
def create_github_issue(
    repository: str,
    title: str,
    body: str = "",
    labels: list[str] | None = None,
) -> dict[str, object]:
    return _execute(
        "create_github_issue",
        {"repository": repository, "title": title, "body": body, "labels": labels or []},
    )


@security_mcp.tool(
    description="Request approval to create a Jira issue.",
    meta={"security": {"required_scope": "connectors:act", "approval_required": True, "side_effect": True}},
    structured_output=True,
)
def create_jira_issue(
    project_key: str,
    summary: str,
    description: str = "",
    issue_type: str = "Task",
) -> dict[str, object]:
    return _execute(
        "create_jira_issue",
        {
            "project_key": project_key,
            "summary": summary,
            "description": description,
            "issue_type": issue_type,
        },
    )


@security_mcp.tool(
    description="Request approval to create a Notion page.",
    meta={"security": {"required_scope": "connectors:act", "approval_required": True, "side_effect": True}},
    structured_output=True,
)
def create_notion_page(
    parent_id: str,
    title: str,
    content: str = "",
    parent_type: Literal["page_id", "database_id"] = "page_id",
) -> dict[str, object]:
    return _execute(
        "create_notion_page",
        {
            "parent_id": parent_id,
            "parent_type": parent_type,
            "title": title,
            "content": content,
        },
    )


@security_mcp.tool(
    description="Export accessible document metadata after human approval.",
    meta={
        "security": {
            "required_scope": "documents:read",
            "approval_required": True,
            "side_effect": True,
        }
    },
    structured_output=True,
)
def export_data(
    classification: Literal["all", "public", "internal", "restricted"] = "all",
    limit: int = 25,
) -> dict[str, object]:
    return _execute(
        "export_data",
        {"classification": classification, "limit": limit},
    )


security_mcp_http_app = security_mcp.streamable_http_app()
