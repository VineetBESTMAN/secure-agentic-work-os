from fastapi import APIRouter, Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.schemas import AgentPlanRequest, AgentPlanResponse, AgentWorkflowRecord, AgentWorkflowRequest
from app.services.agent import agent_service
from app.services.audit import audit_service
from app.services.workflows import workflow_service

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/plan", response_model=AgentPlanResponse)
def create_plan(
    payload: AgentPlanRequest, user=Depends(get_current_user)
) -> AgentPlanResponse:
    plan = agent_service.build_plan(prompt=payload.prompt, user=user)
    audit_service.record(
        actor_id=user.user_id,
        event_type="agent.plan",
        detail={"prompt": payload.prompt, "actions": len(plan.actions)},
        organization_id=user.organization_id,
    )
    return plan


@router.post("/workflows", response_model=AgentWorkflowRecord)
def create_workflow(
    payload: AgentWorkflowRequest, user=Depends(get_current_user)
) -> AgentWorkflowRecord:
    workflow = workflow_service.create_workflow(prompt=payload.prompt, user=user)
    audit_service.record(
        actor_id=user.user_id,
        event_type="agent.workflow_create",
        detail={"workflow_id": workflow.workflow_id, "status": workflow.status},
        organization_id=user.organization_id,
    )
    return workflow


@router.get("/workflows", response_model=list[AgentWorkflowRecord])
def list_workflows(user=Depends(get_current_user)) -> list[AgentWorkflowRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="agent.workflow_list",
        detail={"role": user.role},
        organization_id=user.organization_id,
    )
    return workflow_service.list_workflows(user)


@router.get("/workflows/{workflow_id}", response_model=AgentWorkflowRecord)
def get_workflow(
    workflow_id: str, user=Depends(get_current_user)
) -> AgentWorkflowRecord:
    try:
        workflow = workflow_service.get_workflow(workflow_id, user)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    return workflow


@router.post("/workflows/{workflow_id}/resume", response_model=AgentWorkflowRecord)
def resume_workflow(
    workflow_id: str, user=Depends(get_current_user)
) -> AgentWorkflowRecord:
    return _run_workflow_command(workflow_id, "resume", user)


@router.post("/workflows/{workflow_id}/retry", response_model=AgentWorkflowRecord)
def retry_workflow(
    workflow_id: str, user=Depends(get_current_user)
) -> AgentWorkflowRecord:
    return _run_workflow_command(workflow_id, "retry", user)


@router.post("/workflows/{workflow_id}/cancel", response_model=AgentWorkflowRecord)
def cancel_workflow(
    workflow_id: str, user=Depends(get_current_user)
) -> AgentWorkflowRecord:
    return _run_workflow_command(workflow_id, "cancel", user)


def _run_workflow_command(
    workflow_id: str, command: str, user
) -> AgentWorkflowRecord:
    try:
        if command == "retry":
            workflow = workflow_service.retry_workflow(workflow_id, user)
        elif command == "cancel":
            workflow = workflow_service.cancel_workflow(workflow_id, user)
        else:
            workflow = workflow_service.run_workflow(workflow_id, user)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if str(exc) == "Workflow not found."
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    audit_service.record(
        actor_id=user.user_id,
        event_type=f"agent.workflow_{command}",
        detail={"workflow_id": workflow_id, "status": workflow.status},
        organization_id=user.organization_id,
    )
    return workflow
