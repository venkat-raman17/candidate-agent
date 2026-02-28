"""Agent endpoints: synchronous invoke and SSE streaming."""

import json
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from candidate_agent.api.dependencies import get_graph
from candidate_agent.api.schemas import InvokeRequest, InvokeResponse, StreamRequest

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["agent"])


def _build_input(message: str, candidate_id: str, correlation_id: str) -> dict:
    """Build the initial graph state for a new turn."""
    return {
        "messages": [HumanMessage(content=message)],
        "candidate_id": candidate_id,
        "correlation_id": correlation_id,
        "active_agent": "candidate_primary",
    }


def _extract_result(final_state: dict, thread_id: str, correlation_id: str) -> InvokeResponse:
    """Pull the last AIMessage and tool-call names from the final graph state."""
    messages = final_state.get("messages", [])

    # Last AI message is the final answer
    response_text = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            response_text = (
                msg.content if isinstance(msg.content, str)
                else " ".join(
                    block.get("text", "") for block in msg.content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            )
            break

    # Collect all tool names that were called
    tool_calls: list[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            tool_calls.extend(tc["name"] for tc in msg.tool_calls)

    return InvokeResponse(
        thread_id=thread_id,
        correlation_id=correlation_id,
        response=response_text,
        agent_used=final_state.get("active_agent", "candidate_primary"),
        tool_calls=tool_calls,
    )


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest, graph=Depends(get_graph)) -> InvokeResponse:
    """Run the multi-agent graph synchronously and return the final response.

    Blocks until the agent produces a final answer. Use `/stream` for token-level streaming.
    """
    log = logger.bind(
        thread_id=req.thread_id,
        correlation_id=req.correlation_id,
        candidate_id=req.candidate_id,
    )
    log.info("invoke_start")

    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        final_state = await graph.ainvoke(
            _build_input(req.message, req.candidate_id, req.correlation_id),
            config=config,
        )
    except Exception as exc:
        log.error("invoke_error", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    result = _extract_result(final_state, req.thread_id, req.correlation_id)
    log.info("invoke_complete", agent_used=result.agent_used, tool_calls=result.tool_calls)
    return result


@router.post("/stream")
async def stream(req: StreamRequest, graph=Depends(get_graph)) -> StreamingResponse:
    """Stream agent events as Server-Sent Events (SSE).

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
    )
    log.info("stream_start")

    config = {"configurable": {"thread_id": req.thread_id}}
    input_state = _build_input(req.message, req.candidate_id, req.correlation_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        tool_calls_seen: list[str] = []
        active_agent = "candidate_primary"

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

                elif event_name == "on_chain_start" and "job_application_agent" in node_name:
                    active_agent = "job_application_agent"
                    yield (
                        f"data: {json.dumps({'event': 'handoff', 'data': {'from': 'candidate_primary', 'to': 'job_application_agent'}})}\n\n"
                    )

                elif event_name == "on_chain_end" and node_name in (
                    "candidate_primary",
                    "job_application_agent",
                ):
                    # Track which agent last produced output
                    active_agent = node_name

            yield (
                f"data: {json.dumps({'event': 'done', 'data': {'active_agent': active_agent, 'tool_calls': tool_calls_seen}})}\n\n"
            )
            log.info("stream_complete", active_agent=active_agent, tool_calls=tool_calls_seen)

        except Exception as exc:
            log.error("stream_error", error=str(exc), exc_info=True)
            yield f"data: {json.dumps({'event': 'error', 'data': {'detail': str(exc)}})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )
