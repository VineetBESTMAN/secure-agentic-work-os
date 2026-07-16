from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.schemas import ModelGatewayStatus
from app.services.model_gateway import model_gateway_service


router = APIRouter(prefix="/models", tags=["models"])


@router.get("/status", response_model=ModelGatewayStatus)
def model_status(_user=Depends(get_current_user)) -> ModelGatewayStatus:
    return model_gateway_service.status()
