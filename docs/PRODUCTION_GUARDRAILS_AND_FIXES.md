# Production Guardrails & Critical Fixes
## post_apply_assistant Agent Issues & Solutions

**Date**: 2026-03-01
**Status**: 🔴 CRITICAL - Production Blockers Identified
**Priority**: P0 - Must Fix Before Production Deployment

---

## 🚨 Critical Issues Discovered in Testing

### Issue #1: Infinite Recursion Loop (600+ Observations)
**Test Prompt**: "Tell me about my profile and experience"
**Candidate ID**: C001
**Symptom**: Agent ran for 4+ minutes with 600+ observation levels before manual termination
**Impact**: Production timeout, resource exhaustion, poor UX

**Root Causes**:
1. ❌ **No recursion limit** set in LangGraph StateGraph
2. ❌ **No max iterations guardrail** on tool calls
3. ❌ **Agent prompt lacks convergence guidance** - doesn't know when to stop
4. ❌ **No request-level timeout** enforced at API layer
5. ❌ **Tool calling loop** - Agent calls tools repeatedly without making progress

### Issue #2: Hallucinated Job IDs
**Test Prompt**: "Show me all my applications and their current status"
**Candidate ID**: C001
**Error**: `{"detail":"Agent error: Job not found: JSeniorSRE"}`
**Impact**: Tool calls fail, agent cannot complete task

**Root Causes**:
1. ❌ **Tool schema too vague** - job_id parameter doesn't specify format/constraints
2. ❌ **No input validation** - Tools don't validate ID format (regex: `^J\d{3}$`)
3. ❌ **Agent "guesses" IDs** - Tries to infer job ID from job title "Senior SRE" → "JSeniorSRE"
4. ❌ **Missing examples in tool schema** - Doesn't show agent what valid IDs look like
5. ❌ **System prompt lacks strict ID usage rules** - Doesn't tell agent to ONLY use exact IDs from prior tool responses

---

## ✅ Comprehensive Fix Strategy

### 1. LangGraph Configuration Fixes (CRITICAL - P0)

#### 1.1 Add Recursion Limit
**File**: `careers-ai-service/src/agent/v2_graph.py`

```python
from langgraph.graph import StateGraph

# BEFORE (Missing recursion limit)
graph = StateGraph(AgentState)

# AFTER (With strict recursion limit)
graph = StateGraph(
    AgentState,
    recursion_limit=25  # Max 25 iterations before hard stop
)
```

**Rationale**:
- Prevents infinite loops
- 25 iterations = ~5-7 tool calls with reasonable reasoning steps
- Industry standard for production LLM agents

#### 1.2 Add Request-Level Timeout
**File**: `careers-ai-service/src/api/v2_routes.py`

```python
import asyncio
from fastapi import HTTPException

@router.post("/api/v2/agent/invoke")
async def invoke_agent(request: AgentRequest):
    try:
        # Add 60-second timeout for entire request
        result = await asyncio.wait_for(
            agent_executor.ainvoke(request),
            timeout=60.0  # 60 seconds max
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Agent execution timeout. Please try a simpler query or contact support."
        )
```

**Rationale**:
- Hard stop at 60 seconds prevents runaway requests
- Returns user-friendly error instead of hanging
- Protects backend resources

---

### 2. Tool Schema Improvements (CRITICAL - P0)

#### 2.1 Add ID Format Constraints & Examples
**File**: `candidate-mcp/src/main/java/com/example/mcpserver/tools/JobTools.java`

```java
// BEFORE (Vague schema)
@Tool(
    description = "Get detailed information about a specific job requisition"
)
public JobRequisition getJob(
    @ToolParam(description = "Job ID") String jobId
) { ... }

// AFTER (Strict schema with format and examples)
@Tool(
    description = """
    Get detailed information about a specific job requisition.

    IMPORTANT: job_id must be the EXACT job ID (format: J + 3 digits).
    DO NOT guess or infer job IDs from job titles.
    ONLY use job IDs returned from getApplicationsByCandidate or other tools.

    Examples of valid job IDs: J001, J002, J003
    Examples of INVALID job IDs: JSeniorSRE, job-001, senior-sre
    """
)
public JobRequisition getJob(
    @ToolParam(
        description = "Exact job ID in format J### (e.g., J001, J002). DO NOT infer from job title.",
        required = true
    )
    String jobId
) { ... }
```

#### 2.2 Add Input Validation in Tool Implementation
**File**: `candidate-mcp/src/main/java/com/example/mcpserver/tools/JobTools.java`

