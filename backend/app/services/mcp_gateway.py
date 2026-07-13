import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import get_settings
from app.core.database import decode_json, encode_json, get_connection
from app.core.rbac import require_scope
from app.models.schemas import (
    MCPExecutionRecord,
    MCPExecutionRequest,
    MCPToolCallRequest,
    MCPToolCallResponse,
    MCPToolDefinition,
    UserContext,
)
from app.services.approval import approval_service
from app.services.audit import audit_service
from app.services.policies import policy_service
from app.services.prompt_guard import prompt_guard_service
from app.services.rag import rag_service
from app.services.users import user_service


class SearchDocumentsArguments(BaseModel):
    question: str = Field(min_length=1, max_length=4_000)


class CreateTaskArguments(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SendEmailArguments(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="Follow-up", min_length=1, max_length=200)
    body: str = Field(default="", max_length=10_000)

    @field_validator("to")
    @classmethod
    def validate_recipient(cls, value: str) -> str:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("to must be a valid email address")
        return value.lower()


class ExportDataArguments(BaseModel):
    classification: Literal["all", "public", "internal", "restricted"] = "all"
    limit: int = Field(default=25, ge=1, le=100)


ToolHandler = Callable[[BaseModel, UserContext, str], dict[str, object]]


@dataclass(frozen=True)
class SecureTool:
    name: str
    description: str
    required_scope: str
    side_effect: bool
    approval_required: bool
    arguments_model: type[BaseModel]
    handler: ToolHandler


class MCPGatewayService:
    def __init__(self) -> None:
        self._tools = {
            tool.name: tool
            for tool in (
                SecureTool(
                    name="search_documents",
                    description="Search accessible workspace documents and return cited results.",
                    required_scope="documents:read",
                    side_effect=False,
                    approval_required=False,
                    arguments_model=SearchDocumentsArguments,
                    handler=self._search_documents,
                ),
                SecureTool(
                    name="create_task",
                    description="Create a persistent workspace task.",
                    required_scope="tasks:write",
                    side_effect=True,
                    approval_required=False,
                    arguments_model=CreateTaskArguments,
                    handler=self._create_task,
                ),
                SecureTool(
                    name="send_email",
                    description="Queue an approved email in safe simulation mode.",
                    required_scope="email:send",
                    side_effect=True,
                    approval_required=False,
                    arguments_model=SendEmailArguments,
                    handler=self._simulate_email,
                ),
                SecureTool(
                    name="export_data",
                    description="Export accessible document metadata after approval.",
                    required_scope="documents:read",
                    side_effect=True,
                    approval_required=False,
                    arguments_model=ExportDataArguments,
                    handler=self._export_data,
                ),
            )
        }

    def list_tools(self) -> list[MCPToolDefinition]:
        return [
            MCPToolDefinition(
                name=tool.name,
                description=tool.description,
                required_scope=tool.required_scope,
                approval_required=self._requires_approval(tool),
                side_effect=tool.side_effect,
                input_schema=tool.arguments_model.model_json_schema(),
            )
            for tool in self._tools.values()
        ]

    def execute(
        self, request: MCPToolCallRequest, user: UserContext
    ) -> MCPToolCallResponse:
        execution = self.request_execution(
            MCPExecutionRequest(tool_name=request.tool_name, arguments=request.arguments),
            user,
        )
        status_map = {
            "pending_approval": "approval_required",
            "completed": "completed",
            "blocked": "blocked",
            "rejected": "rejected",
            "failed": "failed",
        }
        messages = {
            "pending_approval": "Action requires human approval before execution.",
            "completed": "Tool call completed after security checks.",
            "blocked": execution.error or "Tool call was blocked by security policy.",
            "rejected": "Tool call was rejected by a reviewer.",
            "failed": execution.error or "Tool execution failed.",
        }
        return MCPToolCallResponse(
            status=status_map[execution.status],
            message=messages[execution.status],
            approval_id=execution.approval_id,
            execution_id=execution.execution_id,
            result=execution.result,
        )

    def request_execution(
        self, request: MCPExecutionRequest, user: UserContext
    ) -> MCPExecutionRecord:
        tool = self._get_tool(request.tool_name)
        require_scope(user.scopes, tool.required_scope)
        arguments = self._validate_arguments(tool, request.arguments)
        arguments_hash = self._hash_arguments(arguments)
        execution_id = f"mcp_{uuid4().hex}"

        unsafe_reasons = self._unsafe_reasons(arguments)
        if unsafe_reasons and policy_service.unsafe_content_blocks_tools(True):
            execution = self._insert_execution(
                execution_id=execution_id,
                tool=tool,
                user=user,
                arguments=arguments,
                arguments_hash=arguments_hash,
                execution_status="blocked",
                error="Prompt safety policy blocked: " + ", ".join(unsafe_reasons),
            )
            self._audit(execution, "mcp.execution_blocked")
            return execution

        if self._requires_approval(tool):
            execution = self._insert_execution(
                execution_id=execution_id,
                tool=tool,
                user=user,
                arguments=arguments,
                arguments_hash=arguments_hash,
                execution_status="pending_approval",
            )
            approval = approval_service.create(
                action_id=tool.name,
                requested_by=user.user_id,
                execution_id=execution_id,
                arguments_hash=arguments_hash,
            )
            execution = self._update_execution(
                execution_id,
                status="pending_approval",
                approval_id=approval.approval_id,
            )
            self._audit(execution, "mcp.approval_requested")
            return execution

        execution = self._insert_execution(
            execution_id=execution_id,
            tool=tool,
            user=user,
            arguments=arguments,
            arguments_hash=arguments_hash,
            execution_status="running",
        )
        return self._run(execution, tool, user)

    def apply_approval(self, approval_id: str) -> MCPExecutionRecord | None:
        approval = approval_service.get(approval_id)
        if approval is None or approval.execution_id is None:
            return None
        execution = self.get_execution(approval.execution_id)
        if execution is None:
            return None
        if approval.status == "rejected":
            execution = self._update_execution(execution.execution_id, status="rejected")
            self._audit(execution, "mcp.execution_rejected")
            return execution
        if approval.status != "approved" or execution.status != "pending_approval":
            return execution

        actual_hash = self._hash_arguments(execution.arguments)
        if (
            actual_hash != execution.arguments_hash
            or approval.arguments_hash != execution.arguments_hash
        ):
            execution = self._update_execution(
                execution.execution_id,
                status="blocked",
                error="Stored tool arguments no longer match the approved payload hash.",
            )
            self._audit(execution, "mcp.execution_tamper_blocked")
            return execution

        tool = self._get_tool(execution.tool_name)
        user = user_service.get_by_id(execution.requested_by)
        if user is None:
            execution = self._update_execution(
                execution.execution_id,
                status="failed",
                error="The requesting user no longer exists.",
            )
            self._audit(execution, "mcp.execution_failed")
            return execution

        self._update_execution(execution.execution_id, status="running")
        return self._run(execution, tool, user)

    def list_executions(self, user: UserContext) -> list[MCPExecutionRecord]:
        where_clause = "" if user.role in {"admin", "manager"} else "WHERE requested_by = ?"
        params = () if not where_clause else (user.user_id,)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM mcp_tool_executions
                {where_clause}
                ORDER BY created_at DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
        return [self._row_to_execution(row) for row in rows]

    def get_execution(self, execution_id: str) -> MCPExecutionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM mcp_tool_executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return self._row_to_execution(row) if row is not None else None

    def can_view_execution(self, execution: MCPExecutionRecord, user: UserContext) -> bool:
        return user.role in {"admin", "manager"} or execution.requested_by == user.user_id

    def _run(
        self, execution: MCPExecutionRecord, tool: SecureTool, user: UserContext
    ) -> MCPExecutionRecord:
        try:
            arguments = tool.arguments_model.model_validate(execution.arguments)
            result = tool.handler(arguments, user, execution.execution_id)
            completed = self._update_execution(
                execution.execution_id,
                status="completed",
                result=result,
                error=None,
            )
            self._audit(completed, "mcp.execution_completed")
            return completed
        except Exception as exc:
            failed = self._update_execution(
                execution.execution_id,
                status="failed",
                error=str(exc),
            )
            self._audit(failed, "mcp.execution_failed")
            return failed

    def _insert_execution(
        self,
        *,
        execution_id: str,
        tool: SecureTool,
        user: UserContext,
        arguments: dict[str, object],
        arguments_hash: str,
        execution_status: str,
        error: str | None = None,
    ) -> MCPExecutionRecord:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO mcp_tool_executions (
                    execution_id, tool_name, requested_by, required_scope,
                    arguments_json, arguments_hash, status, result_json, error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    tool.name,
                    user.user_id,
                    tool.required_scope,
                    encode_json(arguments),
                    arguments_hash,
                    execution_status,
                    encode_json({}),
                    error,
                ),
            )
        execution = self.get_execution(execution_id)
        if execution is None:  # pragma: no cover
            raise RuntimeError("Tool execution could not be persisted.")
        return execution

    def _update_execution(
        self,
        execution_id: str,
        *,
        status: str,
        approval_id: str | None = None,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> MCPExecutionRecord:
        fields = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: list[object] = [status]
        if approval_id is not None:
            fields.append("approval_id = ?")
            params.append(approval_id)
        if result is not None:
            fields.append("result_json = ?")
            params.append(encode_json(result))
        fields.append("error = ?")
        params.append(error)
        params.append(execution_id)
        with get_connection() as connection:
            connection.execute(
                f"UPDATE mcp_tool_executions SET {', '.join(fields)} WHERE execution_id = ?",
                params,
            )
        execution = self.get_execution(execution_id)
        if execution is None:  # pragma: no cover
            raise RuntimeError("Tool execution disappeared during update.")
        return execution

    def _get_tool(self, tool_name: str) -> SecureTool:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown MCP tool: {tool_name}")
        return tool

    @staticmethod
    def _validate_arguments(
        tool: SecureTool, raw_arguments: dict[str, object]
    ) -> dict[str, object]:
        try:
            arguments = tool.arguments_model.model_validate(raw_arguments)
        except ValidationError as exc:
            raise ValueError(f"Invalid arguments for {tool.name}: {exc}") from exc
        return arguments.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _hash_arguments(arguments: dict[str, object]) -> str:
        canonical = json.dumps(
            arguments,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _text_values(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            values: list[str] = []
            for nested in value.values():
                values.extend(MCPGatewayService._text_values(nested))
            return values
        if isinstance(value, list):
            values = []
            for nested in value:
                values.extend(MCPGatewayService._text_values(nested))
            return values
        return []

    def _unsafe_reasons(self, arguments: dict[str, object]) -> list[str]:
        reasons: set[str] = set()
        for text in self._text_values(arguments):
            reasons.update(prompt_guard_service.scan_text(text[:20_000]).reasons)
        return sorted(reasons)

    @staticmethod
    def _row_to_execution(row) -> MCPExecutionRecord:
        return MCPExecutionRecord(
            execution_id=row["execution_id"],
            tool_name=row["tool_name"],
            requested_by=row["requested_by"],
            required_scope=row["required_scope"],
            arguments=decode_json(row["arguments_json"], {}),
            arguments_hash=row["arguments_hash"],
            status=row["status"],
            approval_id=row["approval_id"],
            result=decode_json(row["result_json"], {}),
            error=row["error"],
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )

    @staticmethod
    def _audit(execution: MCPExecutionRecord, event_type: str) -> None:
        audit_service.record(
            actor_id=execution.requested_by,
            event_type=event_type,
            detail={
                "execution_id": execution.execution_id,
                "tool": execution.tool_name,
                "status": execution.status,
                "approval_id": execution.approval_id or "",
                "arguments_hash": execution.arguments_hash,
            },
        )

    @staticmethod
    def _search_documents(
        arguments: BaseModel, user: UserContext, execution_id: str
    ) -> dict[str, object]:
        payload = SearchDocumentsArguments.model_validate(arguments)
        answer = rag_service.answer(question=payload.question, role=user.role)
        return answer.model_dump(mode="json")

    @staticmethod
    def _create_task(
        arguments: BaseModel, user: UserContext, execution_id: str
    ) -> dict[str, object]:
        payload = CreateTaskArguments.model_validate(arguments)
        task_id = f"task_{uuid4().hex}"
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM workspace_tasks WHERE source_execution_id = ?",
                (execution_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO workspace_tasks (
                        task_id, title, description, due_date, status,
                        created_by, source_execution_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        payload.title,
                        payload.description,
                        payload.due_date,
                        "open",
                        user.user_id,
                        execution_id,
                    ),
                )
            else:
                task_id = existing["task_id"]
        return {
            "task_id": task_id,
            "title": payload.title,
            "status": "open",
            "due_date": payload.due_date or "",
        }

    @staticmethod
    def _simulate_email(
        arguments: BaseModel, user: UserContext, execution_id: str
    ) -> dict[str, object]:
        payload = SendEmailArguments.model_validate(arguments)
        return {
            "delivery_mode": "simulated",
            "delivery_status": "simulated",
            "to": payload.to,
            "subject": payload.subject,
            "message": "Approval completed. No external email was sent.",
        }

    @staticmethod
    def _export_data(
        arguments: BaseModel, user: UserContext, execution_id: str
    ) -> dict[str, object]:
        payload = ExportDataArguments.model_validate(arguments)
        documents = rag_service.list_documents(role=user.role)
        if payload.classification != "all":
            documents = [
                document
                for document in documents
                if document.classification == payload.classification
            ]
        rows = [
            {
                "document_id": document.document_id,
                "title": document.title,
                "classification": document.classification,
                "owner_team": document.owner_team,
            }
            for document in documents[: payload.limit]
        ]
        return {
            "format": "json",
            "row_count": len(rows),
            "rows": rows,
        }

    @staticmethod
    def _requires_approval(tool: SecureTool) -> bool:
        settings = get_settings()
        configured = (
            tool.name == "send_email" and settings.require_approval_for_send_email
        ) or (tool.name == "export_data" and settings.require_approval_for_export)
        return tool.approval_required or configured or policy_service.tool_requires_approval(
            tool.name
        )


mcp_gateway_service = MCPGatewayService()
