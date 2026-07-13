from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.schemas import (
    MCPExecutionRecord,
    MCPExecutionRequest,
    MCPToolCallRequest,
    MCPToolCallResponse,
    MCPToolDefinition,
)
from app.services.mcp_gateway import mcp_gateway_service

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/tools", response_model=list[MCPToolDefinition])
def list_tools(user=Depends(get_current_user)) -> list[MCPToolDefinition]:
    return mcp_gateway_service.list_tools()


@router.get("/executions", response_model=list[MCPExecutionRecord])
def list_executions(user=Depends(get_current_user)) -> list[MCPExecutionRecord]:
    return mcp_gateway_service.list_executions(user)


@router.get("/executions/{execution_id}", response_model=MCPExecutionRecord)
def get_execution(
    execution_id: str, user=Depends(get_current_user)
) -> MCPExecutionRecord:
    execution = mcp_gateway_service.get_execution(execution_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP execution not found",
        )
    if not mcp_gateway_service.can_view_execution(execution, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view this execution",
        )
    return execution


@router.post("/executions", response_model=MCPExecutionRecord)
def create_execution(
    payload: MCPExecutionRequest, user=Depends(get_current_user)
) -> MCPExecutionRecord:
    try:
        return mcp_gateway_service.request_execution(payload, user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post("/tool-call", response_model=MCPToolCallResponse)
def tool_call(
    payload: MCPToolCallRequest, user=Depends(get_current_user)
) -> MCPToolCallResponse:
    try:
        return mcp_gateway_service.execute(request=payload, user=user)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
