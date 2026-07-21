from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from study_agent import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["study-agent-api"] = "study-agent-api"
    version: str


@router.get("/healthz", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)