```java
import java.util.regex.Pattern;

private static final Pattern JOB_ID_PATTERN = Pattern.compile("^J\\d{3}$");
private static final Pattern APPLICATION_ID_PATTERN = Pattern.compile("^A\\d{3}$");
private static final Pattern CANDIDATE_ID_PATTERN = Pattern.compile("^C\\d{3}$");

@Tool(description = "...")
public JobRequisition getJob(@ToolParam(...) String jobId) {
    // Validate ID format BEFORE calling downstream service
    if (!JOB_ID_PATTERN.matcher(jobId).matches()) {
        throw new IllegalArgumentException(String.format(
            "Invalid job_id format: '%s'. Expected format: J### (e.g., J001, J002). " +
            "Do not guess job IDs. Use exact IDs from getApplicationsByCandidate() results.",
            jobId
        ));
    }

    return jobSyncClient.getJob(jobId)
        .orElseThrow(() -> new NotFoundException("Job not found: " + jobId));
}
```

**Rationale**:
- Fails fast with clear error message
- Teaches agent correct format through error feedback
- Prevents downstream service calls with invalid IDs

---

### 3. Agent System Prompt Improvements (CRITICAL - P0)

#### 3.1 Add Strict ID Usage Rules
**File**: `careers-ai-service/src/agent/prompts/post_apply_assistant.py`

```python
POST_APPLY_ASSISTANT_PROMPT = """
You are post_apply_assistant, helping candidates track their job applications.

## CRITICAL RULES - ID USAGE

1. **NEVER guess or infer IDs from names/titles**
   ❌ WRONG: User mentions "Senior SRE job" → You call getJob("JSeniorSRE")
   ✅ CORRECT: Call getApplicationsByCandidate() first → Extract job_id from response → Use exact ID

2. **ID Formats (USE EXACTLY AS RETURNED)**:
   - Job IDs: J001, J002, J003 (NOT "JSeniorSRE", "job-001", "senior-sre-job")
   - Application IDs: A001, A002, A003 (NOT "app-001", "application-1")
   - Candidate IDs: C001, C002, C003 (Always provided in context)

3. **Tool Calling Sequence**:
   - To show all applications: getApplicationsByCandidate(candidate_id) → Extract job IDs → DONE
   - To get job details: getApplicationsByCandidate() FIRST → Then getJob(exact_job_id)
   - To show interview schedule: getScheduledEvents(application_id) → Use exact app_id

4. **When to STOP calling tools**:
   - ✅ You have enough information to answer the user's question
   - ✅ Last tool call returned complete data
   - ✅ More tool calls won't add value to the answer
   - ❌ Don't call tools "just to check" or "for completeness"

5. **Answer Directly When Possible**:
   - If user asks "show my applications" and you already called getApplicationsByCandidate() → ANSWER immediately
   - Don't call getJob() for every application unless user specifically asks for job details

## Response Format

Always structure your response:
1. **Direct Answer First** (1-2 sentences)
2. **Supporting Details** (bullet points or table)
3. **Next Steps** (optional, only if relevant)

Example:
"You have 3 active applications. Here's your current status:

• **Senior SRE (J001)**: Technical Interview stage - 2 interviews scheduled next week
• **Frontend Engineer (J002)**: Offer Extended - Expires in 4 days ⚠️
• **Data Engineer (J003)**: Rejected - Shift incompatibility

Your most urgent action: Respond to the Frontend Engineer offer by [date]."
"""
```

---

### 4. Structured Output & Convergence Patterns (HIGH - P1)

#### 4.1 Add Tool Call Tracking
**File**: `careers-ai-service/src/agent/v2_graph.py`

```python
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    candidate_id: str
    application_id: str | None
    tool_call_count: int  # NEW: Track tool calls
    tools_called: list[str]  # NEW: Track which tools were called

def call_model(state: AgentState):
    # Increment tool call counter
    tool_call_count = state.get("tool_call_count", 0)

    # GUARDRAIL: Stop if too many tool calls
    if tool_call_count >= 10:
        return {
            "messages": [AIMessage(content=
                "I've made multiple tool calls but need more information. "
                "Could you please rephrase your question or be more specific?"
            )],
            "tool_call_count": tool_call_count
        }

    response = llm.invoke(state["messages"])

    # Update counter if tools were called
    new_count = tool_call_count + (1 if response.tool_calls else 0)
    tools_called = state.get("tools_called", [])
    if response.tool_calls:
        tools_called.extend([tc["name"] for tc in response.tool_calls])

    return {
        "messages": [response],
        "tool_call_count": new_count,
        "tools_called": tools_called
    }
```

---

### 5. Observability & Monitoring (HIGH - P1)

#### 5.1 Add Tool Call Metrics
**File**: `careers-ai-service/src/observability/metrics.py`

