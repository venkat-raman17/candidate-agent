from fastapi import Request

from candidate_agent.agents.graph import build_graph, build_v2_graph  # noqa: F401
from candidate_agent.config import Settings
from candidate_agent.mcp.client import MCPToolRegistry


def get_graph(request: Request):
    """FastAPI dependency: returns the compiled v1 LangGraph from app state."""
    return request.app.state.graph


def get_v2_graph(request: Request):
    """FastAPI dependency: returns the compiled v2 LangGraph from app state."""
    return request.app.state.v2_graph


def get_registry(request: Request) -> MCPToolRegistry:
    """FastAPI dependency: returns the MCP tool registry from app state."""
    return request.app.state.mcp_registry


def get_settings(request: Request) -> Settings:
    """FastAPI dependency: returns app settings from app state."""
    return request.app.state.settings
