from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness only: the process is up. Database and Neon checks belong to /ready (step 6)."""
    return HealthResponse(status="ok")
