# Production Observability Enhancement Guide

**Version**: 1.0
**Date**: 2026-03-01
**Status**: Production Recommendations
**Stack**: Langfuse + Prometheus + OpenObserve

---

## 🎯 Executive Summary

This guide provides comprehensive recommendations for production observability across the **candidate-agent** (Python) and **candidate-mcp** (Java) services using:

- **Langfuse**: LLM tracing, cost tracking, prompt management, user feedback
- **Prometheus**: Service metrics, SLOs, alerting
- **OpenObserve**: Application logs, structured logging, dashboards, alerting

---

## 1. Langfuse: LLM Observability & Tracing

### 1.1 Current Implementation Status

**✅ Already Implemented**:
- LangfuseCallbackHandler integrated with v2 API routes
- Traces visible in Langfuse UI
- Basic automatic instrumentation via LangChain callbacks

**File**: `candidate-agent/src/candidate_agent/api/routes/agent_v2.py`
```python
from langfuse.langchain import CallbackHandler

langfuse_handler = CallbackHandler()

config = {"configurable": {"thread_id": req.thread_id}, "callbacks": [langfuse_handler]}
final_state = await graph.ainvoke(input_state, config=config)
```

### 1.2 Enhanced Langfuse Implementation

#### A. **Advanced Trace Configuration**

**File**: `candidate-agent/src/candidate_agent/observability/langfuse_config.py` (NEW)

```python
"""Langfuse tracing configuration with advanced features."""

from functools import wraps
from typing import Any, Callable
import structlog
from langfuse import Langfuse
from langfuse.decorators import langfuse_context, observe
from langfuse.langchain import CallbackHandler

from candidate_agent.config import settings

logger = structlog.get_logger(__name__)


class EnhancedLangfuseHandler:
    """Enhanced Langfuse callback handler with session tracking, metadata, and user feedback."""

    def __init__(
        self,
        candidate_id: str | None = None,
        application_id: str | None = None,
        correlation_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ):
        self.candidate_id = candidate_id
        self.application_id = application_id
        self.correlation_id = correlation_id
        self.session_id = session_id or correlation_id  # Fallback to correlation_id
        self.tags = tags or []

        # Build metadata
        metadata = {
            "agent_version": "v2.0",
            "environment": settings.environment,  # Add to Settings
        }
        if candidate_id:
            metadata["candidate_id"] = candidate_id
        if application_id:
            metadata["application_id"] = application_id

        # Create handler with rich context
        self.handler = CallbackHandler(
            session_id=self.session_id,
            user_id=candidate_id,  # Track per-candidate metrics
            tags=self._build_tags(),
            metadata=metadata,
        )

    def _build_tags(self) -> list[str]:
        """Build tags for trace organization."""
        tags = ["production", "post_apply_assistant"] + self.tags
        if self.application_id:
            tags.append("application_specific")
        else:
            tags.append("general_query")
        return tags

    def get_handler(self) -> CallbackHandler:
        """Get the configured callback handler."""
        return self.handler

    def record_user_feedback(self, trace_id: str, score: float, comment: str | None = None):
        """Record user feedback for a trace.

        Args:
            trace_id: Langfuse trace ID
            score: Feedback score (0.0 - 1.0, or -1.0 for thumbs down, 1.0 for thumbs up)
            comment: Optional feedback comment
        """
        client = Langfuse()
        client.score(
            trace_id=trace_id,
            name="user_feedback",
            value=score,
            comment=comment,
        )
        logger.info(
            "langfuse_feedback_recorded",
            trace_id=trace_id,
            score=score,
            has_comment=bool(comment),
        )


def trace_agent_operation(operation_name: str):
    """Decorator to trace custom agent operations.

    Usage:
        @trace_agent_operation("pre_flight_checks")
        def validate_candidate_access(candidate_id: str):
            # Your logic
            pass
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        @observe(name=operation_name)
        def wrapper(*args, **kwargs):
            try:
                result = func(*args, **kwargs)
                # Add success metadata
                langfuse_context.update_current_observation(
                    metadata={"status": "success", "operation": operation_name}
                )
                return result
            except Exception as e:
                # Add error metadata
                langfuse_context.update_current_observation(
                    metadata={
                        "status": "error",
                        "operation": operation_name,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
                )
                raise

        return wrapper

    return decorator
```

**Update File**: `candidate-agent/src/candidate_agent/api/routes/agent_v2.py`

