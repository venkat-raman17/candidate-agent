"""FastAPI application entry point.

Lifespan:
  startup  — configure logging, init MCP registry, compile LangGraph
  shutdown — no explicit teardown needed (MCP client is stateless per-call)
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from candidate_agent.agents.graph import build_graph
from candidate_agent.api.routes.agent import router as agent_router
from candidate_agent.api.routes.health import router as health_router
from candidate_agent.config import settings
from candidate_agent.logging_setup import configure_logging
from candidate_agent.mcp.client import init_registry

# Configure structured logging before anything else uses the logger
configure_logging(settings.log_level)

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    llm_backend = (
        f"local:{settings.local_llm_model}@{settings.local_llm_base_url}"
        if settings.local_llm
        else f"anthropic:{settings.llm_model}"
    )
    logger.info(
        "startup",
        mcp_server=settings.mcp_server_url,
        llm_backend=llm_backend,
        app_port=settings.app_port,
    )

    # Load MCP tools from candidate-mcp server
    registry = await init_registry(settings)

    # Compile the multi-agent LangGraph
    graph = build_graph(registry, settings)

    # Attach to app state so dependencies can access them
    app.state.mcp_registry = registry
    app.state.graph = graph
    app.state.settings = settings

    logger.info("startup_complete")
    yield
    logger.info("shutdown")


app = FastAPI(
    title="Candidate Agent",
    description=(
        "LangGraph multi-agent system exposing ATS candidate domain intelligence "
        "via a Candidate Primary Agent and a Job Application Status sub-agent."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(agent_router, prefix="/api/v1/agent")
app.include_router(health_router)
