from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.schemas import AgentPlanRequest, AgentPlanResponse
from app.services.agent import agent_service
from app.services.audit import audit_service

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