```python
from candidate_agent.observability.langfuse_config import EnhancedLangfuseHandler

@router.post("/invoke", response_model=InvokeResponse)
async def v2_invoke(req: V2InvokeRequest, graph=Depends(get_v2_graph)) -> InvokeResponse:
    log = logger.bind(
        thread_id=req.thread_id,
        correlation_id=req.correlation_id,
        candidate_id=req.candidate_id,
        application_id=req.application_id,
    )
    log.info("v2_invoke_start")

    # Enhanced Langfuse handler with rich context
    langfuse = EnhancedLangfuseHandler(
        candidate_id=req.candidate_id,
        application_id=req.application_id,
        correlation_id=req.correlation_id,
        session_id=req.thread_id,  # Multi-turn conversation tracking
        tags=["invoke", "v2"],
    )

    config = {
        "configurable": {"thread_id": req.thread_id},
        "callbacks": [langfuse.get_handler()],
    }

    try:
        final_state = await graph.ainvoke(
            _build_v2_input(req.message, req.candidate_id, req.application_id, req.correlation_id),
            config=config,
        )
    except Exception as exc:
        log.error("v2_invoke_error", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail=f"Agent error: {exc}") from exc

    result = _extract_result(final_state, req.thread_id, req.correlation_id)
    log.info("v2_invoke_complete", agent_used=result.agent_used, tool_calls=result.tool_calls)
    return result


# NEW: Add user feedback endpoint
@router.post("/feedback")
async def submit_feedback(
    trace_id: str,
    score: float,  # -1.0 (thumbs down), 0.0 (neutral), 1.0 (thumbs up)
    comment: str | None = None,
):
    """Allow users to provide feedback on agent responses."""
    langfuse = EnhancedLangfuseHandler()
    langfuse.record_user_feedback(trace_id, score, comment)
    return {"status": "success", "trace_id": trace_id}
```

#### B. **Cost Tracking & Token Analysis**

Langfuse automatically tracks costs for OpenAI/Anthropic models. Add custom cost tracking for local LLM:

```python
# In observability/langfuse_config.py

def calculate_custom_model_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate cost for custom/local LLM models.

    Args:
        model: Model identifier
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens

    Returns:
        Cost in USD
    """
    # Pricing per 1M tokens (adjust for your infrastructure)
    CUSTOM_MODEL_PRICING = {
        "openai/gpt-oss-20b": {"prompt": 0.50, "completion": 1.50},  # Per 1M tokens
        # Add more custom models
    }

    if model not in CUSTOM_MODEL_PRICING:
        return 0.0  # Unknown model

    pricing = CUSTOM_MODEL_PRICING[model]
    prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["completion"]
    return prompt_cost + completion_cost
```

#### C. **Prompt Management Integration**

Create reusable prompts in Langfuse UI and fetch them:

```python
from langfuse import Langfuse

client = Langfuse()

# Fetch prompt from Langfuse (versioned)
prompt = client.get_prompt("post_apply_assistant_system_prompt", version=3)

# Use in your agent
system_message = prompt.compile(
    workflow_json=registry.workflow_states_json,
    assessment_types_json=registry.assessment_types_json,
    # ... other variables
)
```

**Benefits**:
- Version control for prompts (track performance by version)
- A/B test prompt variations
- Rollback to previous versions
- Centralized prompt management

#### D. **Dataset Creation & Evaluation**

```python
# observability/langfuse_evaluation.py (NEW)

from langfuse import Langfuse

client = Langfuse()

# Create dataset from production traces
dataset = client.create_dataset(
    name="post_apply_production_scenarios",
    description="Real user queries for regression testing",
)

# Add items from successful production traces
dataset.create_item(
    input={
        "message": "What's my application status?",
        "candidate_id": "C001",
        "application_id": "A001",
    },
    expected_output={
        "contains": ["You're currently in the technical interview stage"],
    },
)

# Run evaluation
def evaluate_response(output, expected):
    """Custom evaluation function."""
    if expected["contains"]:
        for phrase in expected["contains"]:
            if phrase.lower() in output["response"].lower():
                return {"score": 1.0, "reason": f"Contains expected phrase: {phrase}"}
        return {"score": 0.0, "reason": "Missing expected phrase"}
    return {"score": 1.0, "reason": "No explicit expectations"}
```

### 1.3 Langfuse Dashboards & Alerts

**Key Metrics to Monitor**:
1. **Latency P50/P95/P99** by agent type
2. **Cost per trace** (identify expensive queries)
3. **Tool call patterns** (which tools are used most)
4. **Error rate** by agent/tool
5. **User feedback scores** (thumbs up/down)
6. **Session duration** (multi-turn conversations)
7. **Token usage trends** over time

**Recommended Alerts**:
- P95 latency > 10s for 5 minutes
- Cost per trace > $0.50 (expensive queries)
- Error rate > 5% for 10 minutes
- User feedback score < 0.6 for 100 traces

---

## 2. Prometheus: Service Metrics & SLOs

### 2.1 Python Agent Metrics (candidate-agent)

#### A. **Metrics Configuration**

**File**: `candidate-agent/src/candidate_agent/observability/metrics.py` (NEW)

