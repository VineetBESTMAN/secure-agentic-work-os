from app.core.config import get_settings
from app.core.rbac import require_scope
from app.models.schemas import MCPToolCallRequest, MCPToolCallResponse, UserContext
from app.services.approval import approval_service
from app.services.policies import policy_service


class MCPGatewayService:
    def execute(
        self, request: MCPToolCallRequest, user: UserContext
    ) -> MCPToolCallResponse:
        require_scope(user.scopes, request.scope)

        settings = get_settings()
        approval_required = (
            request.tool_name == "send_email" and settings.require_approval_for_send_email
        ) or (request.tool_name == "export_data" and settings.require_approval_for_export)
        approval_required = approval_required or policy_service.tool_requires_approval(
            request.tool_name
        )

        if approval_required:
            approval = approval_service.create(
                action_id=request.tool_name,
                requested_by=user.user_id,
            )
            return MCPToolCallResponse(
                status="approval_required",
                message="Action requires human approval before execution.",
                approval_id=approval.approval_id,
            )

        return MCPToolCallResponse(
            status="allowed",
            message="Tool call allowed by MCP gateway policy checks.",
        )


mcp_gateway_service = MCPGatewayService()
