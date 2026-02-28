from fastapi import Request

from candidate_agent.agents.graph import build_graph  # noqa: F401 — re-exported for type hints
from candidate_agent.config import Settings
from candidate_agent.mcp.client import MCPToolRegistry


def get_graph(request: Request):
    """FastAPI dependency: returns the compiled LangGraph from app state."""
    return request.app.state.graph


def get_registry(request: Request) -> MCPToolRegistry:
    """FastAPI dependency: returns the MCP tool registry from app state."""
    return request.app.state.mcp_registry


def get_settings(request: Request) -> Settings:
    """FastAPI dependency: returns app settings from app state."""
    return request.app.state.settings