```python
"""Prometheus metrics for candidate-agent."""

from prometheus_client import Counter, Histogram, Gauge, Info
import time
from functools import wraps
from typing import Callable

# Agent invocation metrics
agent_requests_total = Counter(
    "agent_requests_total",
    "Total number of agent requests",
    ["agent_version", "agent_used", "status"],
)

agent_request_duration_seconds = Histogram(
    "agent_request_duration_seconds",
    "Agent request duration in seconds",
    ["agent_version", "agent_used"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

# Tool invocation metrics
mcp_tool_calls_total = Counter(
    "mcp_tool_calls_total",
    "Total number of MCP tool calls",
    ["tool_name", "status"],
)

mcp_tool_duration_seconds = Histogram(
    "mcp_tool_duration_seconds",
    "MCP tool call duration in seconds",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0],
)

# Handoff metrics
agent_handoff_total = Counter(
    "agent_handoff_total",
    "Total number of agent handoffs",
    ["from_agent", "to_agent"],
)

# MCP connection health
mcp_connection_status = Gauge(
    "mcp_connection_status",
    "MCP server connection status (1=connected, 0=disconnected)",
)

mcp_tools_loaded = Gauge(
    "mcp_tools_loaded",
    "Number of MCP tools loaded",
    ["agent_type"],
)

# LLM token usage
llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total LLM tokens used",
    ["token_type", "model"],  # token_type: prompt|completion
)

llm_cost_total = Counter(
    "llm_cost_usd_total",
    "Total LLM cost in USD",
    ["model"],
)

# Application info
app_info = Info(
    "candidate_agent_app",
    "Application metadata",
)


def track_request_metrics(agent_version: str = "v2"):
    """Decorator to track agent request metrics.

    Usage:
        @track_request_metrics(agent_version="v2")
        async def v2_invoke(req):
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            agent_used = "unknown"

            try:
                result = await func(*args, **kwargs)
                if hasattr(result, "agent_used"):
                    agent_used = result.agent_used
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start_time
                agent_requests_total.labels(
                    agent_version=agent_version, agent_used=agent_used, status=status
                ).inc()
                agent_request_duration_seconds.labels(
                    agent_version=agent_version, agent_used=agent_used
                ).observe(duration)

        return wrapper

    return decorator


def track_tool_call(tool_name: str):
    """Context manager to track MCP tool call metrics.

    Usage:
        with track_tool_call("getCandidateProfile"):
            result = await tool.ainvoke(...)
    """

    class ToolCallTracker:
        def __enter__(self):
            self.start_time = time.time()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = time.time() - self.start_time
            status = "success" if exc_type is None else "error"

            mcp_tool_calls_total.labels(tool_name=tool_name, status=status).inc()
            mcp_tool_duration_seconds.labels(tool_name=tool_name).observe(duration)

    return ToolCallTracker()


def update_mcp_connection_status(connected: bool):
    """Update MCP connection status metric."""
    mcp_connection_status.set(1 if connected else 0)


def update_mcp_tools_loaded(agent_type: str, count: int):
    """Update number of MCP tools loaded."""
    mcp_tools_loaded.labels(agent_type=agent_type).set(count)


def track_llm_usage(model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float):
    """Track LLM token usage and cost."""
    llm_tokens_total.labels(token_type="prompt", model=model).inc(prompt_tokens)
    llm_tokens_total.labels(token_type="completion", model=model).inc(completion_tokens)
    llm_cost_total.labels(model=model).inc(cost_usd)
```

#### B. **Expose Metrics Endpoint**

**File**: `candidate-agent/src/candidate_agent/main.py`

```python
from prometheus_client import make_asgi_app
from candidate_agent.observability.metrics import app_info, update_mcp_connection_status, update_mcp_tools_loaded

# Set application info
app_info.info({
    "version": "2.0.0",
    "python_version": "3.12",
    "environment": settings.environment,
})

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing startup
    registry = await init_registry(settings)

    # Update MCP metrics
    update_mcp_connection_status(True)
    update_mcp_tools_loaded("post_apply_assistant", len(registry.post_apply_tools))

    # ... rest of startup
    yield

    # Shutdown
    update_mcp_connection_status(False)
```

#### C. **Instrument Agent Routes**

**File**: `candidate-agent/src/candidate_agent/api/routes/agent_v2.py`

```python
from candidate_agent.observability.metrics import (
    track_request_metrics,
    agent_handoff_total,
)

@router.post("/invoke", response_model=InvokeResponse)
@track_request_metrics(agent_version="v2")
async def v2_invoke(req: V2InvokeRequest, graph=Depends(get_v2_graph)) -> InvokeResponse:
    # ... existing code
    pass


@router.post("/stream")
async def v2_stream(req: V2StreamRequest, graph=Depends(get_v2_graph)) -> StreamingResponse:
    async def event_generator() -> AsyncGenerator[str, None]:
        # ... existing code

        # Track handoff
        if event_name == "on_chain_start" and "post_apply_assistant" in node_name:
            agent_handoff_total.labels(
                from_agent="v2_primary_assistant",
                to_agent="post_apply_assistant",
            ).inc()

        # ... rest of generator
```

