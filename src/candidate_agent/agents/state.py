from typing import Annotated

from langgraph.graph import MessagesState
from langgraph.managed.is_last_step import RemainingStepsManager
from typing_extensions import NotRequired


class CandidateAgentState(MessagesState):
    """Shared state for the v1 multi-agent graph (primary + job_application_agent).

    Inherits ``messages: Annotated[list[AnyMessage], add_messages]`` from MessagesState.
    Extra fields carry per-request context for logging and routing.

    ``remaining_steps`` must be declared exactly as in LangGraph's own ``AgentState``
    (``NotRequired[Annotated[int, RemainingStepsManager]]``) when passing a custom
    ``state_schema`` to ``create_react_agent`` — it tracks recursion depth internally.
    """

    candidate_id: str   # The candidate this session is acting on behalf of
    correlation_id: str  # Trace ID propagated from the HTTP request
    active_agent: str   # Last agent to produce output ("candidate_primary" | "job_application_agent")
    remaining_steps: NotRequired[Annotated[int, RemainingStepsManager]]


class PostApplyAgentState(MessagesState):
    """State for the v2 graph (v2_primary_assistant + post_apply_assistant).

    Extends CandidateAgentState with ``application_id`` so the v2 primary can pass a
    specific application into context when routing to post_apply_assistant.
    """

    candidate_id: str    # Candidate this session is acting on behalf of
    application_id: str  # Optional: specific application the query is about
    correlation_id: str  # Trace ID propagated from the HTTP request
    active_agent: str    # Last agent to produce output ("v2_primary_assistant" | "post_apply_assistant")
    remaining_steps: NotRequired[Annotated[int, RemainingStepsManager]]
