from fastapi import APIRouter, Request
from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthStatus)
async def liveness() -> HealthStatus:
    return HealthStatus(status="ok")


@router.get("/ready", response_model=HealthStatus)
async def readiness(request: Request) -> HealthStatus:
    if not request.app.state.ready:
        return HealthStatus(status="not_ready")
    return HealthStatus(status="ready")