### 2.2 Java MCP Server Metrics (candidate-mcp)

#### A. **Micrometer Metrics Configuration**

**File**: `candidate-mcp/src/main/java/com/example/mcpserver/observability/McpMetricsConfiguration.java` (NEW)

```java
package com.example.mcpserver.observability;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class McpMetricsConfiguration {

    @Bean
    public McpMetrics mcpMetrics(MeterRegistry registry) {
        return new McpMetrics(registry);
    }
}

/**
 * MCP server metrics registry.
 */
public class McpMetrics {
    private final MeterRegistry registry;

    // Tool invocation metrics
    private final Counter toolCallsTotal;
    private final Timer toolCallDuration;

    // Transformer metrics
    private final Counter transformationsTotal;
    private final Timer transformationDuration;

    // Downstream service metrics
    private final Counter downstreamCallsTotal;
    private final Timer downstreamCallDuration;

    // Circuit breaker metrics
    private final Counter circuitBreakerOpenTotal;

    public McpMetrics(MeterRegistry registry) {
        this.registry = registry;

        this.toolCallsTotal = Counter.builder("mcp.tool.calls.total")
            .description("Total number of tool calls")
            .tags("tool", "status")
            .register(registry);

        this.toolCallDuration = Timer.builder("mcp.tool.duration.seconds")
            .description("Tool call duration")
            .tags("tool")
            .register(registry);

        this.transformationsTotal = Counter.builder("mcp.transformations.total")
            .description("Total number of transformations")
            .tags("transformer", "status")
            .register(registry);

        this.transformationDuration = Timer.builder("mcp.transformation.duration.seconds")
            .description("Transformation duration")
            .tags("transformer")
            .register(registry);

        this.downstreamCallsTotal = Counter.builder("mcp.downstream.calls.total")
            .description("Total downstream service calls")
            .tags("service", "endpoint", "status")
            .register(registry);

        this.downstreamCallDuration = Timer.builder("mcp.downstream.duration.seconds")
            .description("Downstream call duration")
            .tags("service", "endpoint")
            .register(registry);

        this.circuitBreakerOpenTotal = Counter.builder("mcp.circuit_breaker.open.total")
            .description("Total circuit breaker opens")
            .tags("service")
            .register(registry);
    }

    public void recordToolCall(String toolName, String status, long durationMs) {
        toolCallsTotal.increment();
        toolCallDuration.record(durationMs, TimeUnit.MILLISECONDS);
    }

    public void recordTransformation(String transformerName, String status, long durationMs) {
        transformationsTotal.increment();
        transformationDuration.record(durationMs, TimeUnit.MILLISECONDS);
    }

    public void recordDownstreamCall(String service, String endpoint, String status, long durationMs) {
        downstreamCallsTotal.increment();
        downstreamCallDuration.record(durationMs, TimeUnit.MILLISECONDS);
    }

    public void recordCircuitBreakerOpen(String service) {
        circuitBreakerOpenTotal.increment();
    }
}
```

#### B. **Expose Prometheus Endpoint**

**File**: `candidate-mcp/src/main/resources/application.yml`

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  metrics:
    export:
      prometheus:
        enabled: true
    tags:
      application: candidate-mcp
      environment: ${ENVIRONMENT:dev}
```

Access metrics at: `http://localhost:8081/actuator/prometheus`

### 2.3 Prometheus Alert Rules

**File**: `prometheus/alert_rules.yml`

