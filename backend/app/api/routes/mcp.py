from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.schemas import MCPToolCallRequest, MCPToolCallResponse
from app.services.audit import audit_service
from app.services.mcp_gateway import mcp_gateway_service

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.post("/tool-call", response_model=MCPToolCallResponse)
def tool_call(
    payload: MCPToolCallRequest, user=Depends(get_current_user)
) -> MCPToolCallResponse:
    response = mcp_gateway_service.execute(request=payload, user=user)
    audit_service.record(
        actor_id=user.user_id,
        event_type="mcp.tool_call",
        detail={
            "tool": payload.tool_name,
            "approved": response.status != "blocked",
            "status": response.status,
        },
    )
    return response
