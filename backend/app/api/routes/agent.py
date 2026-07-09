from fastapi import APIRouter, Depends

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
    )
    return workflow


@router.get("/workflows", response_model=list[AgentWorkflowRecord])
def list_workflows(user=Depends(get_current_user)) -> list[AgentWorkflowRecord]:
    audit_service.record(
        actor_id=user.user_id,
        event_type="agent.workflow_list",
        detail={"role": user.role},
    )
    return workflow_service.list_workflows()