```yaml
groups:
  - name: candidate_agent_alerts
    interval: 30s
    rules:
      # High error rate
      - alert: HighAgentErrorRate
        expr: |
          rate(agent_requests_total{status="error"}[5m]) /
          rate(agent_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
          service: candidate-agent
        annotations:
          summary: "High error rate in candidate-agent"
          description: "Error rate is {{ $value | humanizePercentage }} for {{ $labels.agent_used }}"

      # Slow agent responses
      - alert: SlowAgentResponses
        expr: |
          histogram_quantile(0.95,
            rate(agent_request_duration_seconds_bucket[5m])
          ) > 10
        for: 5m
        labels:
          severity: warning
          service: candidate-agent
        annotations:
          summary: "Slow agent responses (P95 > 10s)"
          description: "P95 latency is {{ $value }}s for {{ $labels.agent_used }}"

      # MCP connection down
      - alert: McpConnectionDown
        expr: mcp_connection_status == 0
        for: 1m
        labels:
          severity: critical
          service: candidate-agent
        annotations:
          summary: "MCP server connection lost"
          description: "candidate-agent cannot connect to candidate-mcp"

      # High LLM cost
      - alert: HighLlmCost
        expr: |
          increase(llm_cost_usd_total[1h]) > 100
        labels:
          severity: warning
          service: candidate-agent
        annotations:
          summary: "High LLM cost in last hour"
          description: "LLM cost is ${{ $value }} in the last hour"

      # Tool call failures
      - alert: HighToolCallFailureRate
        expr: |
          rate(mcp_tool_calls_total{status="error"}[5m]) /
          rate(mcp_tool_calls_total[5m]) > 0.10
        for: 5m
        labels:
          severity: warning
          service: candidate-agent
        annotations:
          summary: "High MCP tool call failure rate"
          description: "Tool {{ $labels.tool_name }} failure rate is {{ $value | humanizePercentage }}"

  - name: candidate_mcp_alerts
    interval: 30s
    rules:
      # Circuit breaker open
      - alert: CircuitBreakerOpen
        expr: resilience4j_circuitbreaker_state{state="open"} == 1
        for: 2m
        labels:
          severity: critical
          service: candidate-mcp
        annotations:
          summary: "Circuit breaker open for {{ $labels.name }}"
          description: "Circuit breaker {{ $labels.name }} has been open for 2+ minutes"

      # High downstream latency
      - alert: HighDownstreamLatency
        expr: |
          histogram_quantile(0.95,
            rate(mcp_downstream_duration_seconds_bucket[5m])
          ) > 5
        for: 5m
        labels:
          severity: warning
          service: candidate-mcp
        annotations:
          summary: "High downstream service latency"
          description: "P95 latency to {{ $labels.service }}/{{ $labels.endpoint }} is {{ $value }}s"

      # Transformation failures
      - alert: HighTransformationFailureRate
        expr: |
          rate(mcp_transformations_total{status="error"}[5m]) /
          rate(mcp_transformations_total[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
          service: candidate-mcp
        annotations:
          summary: "High transformation failure rate"
          description: "{{ $labels.transformer }} failure rate is {{ $value | humanizePercentage }}"
```

---

## 3. OpenObserve: Application Logs & Dashboards

### 3.1 Strategic Logging Points

#### A. **Python Agent (candidate-agent)**

**Critical Log Events**:

| Event | Level | Fields | Purpose | Alert Trigger |
|-------|-------|--------|---------|---------------|
| `agent_invoke_start` | INFO | `thread_id`, `correlation_id`, `candidate_id`, `application_id`, `message` | Request received | - |
| `handoff_to_post_apply_assistant` | INFO | `reason`, `candidate_id`, `application_id` | Agent handoff occurred | - |
| `mcp_tool_call_start` | DEBUG | `tool_name`, `args`, `correlation_id` | Tool invocation started | - |
| `mcp_tool_call_complete` | INFO | `tool_name`, `duration_ms`, `status` | Tool completed | Alert if `duration_ms > 5000` |
| `mcp_tool_call_error` | ERROR | `tool_name`, `error`, `correlation_id` | Tool failed | Alert immediately |
| `agent_invoke_complete` | INFO | `thread_id`, `correlation_id`, `agent_used`, `tool_calls`, `duration_ms` | Request completed | Alert if `duration_ms > 30000` |
| `agent_invoke_error` | ERROR | `error`, `error_type`, `correlation_id`, `stack_trace` | Request failed | Alert immediately |
| `mcp_connection_failed` | CRITICAL | `error`, `mcp_url`, `retry_attempt` | MCP unreachable | Alert immediately |
| `llm_call_start` | DEBUG | `model`, `prompt_tokens`, `correlation_id` | LLM invocation | - |
| `llm_call_complete` | INFO | `model`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `duration_ms` | LLM completed | Alert if `cost_usd > 1.0` |
| `user_feedback_received` | INFO | `trace_id`, `score`, `has_comment` | User feedback | - |
| `circuit_breaker_opened` | CRITICAL | `service`, `failure_rate` | Circuit opened | Alert immediately |
| `cache_hit` | DEBUG | `cache_key`, `cache_type` | Cache hit | - |
| `cache_miss` | DEBUG | `cache_key`, `cache_type` | Cache miss | - |
| `session_started` | INFO | `session_id`, `candidate_id` | New session | - |
| `session_ended` | INFO | `session_id`, `duration_minutes`, `turn_count` | Session complete | - |

**Enhanced Logging Implementation**:

**File**: `candidate-agent/src/candidate_agent/observability/logging.py` (NEW)

