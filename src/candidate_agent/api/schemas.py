from uuid import uuid4

from pydantic import BaseModel, Field


class InvokeRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    candidate_id: str = Field(
        default="",
        description="Candidate ID the agent is acting on behalf of (e.g. C001)",
    )
    thread_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Conversation thread ID for multi-turn context. Auto-generated if omitted.",
    )
    correlation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Request trace ID for observability. Auto-generated if omitted.",
    )


class InvokeResponse(BaseModel):
    thread_id: str
    correlation_id: str
    response: str = Field(..., description="Final agent response text")
    agent_used: str = Field(
        ...,
        description="Last agent that produced the final answer",
    )
    tool_calls: list[str] = Field(
        default_factory=list,
        description="Names of tools invoked during this run",
    )


class StreamRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    candidate_id: str = Field(default="")
    thread_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))


class HealthResponse(BaseModel):
    status: str
    mcp_connected: bool
    llm_model: str
    version: str = "1.0.0"


# ── v2 route schemas ──────────────────────────────────────────────────────────

class V2InvokeRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    candidate_id: str = Field(
        default="",
        description="Candidate ID the assistant is acting on behalf of (e.g. C001)",
    )
    application_id: str = Field(
        default="",
        description="Optional application ID when the query is about a specific application",
    )
    thread_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Conversation thread ID for multi-turn context. Auto-generated if omitted.",
    )
    correlation_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Request trace ID for observability. Auto-generated if omitted.",
    )


class V2StreamRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    candidate_id: str = Field(default="")
    application_id: str = Field(default="")
    thread_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid4()))
