"""LangGraph multi-agent graphs.

v1 graph  (build_graph):
    START → [candidate_primary] ──(handoff)──► [job_application_agent] → END
                    └──────────────────────────────────────────────────► END

v2 graph  (build_v2_graph):
    START → [v2_primary_assistant] ──(handoff)──► [post_apply_assistant] → END
                    └─────────────────────────────────────────────────────► END

Routing (v1):
  • candidate_primary answers directly for profile, assessment, job, and schema queries.
  • For application / journey / next-steps queries it calls transfer_to_job_application_agent
    (Command with graph=Command.PARENT) to route in the parent StateGraph.

Routing (v2):
  • v2_primary_assistant is a thin router — it calls transfer_to_post_apply_assistant
    for all candidate domain queries and may answer trivial meta-questions directly.
  • post_apply_assistant runs with 12 tools covering profile, application, job, and
    assessment domains. It is the only node that calls candidate-mcp tools in v2.

Production note:
  Replace MemorySaver with AsyncRedisSaver (langgraph-checkpoint-redis) for
  distributed deployments with multiple workers/pods.
"""

import structlog
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

from candidate_agent.agents.llm import build_llm
from candidate_agent.agents.prompts import (
    build_job_app_prompt,
    build_post_apply_prompt,
    build_primary_prompt,
    build_v2_primary_prompt,
)
from candidate_agent.agents.state import CandidateAgentState, PostApplyAgentState
from candidate_agent.config import Settings
from candidate_agent.mcp.client import MCPToolRegistry

logger = structlog.get_logger(__name__)


def _build_context_block(
    state: dict,
    instruction_with_app: str,
    instruction_without_app: str,
) -> str:
    """Append a ## Active Request Context section to a system prompt string.

    Reads ``candidate_id`` and ``application_id`` from LangGraph state and
    injects them so the LLM never has to ask the user for IDs that were
    already supplied in the API request.

    ``candidate_id`` is always mandatory.
    ``application_id`` is optional — different instructions are injected
    depending on whether it is present, so the LLM knows whether to operate
    on a specific application or on the candidate's full application history.
    """
    parts = []
    if state.get("candidate_id"):
        parts.append(f"candidateId: {state['candidate_id']}")

    if state.get("application_id"):
        parts.append(f"applicationId: {state['application_id']}")
        instruction = instruction_with_app
    else:
        instruction = instruction_without_app

    if not parts:
        return ""
    return (
        "\n\n## Active Request Context\n"
        + "\n".join(parts)
        + f"\n{instruction}"
    )


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
        version="v1",
        primary_tools=len(registry.all_tools) + 1,  # +1 for handoff
        app_tools=len(registry.app_tools),
    )
    return graph


def build_v2_graph(registry: MCPToolRegistry, settings: Settings):
    """Compile the v2 multi-agent StateGraph.

    The v2 graph contains two nodes:
    - v2_primary_assistant: thin router with only the handoff tool.
    - post_apply_assistant: specialist with 12 candidate-domain tools.

    Args:
        registry: Pre-loaded MCP tool registry (post_apply_tools populated).
        settings:  Application settings (LLM model, temperature, API key).

    Returns:
        A compiled LangGraph CompiledStateGraph ready to invoke.
    """
    llm = build_llm(settings)

    # ── Handoff tool ─────────────────────────────────────────────────────────
    @tool
    def transfer_to_post_apply_assistant(reason: str) -> Command:  # type: ignore[return]
        """Transfer to the Post-Apply Assistant specialist.

        Use this for ANY query about:
        - Candidate profile, skills, experience, or education
        - Application status, history, timeline, or next steps
        - Interview feedback or stage duration
        - Assessment results, scores, or percentile comparisons
        - Skills gap between the candidate and a role
        - Job details for a role the candidate applied to

        Args:
            reason: Brief description of why you are transferring.
        """
        logger.info("handoff_to_post_apply_assistant", reason=reason)
        return Command(
            goto="post_apply_assistant",
            update={"active_agent": "post_apply_assistant"},
            graph=Command.PARENT,
        )

    # ── Build base system prompt strings ─────────────────────────────────────
    v2_primary_base = build_v2_primary_prompt(
        workflow_json=registry.workflow_states_json,
        assessment_types_json=registry.assessment_types_json,
    )
    post_apply_base = build_post_apply_prompt(
        workflow_json=registry.workflow_states_json,
        assessment_types_json=registry.assessment_types_json,
        candidate_schema_json=registry.candidate_schema_json,
        application_schema_json=registry.application_schema_json,
    )
    logger.info(
        "v2_prompts_built",
        schemas_embedded=bool(
            registry.candidate_schema_json or registry.application_schema_json
        ),
        workflow_embedded=bool(registry.workflow_states_json),
    )

    # ── Callable prompt wrappers — inject candidate_id/application_id from state
    # The LLM only sees the messages list; state fields like candidate_id are
    # invisible without explicit injection. These closures append an
    # "## Active Request Context" block so the LLM never asks the user for
    # IDs that were already supplied in the API request.
    def v2_primary_prompt(state: PostApplyAgentState):
        extra = _build_context_block(
            state,
            instruction_with_app=(
                "Route immediately — candidateId and applicationId are already known."
            ),
            instruction_without_app=(
                "Route immediately — candidateId is known. "
                "No specific application was provided; the specialist will retrieve "
                "all applications for this candidate."
            ),
        )
        return [SystemMessage(content=v2_primary_base + extra)] + state["messages"]

    def post_apply_prompt(state: PostApplyAgentState):
        extra = _build_context_block(
            state,
            instruction_with_app=(
                "A specific application is in scope. "
                "Use both IDs directly in tool calls — do not ask the candidate for them."
            ),
            instruction_without_app=(
                "No specific application was provided. "
                "Call getApplicationsByCandidate(candidateId) to retrieve the full list "
                "of applications for this candidate — do not ask the candidate for an "
                "application ID."
            ),
        )
        return [SystemMessage(content=post_apply_base + extra)] + state["messages"]

    # ── post_apply_assistant (specialist, 12 tools) ──────────────────────────
    post_apply_agent = create_react_agent(
        model=llm,
        tools=registry.post_apply_tools,
        prompt=post_apply_prompt,
        state_schema=PostApplyAgentState,
        name="post_apply_assistant",
    )

    # ── v2_primary_assistant (router, handoff tool only) ─────────────────────
    v2_primary_agent = create_react_agent(
        model=llm,
        tools=[transfer_to_post_apply_assistant],
        prompt=v2_primary_prompt,
        state_schema=PostApplyAgentState,
        name="v2_primary_assistant",
    )

    # ── Graph wiring ─────────────────────────────────────────────────────────
    builder = StateGraph(PostApplyAgentState)
    builder.add_node("v2_primary_assistant", v2_primary_agent)
    builder.add_node("post_apply_assistant", post_apply_agent)

    builder.add_edge(START, "v2_primary_assistant")
    # Primary edges to END when it answers trivial meta-questions directly.
    builder.add_edge("v2_primary_assistant", END)
    # post_apply_assistant edges to END after completing its candidate-facing response.
    builder.add_edge("post_apply_assistant", END)

    checkpointer = MemorySaver()
    v2_graph = builder.compile(checkpointer=checkpointer)

    logger.info(
        "graph_compiled",
        version="v2",
        post_apply_tools=len(registry.post_apply_tools),
    )
    return v2_graph