```python
"""Structured logging for OpenObserve integration."""

import structlog
import time
from contextlib import contextmanager
from typing import Any

logger = structlog.get_logger(__name__)


class AgentLogger:
    """Enhanced logger for agent operations with automatic context."""

    def __init__(self, correlation_id: str, candidate_id: str | None = None):
        self.correlation_id = correlation_id
        self.candidate_id = candidate_id
        self.log = logger.bind(
            correlation_id=correlation_id,
            candidate_id=candidate_id,
        )

    def agent_invoke_start(self, thread_id: str, message: str, application_id: str | None = None):
        """Log agent invocation start."""
        self.log.info(
            "agent_invoke_start",
            thread_id=thread_id,
            message_length=len(message),
            application_id=application_id,
        )

    def agent_invoke_complete(
        self,
        thread_id: str,
        agent_used: str,
        tool_calls: list[str],
        duration_ms: float,
    ):
        """Log agent invocation complete."""
        self.log.info(
            "agent_invoke_complete",
            thread_id=thread_id,
            agent_used=agent_used,
            tool_calls=tool_calls,
            tool_count=len(tool_calls),
            duration_ms=duration_ms,
        )

    def agent_invoke_error(self, error: Exception, error_context: dict[str, Any] | None = None):
        """Log agent invocation error."""
        self.log.error(
            "agent_invoke_error",
            error=str(error),
            error_type=type(error).__name__,
            error_context=error_context or {},
            exc_info=True,
        )

    @contextmanager
    def tool_call_span(self, tool_name: str, args: dict[str, Any]):
        """Context manager for tool call logging.

        Usage:
            with agent_logger.tool_call_span("getCandidateProfile", {"candidateId": "C001"}):
                result = await tool.ainvoke(args)
        """
        start = time.time()
        self.log.debug("mcp_tool_call_start", tool_name=tool_name, args=args)

        try:
            yield
            duration_ms = (time.time() - start) * 1000
            self.log.info(
                "mcp_tool_call_complete",
                tool_name=tool_name,
                duration_ms=duration_ms,
                status="success",
            )
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            self.log.error(
                "mcp_tool_call_error",
                tool_name=tool_name,
                duration_ms=duration_ms,
                error=str(e),
                error_type=type(e).__name__,
                status="error",
            )
            raise

    def handoff(self, from_agent: str, to_agent: str, reason: str):
        """Log agent handoff."""
        self.log.info(
            "handoff_to_post_apply_assistant" if to_agent == "post_apply_assistant" else "agent_handoff",
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
        )

    def llm_call(self, model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float, duration_ms: float):
        """Log LLM call."""
        self.log.info(
            "llm_call_complete",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
        )
```

#### B. **Java MCP Server (candidate-mcp)**

**Critical Log Events**:

| Event | Level | Fields | Purpose | Alert Trigger |
|-------|-------|--------|---------|---------------|
| `tool_called` | INFO | `tool`, `candidate_id`, `trace_id`, `args_hash` | Tool invoked | - |
| `tool_completed` | INFO | `tool`, `duration_ms`, `result_size_bytes` | Tool completed | Alert if `duration_ms > 5000` |
| `tool_error` | ERROR | `tool`, `error`, `error_type`, `trace_id` | Tool failed | Alert immediately |
| `transformation_start` | DEBUG | `transformer`, `source_type` | Transformation started | - |
| `transformation_complete` | INFO | `transformer`, `duration_ms`, `fields_stripped` | Transformation done | - |
| `pii_violation_detected` | CRITICAL | `transformer`, `field`, `value_hash` | PII leak detected | Alert immediately + page on-call |
| `downstream_call_start` | DEBUG | `service`, `endpoint`, `method` | Downstream call | - |
| `downstream_call_complete` | INFO | `service`, `endpoint`, `status_code`, `duration_ms` | Downstream done | Alert if `status_code >= 500` |
| `downstream_call_error` | ERROR | `service`, `endpoint`, `error`, `retry_attempt` | Downstream failed | Alert if 3+ failures in 5 min |
| `circuit_breaker_opened` | CRITICAL | `service`, `failure_rate`, `call_count` | Circuit opened | Alert immediately |
| `circuit_breaker_closed` | INFO | `service`, `duration_open_seconds` | Circuit closed | - |
| `sla_breach_detected` | WARN | `application_id`, `stage`, `days_in_stage`, `threshold` | SLA breached | Alert if count > 10 in 1 hour |
| `cache_hit` | DEBUG | `cache_key`, `cache_type`, `ttl_remaining` | Cache hit | - |
| `cache_miss` | DEBUG | `cache_key`, `cache_type` | Cache miss | - |
| `mcp_request_received` | INFO | `x_correlation_id`, `x_candidate_id`, `method` | MCP request | - |
| `mcp_response_sent` | INFO | `x_correlation_id`, `status`, `duration_ms` | MCP response | Alert if `duration_ms > 10000` |

**Logging Implementation**:

**File**: `candidate-mcp/src/main/java/com/example/mcpserver/observability/McpLogger.java` (NEW)

