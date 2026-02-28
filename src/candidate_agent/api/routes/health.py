"""Health check endpoints."""

import httpx
import structlog
from fastapi import APIRouter, Depends

from candidate_agent.api.dependencies import get_settings
from candidate_agent.api.schemas import HealthResponse
from candidate_agent.config import Settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Liveness + MCP server reachability check.

    Returns 200 regardless of MCP connectivity so the process stays alive;
    ``mcp_connected`` indicates actual connectivity status.
    """
    mcp_ok = await _check_mcp(settings.mcp_server_url, settings.mcp_connect_timeout)
    return HealthResponse(
        status="healthy",
        mcp_connected=mcp_ok,
        llm_model=settings.llm_model,
    )


async def _check_mcp(url: str, timeout: int) -> bool:
    """Probe the MCP server with a minimal HTTP GET.

    The stateless MCP endpoint returns 4xx on GET (it expects POST + SSE), but a
    connection-refused or DNS failure indicates the server is down.
    """
    try:
        async with httpx.AsyncClient(timeout=float(timeout)) as client:
            resp = await client.get(url)
            # 4xx means the server is up but rejected the bare GET — that's fine.
            return resp.status_code < 500
    except Exception as exc:
        logger.warning("mcp_health_check_failed", url=url, error=str(exc))
        return False
