"""MCP tool registry — loads and caches tools and static resources from candidate-mcp at startup.

langchain-mcp-adapters 0.2.x design:
  - MultiServerMCPClient is NOT a context manager; call `await client.get_tools()` directly.
  - Each tool invocation creates a fresh HTTP session to the stateless MCP server, which
    is exactly what we want — no persistent session state to manage.
  - get_resources(server_name, uris=[...]) fetches specific resource URIs (including templates).
  - Dynamic resource templates require explicit URIs; they are NOT returned by uris=None.
"""

from dataclasses import dataclass, field

import structlog
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from candidate_agent.config import Settings

logger = structlog.get_logger(__name__)

# Tools routed exclusively to the v1 Job Application sub-agent
APP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "getApplicationStatus",
        "getApplicationsByCandidate",
        "getCandidateJourney",
        "getNextSteps",
        "getStageDuration",
        "getInterviewFeedback",
    }
)

# Tools available to the v2 post_apply_assistant (profile + application + job + assessment)
POST_APPLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # Profile
        "getCandidateProfile",
        "getSkillsGap",
        "getCandidatePreferences",  # NEW: location, job, work style preferences
        # Application
        "getApplicationStatus",
        "getApplicationsByCandidate",
        "getCandidateJourney",
        "getNextSteps",
        "getStageDuration",
        "getInterviewFeedback",
        "getApplicationGroup",  # NEW: draft multi-job applications
        "getApplicationGroupsByCandidate",  # NEW: all draft applications
        "getScheduledEvents",  # NEW: upcoming interview schedule
        # Job enrichment — application.jobId → full job details (title, location, assessment codes)
        "getJob",
        # Assessment
        "getAssessmentResults",
        "getAssessmentByType",
        "compareToPercentile",
    }
)

# Static knowledge-base resources to embed in agent system prompts at startup
_KNOWLEDGE_URIS = [
    "ats://workflow/application-states",
    "ats://workflow/assessment-types",
    "ats://schema/candidate",
    "ats://schema/application",
]


def _blob_text(blobs, uri: str) -> str:
    """Extract the text content of a Blob whose metadata['uri'] matches ``uri``."""
    for blob in blobs:
        if blob.metadata.get("uri") == uri:
            raw = blob.data
            return raw if isinstance(raw, str) else raw.decode("utf-8")
    return ""


@dataclass
class MCPToolRegistry:
    """Holds the MCP client, pre-loaded tool lists, and static knowledge resources.

    Fields loaded at startup (all_tools, app_tools) are used by the LangGraph agents.
    Knowledge fields (workflow_states_json, etc.) are embedded into system prompts so the
    LLM understands the ATS domain without needing tool calls for every request.
    """

    client: MultiServerMCPClient
    all_tools: list[BaseTool] = field(default_factory=list)
    app_tools: list[BaseTool] = field(default_factory=list)
    post_apply_tools: list[BaseTool] = field(default_factory=list)

    # Static knowledge resources — embedded into agent system prompts
    workflow_states_json: str = ""      # ats://workflow/application-states
    assessment_types_json: str = ""     # ats://workflow/assessment-types
    candidate_schema_json: str = ""     # ats://schema/candidate
    application_schema_json: str = ""   # ats://schema/application


async def init_registry(settings: Settings) -> MCPToolRegistry:
    """Create the MCP client, load all tools and static knowledge resources.

    Called once during FastAPI lifespan startup.
    """
    client = MultiServerMCPClient(
        {
            "candidate_mcp": {
                "url": settings.mcp_server_url,
                "transport": "streamable_http",
                # Stateless MCP server requires both media types in Accept
                "headers": {
                    "Accept": "application/json, text/event-stream",
                },
            }
        }
    )

    log = logger.bind(server=settings.mcp_server_url)

    # ── Load tools ────────────────────────────────────────────────────────────
    log.info("loading_mcp_tools")
    all_tools: list[BaseTool] = await client.get_tools()
    app_tools = [t for t in all_tools if t.name in APP_TOOL_NAMES]
    post_apply_tools = [t for t in all_tools if t.name in POST_APPLY_TOOL_NAMES]
    log.info(
        "mcp_tools_loaded",
        total=len(all_tools),
        app_agent_tools=len(app_tools),
        post_apply_tools=len(post_apply_tools),
        all_tool_names=[t.name for t in all_tools],
        app_tool_names=[t.name for t in app_tools],
        post_apply_tool_names=[t.name for t in post_apply_tools],
    )

    # ── Load static knowledge resources for system prompt enrichment ──────────
    workflow_states_json = assessment_types_json = candidate_schema_json = application_schema_json = ""
    try:
        log.info("loading_mcp_resources", uris=_KNOWLEDGE_URIS)
        blobs = await client.get_resources("candidate_mcp", uris=_KNOWLEDGE_URIS)
        workflow_states_json = _blob_text(blobs, "ats://workflow/application-states")
        assessment_types_json = _blob_text(blobs, "ats://workflow/assessment-types")
        candidate_schema_json = _blob_text(blobs, "ats://schema/candidate")
        application_schema_json = _blob_text(blobs, "ats://schema/application")
        log.info(
            "mcp_resources_loaded",
            loaded=[uri for uri in _KNOWLEDGE_URIS if _blob_text(blobs, uri)],
        )
    except Exception as exc:
        # Resource loading is best-effort — agents still work without prompt enrichment
        log.warning("mcp_resources_load_failed", error=str(exc))

    return MCPToolRegistry(
        client=client,
        all_tools=all_tools,
        app_tools=app_tools,
        post_apply_tools=post_apply_tools,
        workflow_states_json=workflow_states_json,
        assessment_types_json=assessment_types_json,
        candidate_schema_json=candidate_schema_json,
        application_schema_json=application_schema_json,
    )