```java
package com.example.mcpserver.observability;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

public class McpLogger {
    private static final Logger log = LoggerFactory.getLogger(McpLogger.class);

    public static void toolCalled(String tool, String candidateId, String traceId, Map<String, Object> args) {
        MDC.put("tool", tool);
        MDC.put("candidate_id", candidateId);
        MDC.put("trace_id", traceId);
        log.info("tool_called args_hash={}", hashArgs(args));
        MDC.clear();
    }

    public static void toolCompleted(String tool, long durationMs, int resultSizeBytes) {
        MDC.put("tool", tool);
        log.info("tool_completed duration_ms={} result_size_bytes={}", durationMs, resultSizeBytes);
        MDC.clear();
    }

    public static void toolError(String tool, Exception error, String traceId) {
        MDC.put("tool", tool);
        MDC.put("trace_id", traceId);
        MDC.put("error_type", error.getClass().getSimpleName());
        log.error("tool_error error={}", error.getMessage(), error);
        MDC.clear();
    }

    public static void piiViolationDetected(String transformer, String field, String valueHash) {
        MDC.put("transformer", transformer);
        MDC.put("field", field);
        MDC.put("value_hash", valueHash);
        log.error("pii_violation_detected - CRITICAL: PII data detected in transformer output");
        MDC.clear();
    }

    public static void circuitBreakerOpened(String service, double failureRate, long callCount) {
        MDC.put("service", service);
        log.error("circuit_breaker_opened failure_rate={} call_count={}", failureRate, callCount);
        MDC.clear();
    }

    public static void slaBreachDetected(String applicationId, String stage, long daysInStage, int threshold) {
        MDC.put("application_id", applicationId);
        MDC.put("stage", stage);
        log.warn("sla_breach_detected days_in_stage={} threshold={}", daysInStage, threshold);
        MDC.clear();
    }

    private static String hashArgs(Map<String, Object> args) {
        // Generate hash of args for deduplication
        return String.valueOf(args.hashCode());
    }
}
```

### 3.2 OpenObserve Dashboards

#### Dashboard 1: **Agent Performance Overview**

**Panels**:
1. **Request Rate** (time series)
   - Query: `count by (agent_used) (agent_invoke_start)`
   - Shows: Requests per minute by agent type

2. **P50/P95/P99 Latency** (time series)
   - Query: `percentile(agent_invoke_complete.duration_ms, [50, 95, 99])`
   - Shows: Latency distribution over time

3. **Error Rate** (gauge)
   - Query: `count(agent_invoke_error) / count(agent_invoke_start) * 100`
   - Shows: Percentage of failed requests

4. **Top Tools Used** (bar chart)
   - Query: `count by (tool_name) (mcp_tool_call_complete) | sort desc | limit 10`
   - Shows: Most frequently called tools

5. **LLM Cost** (time series)
   - Query: `sum(llm_call_complete.cost_usd)`
   - Shows: Cumulative LLM cost over time

6. **Tool Call Heatmap** (heatmap)
   - Query: `count by (tool_name, hour) (mcp_tool_call_complete)`
   - Shows: Tool usage patterns by hour of day

#### Dashboard 2: **MCP Server Health**

**Panels**:
1. **Tool Success Rate** (gauge grid)
   - Query: `count(tool_completed{status="success"}) / count(tool_called) * 100 by tool`
   - Shows: Success rate per tool

2. **Downstream Service Latency** (time series)
   - Query: `avg(downstream_call_complete.duration_ms) by service`
   - Shows: Average latency to each downstream service

3. **Circuit Breaker Status** (stat grid)
   - Query: `latest(circuit_breaker_opened.service)`
   - Shows: Open/closed status per service

4. **Transformation Performance** (table)
   - Query: `avg(transformation_complete.duration_ms), count(*) by transformer`
   - Shows: Average duration and call count per transformer

5. **PII Violations** (counter)
   - Query: `count(pii_violation_detected)`
   - Shows: Total PII violations (should be 0)

#### Dashboard 3: **User Experience & SLOs**

**Panels**:
1. **SLO Compliance** (gauge)
   - Target: 95% of requests < 10s
   - Query: `count(agent_invoke_complete{duration_ms < 10000}) / count(agent_invoke_complete) * 100`

2. **User Feedback Trends** (time series)
   - Query: `avg(user_feedback_received.score) by date`
   - Shows: Average feedback score over time

3. **SLA Breaches** (counter)
   - Query: `count(sla_breach_detected)`
   - Shows: Number of SLA breaches

4. **Session Duration** (histogram)
   - Query: `histogram(session_ended.duration_minutes)`
   - Shows: Distribution of session lengths

5. **Multi-Turn Conversations** (stat)
   - Query: `count(session_ended{turn_count > 1}) / count(session_ended) * 100`
   - Shows: Percentage of multi-turn conversations

### 3.3 OpenObserve Alert Rules

**File**: `openobserve/alert_rules.json`

