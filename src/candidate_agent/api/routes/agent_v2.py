"""v2 agent endpoints: post_apply_assistant via v2 primary router.

Mirrors the v1 agent route but is wired to the v2 graph (v2_primary_assistant +
post_apply_assistant) and accepts an optional ``application_id`` field that is
passed into the PostApplyAgentState.
"""

import json
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from langfuse.langchain import CallbackHandler
 
from candidate_agent.api.dependencies import get_v2_graph
from candidate_agent.api.schemas import InvokeResponse, V2InvokeRequest, V2StreamRequest
import os


LANGFUSE_SECRET_KEY="sk-lf-1205fdff-cde4-409a-9b14-b9798dfa1ec0"
LANGFUSE_PUBLIC_KEY="pk-lf-77cf9a70-8fe4-4d9e-9cde-3d8aab018b72"
LANGFUSE_BASE_URL="http://localhost:3000"

os.environ["LANGFUSE_SECRET_KEY"] = LANGFUSE_SECRET_KEY
os.environ["LANGFUSE_PUBLIC_KEY"] = LANGFUSE_PUBLIC_KEY
os.environ["LANGFUSE_BASE_URL"] = LANGFUSE_BASE_URL

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["agent-v2"])
langfuse_handler = CallbackHandler()

def _build_v2_input(
    message: str,
    candidate_id: str,
    application_id: str,
    correlation_id: str,
) -> dict:
    """Build the initial v2 graph state for a new turn."""
    return {
        "messages": [HumanMessage(content=message)],
        "candidate_id": candidate_id,
        "application_id": application_id,
        "correlation_id": correlation_id,
        "active_agent": "v2_primary_assistant",
    }


def _extract_result(final_state: dict, thread_id: str, correlation_id: str) -> InvokeResponse:
    """Pull the last AIMessage and tool-call names from the final v2 graph state."""
    messages = final_state.get("messages", [])

    response_text = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = (
                msg.content if isinstance(msg.content, str)
                else " ".join(
                    block.get("text", "")
                    for block in msg.content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
            break

    tool_calls: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tool_calls.extend(tc["name"] for tc in msg.tool_calls)

    return InvokeResponse(
        thread_id=thread_id,
        correlation_id=correlation_id,
        response=response_text,
        agent_used=final_state.get("active_agent", "v2_primary_assistant"),
        tool_calls=tool_calls,
    )


@router.post("/invoke", response_model=InvokeResponse)
async def v2_invoke(req: V2InvokeRequest, graph=Depends(get_v2_graph)) -> InvokeResponse:
    """Run the v2 agent graph synchronously and return the final response.

    Routes through v2_primary_assistant → post_apply_assistant for all
    candidate domain queries. The post_apply_assistant speaks directly to
    the candidate in plain, empathetic language.
    """
    log = logger.bind(
        thread_id=req.thread_id,
        correlation_id=req.correlation_id,
        candidate_id=req.candidate_id,
        application_id=req.application_id,
    )
    log.info("v2_invoke_start")

    config = {"configurable": {"thread_id": req.thread_id}, "callbacks": [langfuse_handler]}

    try:
        final_state = await graph.ainvoke(
            _build_v2_input(
                req.message, req.candidate_id, req.application_id, req.correlation_id
            ),
            config=config,
        )
    except Exception as exc:
        log.error("v2_invoke_error", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    result = _extract_result(final_state, req.thread_id, req.correlation_id)
    log.info("v2_invoke_complete", agent_used=result.agent_used, tool_calls=result.tool_calls)
    return result


@router.post("/stream")
async def v2_stream(req: V2StreamRequest, graph=Depends(get_v2_graph)) -> StreamingResponse:
    """Stream v2 agent events as Server-Sent Events (SSE).

    Event types emitted:
    - ``token``     — LLM token chunk (data: {content: str})
    - ``tool_call`` — tool invocation start (data: {name: str})
    - ``handoff``   — agent handoff (data: {from: str, to: str})
    - ``done``      — stream complete (data: {active_agent: str, tool_calls: [str]})
    - ``error``     — unhandled error (data: {detail: str})
    """
    log = logger.bind(
        thread_id=req.thread_id,
        correlation_id=req.correlation_id,
        candidate_id=req.candidate_id,
        application_id=req.application_id,
    )
    log.info("v2_stream_start")

    config = {"configurable": {"thread_id": req.thread_id}, "callbacks": [langfuse_handler]}
    input_state = _build_v2_input(
        req.message, req.candidate_id, req.application_id, req.correlation_id
    )

    async def event_generator() -> AsyncGenerator[str, None]:
        tool_calls_seen: list[str] = []
        active_agent = "v2_primary_assistant"

        try:
            async for event in graph.astream_events(input_state, config=config, version="v2"):
                event_name = event.get("event", "")
                event_data = event.get("data", {})
                node_name = event.get("name", "")

                if event_name == "on_chat_model_stream":
                    chunk = event_data.get("chunk")
                    if chunk and chunk.content:
                        content = (
                            chunk.content if isinstance(chunk.content, str)
                            else "".join(
                                b.get("text", "")
                                for b in chunk.content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        )
                        if content:
                            yield f"data: {json.dumps({'event': 'token', 'data': {'content': content}})}\n\n"

                elif event_name == "on_tool_start":
                    tool_name = node_name or event.get("run_id", "unknown")
                    tool_calls_seen.append(tool_name)
                    yield f"data: {json.dumps({'event': 'tool_call', 'data': {'name': tool_name}})}\n\n"

                elif event_name == "on_chain_start" and "post_apply_assistant" in node_name:
                    active_agent = "post_apply_assistant"
                    yield (
                        f"data: {json.dumps({'event': 'handoff', 'data': {'from': 'v2_primary_assistant', 'to': 'post_apply_assistant'}})}\n\n"
                    )

                elif event_name == "on_chain_end" and node_name in (
                    "v2_primary_assistant",
                    "post_apply_assistant",
                ):
                    active_agent = node_name

            yield (
                f"data: {json.dumps({'event': 'done', 'data': {'active_agent': active_agent, 'tool_calls': tool_calls_seen}})}\n\n"
            )
            log.info("v2_stream_complete", active_agent=active_agent, tool_calls=tool_calls_seen)

        except Exception as exc:
            log.error("v2_stream_error", error=str(exc), exc_info=True)
            yield f"data: {json.dumps({'event': 'error', 'data': {'detail': str(exc)}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