```python
from prometheus_client import Counter, Histogram, Gauge

# Tool call metrics
tool_calls_total = Counter(
    "agent_tool_calls_total",
    "Total tool calls by tool name",
    ["tool_name", "candidate_id"]
)

tool_call_errors = Counter(
    "agent_tool_call_errors_total",
    "Tool call errors by tool name and error type",
    ["tool_name", "error_type"]
)

agent_iterations = Histogram(
    "agent_iterations_count",
    "Number of iterations per request",
    buckets=[1, 3, 5, 10, 15, 20, 25, 30]
)

agent_recursion_limit_hit = Counter(
    "agent_recursion_limit_hit_total",
    "Requests that hit recursion limit"
)

# In tool execution:
def execute_tool(tool_name: str, params: dict, candidate_id: str):
    tool_calls_total.labels(tool_name=tool_name, candidate_id=candidate_id).inc()
    try:
        result = tools[tool_name](**params)
        return result
    except Exception as e:
        tool_call_errors.labels(
            tool_name=tool_name,
            error_type=type(e).__name__
        ).inc()
        raise
```

#### 5.2 Add Alerting Rules
**File**: `careers-ai-service/deployment/prometheus-alerts.yml`

```yaml
groups:
  - name: agent_guardrails
    interval: 30s
    rules:
      # Alert on excessive tool calls
      - alert: AgentExcessiveToolCalls
        expr: rate(agent_tool_calls_total[5m]) > 50
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Agent making excessive tool calls"
          description: "Tool call rate {{ $value }} calls/sec exceeds threshold"

      # Alert on recursion limit hits
      - alert: AgentRecursionLimitHit
        expr: increase(agent_recursion_limit_hit_total[5m]) > 5
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Multiple requests hitting recursion limit"
          description: "{{ $value }} requests hit recursion limit in last 5 minutes"

      # Alert on tool call errors
      - alert: AgentToolCallErrors
        expr: rate(agent_tool_call_errors_total[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High tool call error rate"
          description: "Error rate {{ $value }} errors/sec for tool {{ $labels.tool_name }}"
```

---

### 6. Testing Strategy Updates (MEDIUM - P2)

#### 6.1 Add Guardrail-Specific Tests
**File**: `careers-ai-service/tests/test_guardrails.py`

```python
import pytest
from fastapi.testclient import TestClient

def test_recursion_limit_prevents_infinite_loop(client: TestClient):
    """Test that recursion limit stops infinite loops"""
    response = client.post("/api/v2/agent/invoke", json={
        "thread_id": "test-recursion",
        "candidate_id": "C001",
        "message": "Tell me everything about everything repeatedly"  # Intentionally vague
    })

    # Should complete within reasonable time
    assert response.status_code in [200, 504]  # Success or timeout

    # If successful, check iteration count
    if response.status_code == 200:
        data = response.json()
        # Should not exceed recursion limit
        assert data.get("iterations", 0) <= 25

def test_invalid_job_id_format_rejected(client: TestClient):
    """Test that hallucinated job IDs are rejected"""
    # Simulate agent trying to call getJob with invalid ID
    with pytest.raises(ValueError, match="Invalid job_id format"):
        job_tools.getJob("JSeniorSRE")  # Should fail validation

    with pytest.raises(ValueError, match="Invalid job_id format"):
        job_tools.getJob("job-001")  # Should fail validation

    # Valid ID should pass
    result = job_tools.getJob("J001")
    assert result is not None

def test_request_timeout_enforced(client: TestClient):
    """Test that requests timeout after 60 seconds"""
    import time
    start = time.time()

    response = client.post("/api/v2/agent/invoke", json={
        "thread_id": "test-timeout",
        "candidate_id": "C001",
        "message": "Some query that might loop"
    })

    elapsed = time.time() - start

    # Should timeout within 65 seconds (60s limit + 5s grace)
    assert elapsed < 65
    assert response.status_code in [200, 504]
```

---

## 🎯 Implementation Priority

### Phase 1: Critical Fixes (Deploy Immediately - P0)
**Timeline**: 1-2 days

- [ ] Add `recursion_limit=25` to StateGraph
- [ ] Add 60-second request timeout
- [ ] Add ID format validation to all tool methods
- [ ] Update tool schemas with format constraints and examples
- [ ] Update agent system prompt with strict ID usage rules

**Acceptance Criteria**:
- ✅ Test Prompt 1.1 completes in < 30 seconds
- ✅ Test Prompt 2.1 succeeds without hallucinating job IDs
- ✅ No requests exceed 60 seconds
- ✅ All tool calls validate ID format before execution

### Phase 2: Observability (Deploy Week 2 - P1)
**Timeline**: 3-5 days

