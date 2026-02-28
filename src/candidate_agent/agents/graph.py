"""LangGraph multi-agent graph: Candidate Primary + Job Application sub-agent.

Graph topology:
    START
      │
      ▼
  [candidate_primary]  ── (handoff tool) ──► [job_application_agent]
      │                                              │
      └──────────── END ◄────────────────────────────┘

Routing:
  • candidate_primary answers directly for profile, assessment, job, and schema queries.
  • For application status / journey / next-steps, it calls `transfer_to_job_application_agent`,
    which returns Command(goto="job_application_agent", graph=Command.PARENT) to exit the
    react-agent subgraph and route in the parent StateGraph.
  • job_application_agent runs to completion and edges to END.
  • candidate_primary also edges to END when it answers directly (no handoff).

Production note:
  Replace MemorySaver with AsyncPostgresSaver (langgraph-checkpoint-postgres) or
  AsyncRedisSaver (langgraph-checkpoint-redis) for distributed deployments.
"""

import structlog
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

from candidate_agent.agents.llm import build_llm
from candidate_agent.agents.prompts import build_job_app_prompt, build_primary_prompt
from candidate_agent.agents.state import CandidateAgentState
from candidate_agent.config import Settings
from candidate_agent.mcp.client import MCPToolRegistry

logger = structlog.get_logger(__name__)


def build_graph(registry: MCPToolRegistry, settings: Settings):
    """Compile the multi-agent StateGraph.

    Args:
        registry: Pre-loaded MCP tool registry (all_tools + app_tools).
        settings:  Application settings (LLM model, temperature, API key).

    Returns:
        A compiled LangGraph CompiledStateGraph ready to invoke.
    """
    llm = build_llm(settings)

    # ── Handoff tool ─────────────────────────────────────────────────────────
    # Returning Command with graph=Command.PARENT exits the react-agent subgraph
    # and routes in the parent StateGraph (our custom CandidateAgentState graph).
    @tool
    def transfer_to_job_application_agent(reason: str) -> Command:  # type: ignore[return]
        """Transfer to the Job Application Status specialist agent.

        Use this when the user asks about:
        - Application status for a specific application or candidate
        - Candidate journey or timeline narrative
        - Next steps or guidance for a particular application stage
        - How long a candidate has been in a stage (stage duration)
        - Interview feedback or rounds

        Args:
            reason: Brief description of why you are transferring to this agent.
        """
        logger.info("handoff_to_job_application_agent", reason=reason)
        return Command(
            goto="job_application_agent",
            update={"active_agent": "job_application_agent"},
            graph=Command.PARENT,
        )

    # ── Build resource-enriched system prompts ───────────────────────────────
    primary_prompt = build_primary_prompt(
        workflow_json=registry.workflow_states_json,
        assessment_types_json=registry.assessment_types_json,
    )
    job_app_prompt = build_job_app_prompt(
        workflow_json=registry.workflow_states_json,
    )
    logger.info(
        "prompts_built",
        primary_enriched=bool(registry.workflow_states_json or registry.assessment_types_json),
        job_app_enriched=bool(registry.workflow_states_json),
    )

    # ── Job Application sub-agent ────────────────────────────────────────────
    job_app_agent = create_react_agent(
        model=llm,
        tools=registry.app_tools,
        prompt=job_app_prompt,
        state_schema=CandidateAgentState,
        name="job_application_agent",
    )

    # ── Candidate Primary agent ──────────────────────────────────────────────
    # Has all tools plus the handoff tool.
    primary_agent = create_react_agent(
        model=llm,
        tools=[*registry.all_tools, transfer_to_job_application_agent],
        prompt=primary_prompt,
        state_schema=CandidateAgentState,
        name="candidate_primary",
    )

    # ── Graph wiring ─────────────────────────────────────────────────────────
    builder = StateGraph(CandidateAgentState)
    builder.add_node("candidate_primary", primary_agent)
    builder.add_node("job_application_agent", job_app_agent)

    builder.add_edge(START, "candidate_primary")
    # Primary edges to END when it answers directly (no handoff).
    builder.add_edge("candidate_primary", END)
    # Sub-agent edges to END after producing its narrative response.
    builder.add_edge("job_application_agent", END)

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    logger.info(
        "graph_compiled",
        primary_tools=len(registry.all_tools) + 1,  # +1 for handoff
        app_tools=len(registry.app_tools),
    )
    return graph