```json
{
  "alerts": [
    {
      "name": "critical_agent_error_rate",
      "query": "count(agent_invoke_error) / count(agent_invoke_start) * 100 > 10",
      "duration": "5m",
      "severity": "critical",
      "notification": ["slack_oncall", "pagerduty"],
      "description": "Agent error rate exceeds 10% for 5 minutes"
    },
    {
      "name": "high_llm_cost_spike",
      "query": "sum(llm_call_complete.cost_usd) > 50",
      "duration": "1h",
      "severity": "warning",
      "notification": ["slack_eng"],
      "description": "LLM cost exceeds $50 in 1 hour"
    },
    {
      "name": "mcp_connection_down",
      "query": "count(mcp_connection_failed) > 0",
      "duration": "1m",
      "severity": "critical",
      "notification": ["slack_oncall", "pagerduty"],
      "description": "MCP server connection lost"
    },
    {
      "name": "pii_violation_detected",
      "query": "count(pii_violation_detected) > 0",
      "duration": "1m",
      "severity": "critical",
      "notification": ["slack_security", "pagerduty"],
      "description": "PII data detected in transformer output - IMMEDIATE ACTION REQUIRED"
    },
    {
      "name": "circuit_breaker_open",
      "query": "count(circuit_breaker_opened) > 0",
      "duration": "2m",
      "severity": "critical",
      "notification": ["slack_oncall"],
      "description": "Circuit breaker opened for downstream service"
    },
    {
      "name": "slow_agent_responses",
      "query": "percentile(agent_invoke_complete.duration_ms, 95) > 30000",
      "duration": "10m",
      "severity": "warning",
      "notification": ["slack_eng"],
      "description": "P95 agent response time exceeds 30 seconds"
    },
    {
      "name": "high_tool_failure_rate",
      "query": "count(mcp_tool_call_error) / count(mcp_tool_call_start) * 100 > 15",
      "duration": "5m",
      "severity": "warning",
      "notification": ["slack_eng"],
      "description": "MCP tool call failure rate exceeds 15%"
    },
    {
      "name": "excessive_sla_breaches",
      "query": "count(sla_breach_detected) > 50",
      "duration": "1h",
      "severity": "warning",
      "notification": ["slack_recruiting"],
      "description": "More than 50 SLA breaches in 1 hour"
    }
  ]
}
```

---

## 4. Implementation Priorities

### Phase 1: Foundation (Week 1)
- ✅ Basic Langfuse integration (already done)
- 🔲 Enhanced Langfuse with session tracking and metadata
- 🔲 Prometheus metrics endpoints (Python + Java)
- 🔲 Basic structured logging (correlation IDs, candidate IDs)

### Phase 2: Comprehensive Instrumentation (Week 2)
- 🔲 All strategic log events implemented
- 🔲 Prometheus alert rules configured
- 🔲 OpenObserve dashboards created
- 🔲 Tool call metrics tracking

### Phase 3: Advanced Features (Week 3)
- 🔲 Langfuse prompt management integration
- 🔲 Dataset creation from production traces
- 🔲 User feedback collection endpoint
- 🔲 Cost tracking and optimization

### Phase 4: Production Hardening (Week 4)
- 🔲 Alert rule tuning based on real traffic
- 🔲 Dashboard refinement
- 🔲 SLO definition and tracking
- 🔲 On-call runbook creation

---

## 5. Optimization Opportunities

### langchain-mcp-adapters Optimizations

**Currently Missing**:
1. **Resource Template Fetching**: Fetch dynamic resources for richer context
2. **Connection Pooling Tuning**: Optimize HTTP session reuse
3. **Tool Result Caching**: Cache identical tool calls in multi-turn conversations

**Recommended**:
```python
# Pre-fetch candidate context when IDs are known
if candidate_id and application_id:
    context_resources = await client.get_resources(
        "candidate_mcp",
        uris=[
            f"candidate://{candidate_id}/profile",
            f"candidate://{candidate_id}/applications",
            f"application://{application_id}/timeline",
        ]
    )
    # Inject into agent state or system prompt
```

### Spring AI MCP Optimizations

**Currently Missing**:
1. **Streaming Tool Responses**: Use `Flux<String>` for long-running tools
2. **Caching Layer**: Cache AgentContext DTOs (5-15 min TTL)
3. **App2App Signature Authentication**: Secure service-to-service calls
4. **Connection Pooling**: WebClient configuration with pool tuning

**Recommended**:
```java
// Add to application.yml
spring:
  ai:
    mcp:
      server:
        cache:
          enabled: true
          ttl: 300  # 5 minutes
          max-size: 1000
```

---

## Summary

This guide provides a comprehensive observability strategy for production deployment:

- **Langfuse**: LLM tracing, cost tracking, prompt management, user feedback
- **Prometheus**: Service metrics, SLOs, alerting
- **OpenObserve**: Application logs, structured logging, dashboards

**Key Benefits**:
1. End-to-end request tracing across Python agent and Java MCP server
2. Real-time alerting on critical issues (PII violations, circuit breakers, errors)
3. Cost optimization through LLM usage tracking
4. User feedback integration for continuous improvement
5. SLO tracking and compliance monitoring

**Next Steps**:
1. Implement enhanced Langfuse configuration
2. Add Prometheus metrics endpoints
3. Deploy strategic logging across both services
4. Create OpenObserve dashboards and alert rules
5. Tune alerts based on production traffic

---

**Document Created**: 2026-03-01
**Observability Stack**: Langfuse + Prometheus + OpenObserve
**Status**: Production-Ready Recommendations
**Implementation Priority**: Phase 1 (Foundation) - Start Immediately