- [ ] Add tool call tracking to state
- [ ] Add Prometheus metrics for tool calls, errors, iterations
- [ ] Add Prometheus alerting rules
- [ ] Add Langfuse tracking for tool call chains
- [ ] Add dashboard for monitoring tool usage patterns

**Acceptance Criteria**:
- ✅ Can see tool call count per request in metrics
- ✅ Alerts fire when recursion limit is hit
- ✅ Can trace tool call chains in Langfuse

### Phase 3: Enhanced Testing (Deploy Week 3 - P2)
**Timeline**: 3-5 days

- [ ] Add guardrail-specific tests
- [ ] Add load tests with recursion scenarios
- [ ] Add contract tests for tool input validation
- [ ] Add chaos tests (simulate downstream failures)

**Acceptance Criteria**:
- ✅ Test suite includes 20+ guardrail tests
- ✅ Load tests verify no infinite loops under load
- ✅ Contract tests verify all tools validate inputs

---

## 📊 Expected Improvements

### Before Fixes (Current State)
- ❌ Prompt 1.1: **4+ minutes**, 600+ observations, manual termination required
- ❌ Prompt 2.1: **Fails** with "Job not found: JSeniorSRE"
- ❌ No guardrails on recursion
- ❌ No input validation
- ❌ No observability

### After Fixes (Target State)
- ✅ Prompt 1.1: **< 10 seconds**, max 25 iterations, clean convergence
- ✅ Prompt 2.1: **Succeeds** in < 5 seconds, uses correct job IDs (J001, J002, J003)
- ✅ Hard limits prevent runaway requests
- ✅ All tool inputs validated
- ✅ Full observability with metrics and alerts

---

## 🔗 LLD Document Updates Required

The following sections need to be added/updated in the LLD documents:

### Section to Add: "Agent Guardrails & Convergence"

```markdown
## Agent Guardrails & Convergence

### Recursion Limits
- **StateGraph recursion_limit**: 25 iterations max
- **Request timeout**: 60 seconds hard stop
- **Tool call limit per request**: 10 tool calls max (tracked in state)

### ID Validation Strategy
All tool parameters representing entity IDs MUST be validated against these patterns:
- Job IDs: `^J\d{3}$` (e.g., J001, J002)
- Application IDs: `^A\d{3}$` (e.g., A001, A002)
- Candidate IDs: `^C\d{3}$` (e.g., C001, C002)
- Group IDs: `^AG\d{3}$` (e.g., AG001, AG002)

Validation occurs in two layers:
1. **Tool schema** (Pydantic/JSON Schema): Documents format in description
2. **Tool implementation** (Java @Tool method): Rejects invalid format before downstream call

### Convergence Patterns
The agent follows these patterns to ensure convergence:

1. **Tool Call Sequencing**: Call foundational tools first (e.g., getApplicationsByCandidate) before detail tools (e.g., getJob)
2. **Stop Conditions**: Agent stops when:
   - Sufficient data collected to answer query
   - Last tool call returned complete information
   - Tool call count reaches limit
   - Recursion limit reached
   - Request timeout approaching
3. **No Speculative Calls**: Agent does NOT call tools "just in case" or "for completeness"

### Anti-Patterns (Prohibited)
❌ **ID Inference**: Never guess IDs from names/titles (e.g., "Senior SRE" → "JSeniorSRE")
❌ **Redundant Calls**: Don't call same tool multiple times with same params
❌ **Chain Speculation**: Don't call tools without using previous tool results
❌ **Unbounded Loops**: Don't loop through results calling detail tools for each item without limit
```

---

## ✅ Action Items

**For Engineering Team**:
1. Review and approve this fix strategy
2. Create Jira tickets for each phase (3 epics: P0, P1, P2)
3. Assign developers to Phase 1 (Critical Fixes)
4. Schedule deployment: Phase 1 by end of week, Phase 2 following week

**For QA Team**:
1. Update test suite with guardrail-specific tests
2. Add performance benchmarks for response time (< 30s for complex queries)
3. Create chaos testing scenarios (downstream failures, slow responses)

**For DevOps Team**:
1. Set up Prometheus metrics collection
2. Configure alerting rules
3. Add Grafana dashboard for agent health monitoring

**For Product/PM**:
1. Review and approve 60-second timeout (user experience)
2. Define error message copy for timeout scenarios
3. Document known limitations in product docs

---

## 📚 References

- [LangGraph Recursion Limits](https://langchain-ai.github.io/langgraph/concepts/low_level/#recursion-limit)
- [Pydantic Validation](https://docs.pydantic.dev/latest/concepts/validators/)
- [Prometheus Alerting](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
- [LLM Agent Best Practices](https://www.anthropic.com/research/building-effective-agents)
