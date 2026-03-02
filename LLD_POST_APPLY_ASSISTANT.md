# Low Level Design
## post_apply_assistant Integration

| Field | Detail |
|---|---|
| **Document Version** | 1.0 |
| **Status** | In-progress |
| **Last Updated** | 2026-03-02 |
| **Component** | careers-ai-service (existing, Add new Primary Assistant 2 with post_apply_assistant) · candidate-mcp (new) |
| **Parent System** | Careers AI Platform |
| **Depends On** | cx-applications · talent-profile-service · job-sync-service · careers-data-schema |

---

## Table of Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Glossary](#2-glossary)
3. [System Context](#3-system-context)
4. [Architecture Overview](#4-architecture-overview)
5. [Component Design](#5-component-design)
    5.1 [v2 API Route — careers-ai-service](#51-v2-api-route--careers-ai-service)
    5.2 [post_apply_assistant — Sub-assistant](#52-post_apply_assistant--sub-assistant)
    5.3 [candidate-mcp — Architecture](#53-candidate-mcp--architecture)
    5.4 [Three-Layer Data Transformation Pipeline](#54-three-layer-data-transformation-pipeline)
    5.5 [Agent Guardrails & Anti-Hallucination](#55-agent-guardrails--anti-hallucination)
        5.5.1 [Recursion & Iteration Limits](#551-recursion--iteration-limits)
        5.5.2 [ID Validation Strategy](#552-id-validation-strategy)
        5.5.3 [Convergence Patterns](#553-convergence-patterns)
6. [Key Data Flows](#6-key-data-flows)
7. [Integration Design](#7-integration-design)
8. [Security Design](#8-security-design)
9. [Resilience Design](#9-resilience-design)
10. [Observability Design](#10-observability-design)
11. [Caching Design](#11-caching-design)
    11.1 [MCP Static Resource Schema Cache — careers-ai-service side](#111-mcp-static-resource-schema-cache--careers-ai-service-side)
    11.2 [LangGraph Thread State — Conversation Checkpointer](#112-langgraph-thread-state--conversation-checkpointer)
    11.3 [Within-Session Tool Response Cache — careers-ai-service side](#113-within-session-tool-response-cache--careers-ai-service-side)
    11.4 [Tool Response Cache — candidate-mcp side](#114-tool-response-cache--candidate-mcp-side)
    11.5 [Cache Hierarchy Summary](#115-cache-hierarchy-summary)
12. [Error Handling](#12-error-handling)
13. [Testing Strategy](#13-testing-strategy)
14. [Deployment](#14-deployment)
15. [Design Decisions](#15-design-decisions)
16. [Open Issues & Risks](#16-open-issues--risks)

---

## 1. Purpose & Scope

### 1.1 Purpose

This document describes the design for implementing a **primary assistant 2** and a
`post_apply_assistant` sub-assistant as a new feature within the existing `careers-ai-service`.
This service provides the agentic workflows for the candidate-facing AI assistant that will be
integrated into the `cx-web` UI. The new sub-assistant handles queries about a candidate's profile,
applications, assessments, and preferences by calling tools exposed by `candidate-mcp`.

The v1 primary assistant (with its existing job search assistant and job-search-service
direct HTTP calls) is **completely untouched**. The new capability is exposed under a
separate `/api/v2/agent/` route backed by a dedicated v2 LangGraph. In a future
phase, a single primary assistant with all sub-assistants will consolidate both v1
and v2 routes into one.

### 1.2 In Scope

- A new `/api/v2/agent/invoke` and `/api/v2/agent/stream` route in `careers-ai-service`
- A new v2 LangGraph graph containing only `post_apply_assistant` as a sub-node
- A `primary_assistant_2` orchestrator node that routes to `post_apply_assistant` for all post apply domain queries
- Connecting the v2 graph to `candidate-mcp` via the MCP client
- **App2App signature authentication** between `careers-ai-service` and `candidate-mcp`: signature generated per request, 5-minute default TTL, configurable per client in the `candidate-mcp` service registry
- **TLS connection pool configuration** for the httpx transport: shared persistent pool with HTTP/2 and TLS session resumption to eliminate per-tool-call handshake overhead
- Implementation of downstream REST client integration in `candidate-mcp`
- Schema sharing strategy: how `careers-data-schema` models flow through `candidate-mcp` into the LLM prompt
- Resilience, observability, and caching
- Testing strategy covering unit, integration, and contract tests
- **Agent guardrails**: recursion limits (10 iterations), request timeouts (30 seconds), tool call limits (10 per request)
- **ID validation and anti-hallucination measures**: regex validation for all entity IDs, two-layer validation (schema + implementation)
- **Convergence patterns and stop conditions**: tool calling sequence rules, explicit stop conditions, prohibition of speculative calls

### 1.3 Out of Scope

- **existing primary assistant (considered as v1)** — no changes to existing graph, nodes, tools, routing logic, or the `/api/v1/agent/` routes
- **Existing job search assistant** — untouched
- **Direct HTTP calls to job-search-service** — untouched
- **Consolidation of v1 and v2 into a single primary assistant** — future phase

### 1.4 Assumptions

- `careers-ai-service` is a Python Uvicorn + LangGraph application. The v2 graph runs in the same process as v1; v2 will use the MCP tool registry loaded at startup.
- The primary assistant makes direct HTTP calls to `job-search-service` for job data — this pattern is not used for the new sub-assistant; `candidate-mcp` is used instead.
- `careers-data-schema` is a shared Maven library containing canonical Java domain models used across all backend services.
- All service-to-service authentication uses App2App HMAC-SHA256 signature — both `careers-ai-service` → `candidate-mcp` and `candidate-mcp` → downstream services.
- `candidate-mcp` is a stateless MCP server that calls real downstream services using App2App signature authentication.

---

## 2. Glossary

| Term | Definition |
|---|---|
| **MCP** | Model Context Protocol — a standard for exposing tools and resources to LLM agents over HTTP |
| **LangGraph** | Python framework for building stateful multi-agent LLM workflows as directed graphs |
| **StateGraph** | LangGraph construct representing the agent workflow as nodes (agents) and edges (routing) |
| **Handoff Tool** | A LangGraph `@tool` that, when called by the primary assistant, routes execution to a named sub-assistant |
| **candidate-mcp** | The Java MCP server that exposes candidate domain tools and schema resources. Calls downstream services from its tools. |
| **careers-data-schema** | Shared Java Maven library containing canonical domain models used across all Careers platform services |
| **MCP Resource** | A static or templated data object served by the MCP server — fetched once at agent startup and embedded into LLM system prompts |
| **MCP Tool** | A callable function the LLM agent invokes at runtime to retrieve live data |
| **Circuit Breaker** | Resilience pattern that stops calls to a failing downstream service and returns a structured fallback |
| **Virtual Threads** | Java 21 lightweight threads that make blocking I/O safe within synchronous MCP tool handlers |
| **post_apply_assistant** | New LangGraph sub-assistant handling candidate profile, application, assessment, and preferences queries |
| **primary_assistant_2** | New orchestrator node introduced under the v2 API route; contains only `post_apply_assistant` as a sub-node for now |
| **App2App Auth** | HMAC-SHA256 request signature scheme used between `careers-ai-service` (caller) and `candidate-mcp` (receiver). No user identity or OAuth2 token involved — machine-to-machine trust only. |
| **Signature TTL** | The validity window of an App2App request signature. Default 5 minutes; configurable per registered client in the `candidate-mcp` service registry. |

---

## 3. System Context

The diagram below shows the existing platform and the new
components added under the v2 route.

```mermaid
graph TB
    User(["Candidate"])

    subgraph "careers-ai-service"
        subgraph "v1 — existing, no changes"
            V1API["POST /api/v1/agent/*"]
            PA["primary_assistant"]
            JSA["job_search_assistant"]
            V1API --> PA
            PA -->|"existing handoff"| JSA
        end

        subgraph "v2 — new"
            V2API["POST /api/v2/agent/*"]
            V2PA["primary_assistant_2"]
            PAA["post_apply_assistant"]
            V2API --> V2PA
            V2PA -->|"new handoff"| PAA
        end
    end

    subgraph "MCP Layer"
        CMCP["candidate-mcp"]
    end

    subgraph "Downstream Services"
        JSS["job-search-service"]
        CXA["cx-applications"]
        TPS["talent-profile-service"]
        JSS2["job-sync-service"]
    end

    subgraph "Shared Libraries"
        CDS["careers-data-schema"]
    end

    User -->|"v1 REST"| V1API
    User -->|"v2 REST / SSE"| V2API
    JSA -->|"REST + App2App"| JSS
    PAA -->|"JSON RPC 2.0 + App2App"| CMCP
    CMCP -->|"REST + App2App"| CXA
    CMCP -->|"REST + App2App"| TPS
    CMCP -->|"REST + App2App"| JSS2
    CDS -.->|"domain models"| CMCP
    CDS -.->|"domain models"| CXA
    CDS -.->|"domain models"| TPS
```

---

## 4. Architecture Overview

### 4.1 LangGraph Graphs

Two separate compiled `StateGraph` instances exist in the same Python process. The
v1 graph is unchanged. The v2 graph is new.

#### v1 Graph — Existing (no changes)

```mermaid
flowchart TD
    S1(["START"])
    PA["primary_assistant"]
    JSA["job_search_assistant"]
    E1(["END"])

    S1 --> PA
    PA -->|"job search query"| JSA
    PA -->|"direct answer"| E1
    JSA --> E1
```

#### v2 Graph — New

A minimal graph containing only the `primary_assistant_2` and `post_apply_assistant`
nodes. In a future consolidation phase this graph will absorb the job search assistant
and replace v1 entirely.

```mermaid
flowchart TD
    S2(["START"])

    V2PA["primary_assistant_2"]

    PAA["post_apply_assistant (application, profile, preferences, assessments)"]

    E2(["END"])

    S2 --> V2PA
    V2PA -->|"channel=post_apply"| PAA
    V2PA -->|"direct answer"| E2
    PAA --> E2
```

### 4.2 MCP Component Architecture

```mermaid
flowchart LR
    subgraph "Python Agent Process"
        REG["MCPToolRegistry post_apply_tools[] schemas embedded in LLM prompts"]
        PAA["post_apply_assistant"]
        REG --> PAA
    end

    subgraph "candidate-mcp"
        subgraph "Tool Layer"
            PT["ProfileTools"]
            AT["ApplicationTools"]
            JT["JobTools"]
        end
        subgraph "Client Layer"
            TC["TalentProfileClient"]
            CC["ApplicationsClient"]
            JSC["JobClient"]
        end
        subgraph "Resource Layer"
            SR["Static Resources (schema, status mappings)"]
        end
        PT --> TC
        AT --> CC
        JT --> JSC
    end

    PAA -->|"Streamable HTTP JSON RPC 2.0 + App2App headers"| PT
    PAA -->|"Streamable HTTP JSON RPC 2.0 + App2App headers"| AT
    PAA -->|"Streamable HTTP JSON RPC 2.0 + App2App headers"| JT
    REG -->|"startup: load schemas"| SR

    TC -->|"REST"| TPS[("talent-profile-service")]
    CC -->|"REST"| CXA[("cx-applications")]
    JSC -->|"REST"| JSS[("job-sync-service")]
```

---

## 5. Component Design

### 5.1 v2 API Route — careers-ai-service

A new pair of FastAPI routes is registered under the `/api/v2/agent/` prefix in
the existing `careers-ai-service` service. They are wired to the v2 compiled graph.
The v1 routes and v1 graph remain completely independent.

| Route | v1 (existing) | v2 (new) |
|---|---|---|
| Sync invoke | `POST /api/v1/agent/invoke` | `POST /api/v2/agent/invoke` |
| SSE stream | `POST /api/v1/agent/stream` | `POST /api/v2/agent/stream` |
| Backing graph | v1 graph (primary + job search) | v2 graph (primary v2 + post apply) |
| MCP tools used | None (direct HTTP to job-search-service) | tools from `candidate-mcp` |
| Auth to MCP | N/A | App2App signature |

#### Migration Path

```mermaid
flowchart LR
    NOW["Now v1: primary + job search; v2: v2 primary + post_apply"]
    FUTURE["Future Single primary + all sub-assistants /api/v1 retired /api/v2 becomes canonical"]

    NOW -->|"once all sub-assistants are stable"| FUTURE
```

---

### 5.2 post_apply_assistant — Sub-assistant

#### Responsibilities

- Respond to queries about a candidate's profile, applications status, assessment results, and stated preferences.
- Call `candidate-mcp` tools to retrieve live data (Layer 1 projected context).
- Apply a query-specific context filter before passing tool results to the LLM (Layer 2).
- Produce clear, empathetic, candidate-facing responses using its persona system prompt and named response templates (Layer 3).
- **This assistant faces the actual candidate directly** — Tone, language, and content are designed accordingly.

See **Section 5.4** for the full three-layer transformation pipeline.

#### State Schema — v2 State

The v2 graph uses its own `AgentState`. It does not share or modify the v1 state schema.

| Field | Type | Default | Description |
|---|---|---|---|
| `messages` | `list[BaseMessage]` | `[]` | Conversation history (LangGraph managed) |
| `talent_profile_id` | `str` | `""` | Candidate context for tool calls — **mandatory at the sub assistant boundary** |
| `ats_requisition_id` | `str` | `""` | Optional. When set, the assistant focuses on this specific application. When absent, the assistant retrieves all applications for the candidate. |
| `thread_id` | `str` | auto | Coversation thread ID |
| `correlation_id` | `str` | auto | Request trace ID |

#### State Injection into LLM Context — Callable Prompt Pattern

LangGraph state fields such as `talent_profile_id` and `ats_requisition_id` are **not
automatically visible to the LLM**. The LLM operates only on the `messages` list.
Without explicit injection the LLM will prompt the user to provide IDs it already has.

Both `primary_assistant_2` and `post_apply_assistant` use **callable prompt
functions** rather than static strings. At each inference step the callable reads
the current state and appends an `Active Request Context` block to the system
prompt before passing it to the LLM.

```mermaid
flowchart LR
    STATE["LangGraph State (talent_profile_id: C001, ats_requisition_id: R001, messages: [...])"]
    CALLABLE["Callable Prompt _build_context_block(): Reads talent_profile_id, Reads ats_requisition_id -> Builds context block"]
    SYSMSG["System Message ...base prompt... ## Active Request Context <instruction>"]
    LLM["LLM (has full context never asks for IDs)"]

    STATE --> CALLABLE
    CALLABLE --> SYSMSG
    SYSMSG --> LLM
```

The injected instruction differs based on whether `ats_requisition_id` is present:

| Scenario | `ats_requisition_id` in state | Instruction injected |
|---|---|---|
| v2 primary (with app) | set | "Route immediately — talentProfileId and atsRequisitionId are already known." |
| v2 primary (no app) | empty | "Route immediately — talentProfileId is known. No specific application — the specialist will retrieve all priority applications." |
| post_apply (with app) | set | "A specific application is in scope. Use both IDs directly in tool calls." |
| post_apply (no app) | empty | "No specific application was provided. Call `getActionableApplications(talentProfileId)` " |

This pattern ensures:
- When `ats_requisition_id` is absent, `post_apply_assistant` automatically broadens its scope to the full applications list rather than asking for clarification.
- The base prompt strings are built once at startup; the context block is appended cheaply per inference step with no additional LLM calls.

#### Handoff Trigger Conditions

The primary assistant calls `transfer_to_post_apply_assistant` when the user's query
concerns any of the following:

- A candidate's profile and job applications
- Status, history, or timeline of a specific application
- What happens next in the application process
- Assessment results, scores, or completion status
- Candidate preferences (location, job, renewal)

#### Tool Set

All tools are served by `candidate-mcp`. The sub-assistant has access to various **8 tools** across
three domains. The **Job** tool is used to enrich
application context: every application carries a `jobId`, so the assistant fetches job details
(title, location, required assessment code, shift) to give the candidate meaningful context
alongside their application status.

| Domain | Tool | How it is used by post_apply_assistant |
|---|---|---|
| **Profile** (3 tools) | `getTalentProfile` | Candidate's entire profile (PII striped) |
| | `getPreferences` | Candidate's job, location preferences |
| | `getAssessmentResults` | Candidate's assessment results |
| **Application** (4 tools) | `getActionableApplications` | Sorted and grouped actionable applications list with SLA, status mapping |
| | `getApplicationDetails` | Current stage, days in stage, workflow history, metadata |
| | `getAtsApplications` | All the raw post-apply applications list (PII striped) |
| | `getApplicationGroups` | All the raw pre-apply applications list (PII striped) |
| **Job** (1 tool) | `getJobDetails` | Enriches application context: resolves `jobId` → job title, location, required assessment code, job type, shift |

**Total**: 8 tools

**Typical job enrichment pattern:**

```mermaid
sequenceDiagram
    participant PAA as post_apply_assistant
    participant CMCP as candidate-mcp

    PAA->>CMCP: getActionableApplications(talentProfileId)
    CMCP-->>PAA: [{ atsRequisitionId, jobId, status, stage, ... }, ...]

    note over PAA: applications contains jobId → enrich

    PAA->>CMCP: getJobDetails(jobIds)
    CMCP-->>PAA: { title, location, requiredAssessmentCode, shift }

    note over PAA: now has full context to answer<br/>"Where is this job?", "What assessments<br/>are required?", "What is the job title?"
```

---

### 5.3 candidate-mcp — Architecture

`candidate-mcp` is a stateless MCP server built from java MCP starter kit. Every tool handler calls the
appropriate downstream service, passes the response through `ContextTransformer`
to strip PII and project agent-safe fields, and returns the result as JSON.
`candidate-mcp` is the single point where raw backend data is sanitised — no PII or
internal metadata ever reaches the agents or the LLM.

#### Downstream Service Responsibilities

| Service | Tools it backs | Data it provides |
|---|---|---|
| `talent-profile-service` | `getTalentProfile`, `getPreferences`, `getAssessmentResults` | Candidate profiles, questionnaire responses, assessment results and preferences |
| `cx-applications` | `getActionableApplications`, `getApplicationDetails`, `getAtsApplications`, `getApplicationGroups` | Application documents, stage history, statuses |
| `job-sync-service` | `getJobDetails` | Job requisition details — title, location, assessment codes, job type, shift details |

#### Package Structure

```
candidate-mcp/
├── config/
│   ├── McpConfiguration          Tool & resource registration
│   ├── WebClientConfiguration    One WebClient bean per downstream service
│   ├── ResilienceConfiguration   Circuit breaker & retry registries
│   └── SecurityConfiguration     App2App SignatureProvider (outbound) - inbound is handled by MCP's sidecar
├── tool/
│   ├── ProfileTools              Delegates to TalentProfileClient → transformer
│   ├── ApplicationTools          Delegates to CxApplicationsClient → transformer
│   ├── JobTools                  Delegates to JobSyncClient → transformer
│   └── AssessmentTools           Delegates to TalentProfileClient → transformer
├── transformer/
│   └── ContextTransformer   PII strip + field projection for each domain
├── resource/
│   └── StaticResources           Status mappings, Serialises careers-data-schema → JSON Schema MCP resources
├── client/
│   ├── TalentProfileClient       WebClient wrapper for talent-profile-service
│   ├── CxApplicationsClient      WebClient wrapper for cx-applications
│   └── JobSyncClient             WebClient wrapper for job-sync-service
└── dto/
    ├── profile/                  AgentContext DTOs for profile domain
    ├── application/              AgentContext DTOs for application domain
    ├── job/                      AgentContext DTOs for job domain
    └── assessment/               AgentContext DTOs for assessment domain
```

#### Technology Stack

| Concern | Technology |
|---|---|
| Framework | Spring Boot · Java 21 |
| MCP SDK | Spring AI (stateless streamable HTTP) |
| HTTP client | WebClient (Project Reactor) + virtual threads for safe blocking in MCP handlers |
| Domain models | `careers-data-schema` (Maven compile dependency) |
| Auth (inbound from agent) | App2App HMAC-SHA256 signature validation |
| Auth (outbound to downstream) | App2App HMAC-SHA256 signature — one shared secret for all downstream services |
| Resilience | Resilience4j — circuit breaker + retry, one instance per downstream service |
| Observability | Micrometer + OpenTelemetry |

---

### 5.4 Three-Layer Data Transformation Pipeline

Data passes through three distinct transformation stages before reaching the candidate.
Each layer has a single, well-bounded responsibility.

```mermaid
flowchart TD
    RAW["Backend services · Cosmos Document Full record · all fields PII included · internal metadata Database artifacts · audit fields"]

    L1["Layer 1 — candidate-mcp Transformer · PII stripped · internal fields dropped · Projected into AgentContext DTOs · Agent-neutral: same output regardless of which assistant calls this tool"]

    L2["Layer 2 — post_apply_assistant Context Filter · Query-specific field selection · Only what this LLM turn needs · Reduces context window consumption · Prevents LLM reasoning over noise"]

    L3["Layer 3 — post_apply_assistant Response Formatter · Candidate-facing persona · Human language · Empathetic · jargon-free · actionable · Driven by system prompt + response templates"]

    OUT(["Candidate-facing Response"])

    RAW -->|"REST → WebClient"| L1
    L1 -->|"MCP tool result JSON"| L2
    L2 -->|"filtered context in LLM prompt"| L3
    L3 --> OUT
```

#### **Layer 1 — MCP Transformer**

`candidate-mcp` is **agent-neutral**: it does not know which assistant or which user
type is calling it. Every tool handler maps the raw downstream response to a projected
`AgentContext` DTO before returning. This projection is the same for every caller.

**PII fields always stripped (never appear in any tool response):**

| Category | Fields Excluded |
|---|---|
| Direct identifiers | National ID / NI number, passport number, exact date of birth |
| Contact details | Personal phone number, home address lines, personal email |
| Internal ATS | Database row IDs, audit `created_by` / `modified_by`, internal routing codes, lock/version fields |
| Downstream artefacts | Cosmos `_etag`, `_ts`, partition keys, internal service correlation IDs |

**Fields included in agent context:**

| Domain | Included Fields |
|---|---|
| Profile | Candidate ID, display name, Assessment results with status details |
| Application | Application ID, job ID, status enums, current stage name, days in current stage, SLA, stage history, ats source |
| Job | Job ID, title, job type, location, shift details |

```mermaid
flowchart LR
    subgraph "candidate-mcp tool handler"
        RAW_DTO["Raw downstream DTO (full Cosmos fields)"]
        PROJ["ContextTransformer · strip PII fields · drop internal metadata · map enums to stable names · compute derived fields"]
        AC["AgentContext DTO (projected · safe · stable)"]

        RAW_DTO --> PROJ --> AC
    end

    CMCP_OUT["MCP tool result JSON → post_apply_assistant"]
    AC --> CMCP_OUT
```

---

#### **Layer 2 — Post Apply Assistant Context Filter**

The `post_apply_assistant` receives the agent-neutral context from Layer 1 — which is
already PII-safe but may still contain fields irrelevant to the current query. A
second filter prevents the LLM from reasoning over unrelated fields and keeps token
usage predictable.

This filter operates in two complementary ways:

**System prompt instructions**
The `post_apply_assistant` system prompt includes explicit field-focus directives.
The LLM is told which fields to prioritise for each query type and to disregard the
rest.

```mermaid
flowchart LR
    TOOL_RESULT["MCP tool result (all projected fields)"]

    subgraph "post_apply_assistant"
        subgraph "system prompt"
            DIR["Field focus directives"]
        end
    end

    LLM["LLM reasoning (attends to relevant fields per directive)"]

    TOOL_RESULT --> LLM
    DIR --> LLM
```

**Programmatic filter (for large payloads)**
Where a tool response may contain many items (e.g. `getActionableApplications`
returning a list with many context fields), an agent `ContextFilter`
trims the payload before it enters the LLM message. This is a safety net for
token-budget control, not the primary filtering mechanism.

---

#### **Layer 3 — post_apply_assistant Response Formatter**

The `post_apply_assistant` faces the actual candidate. Its system prompt and response templates are designed for that audience:
clear, empathetic, jargon-free, and actionable.

**System prompt — candidate persona directives:**

```mermaid
flowchart TD
    SP["post_apply_assistant System Prompt"]

    SP --> T1["Tone · Warm and professional · First person plural when   referring to the process · Never expose internal tool names   or field keys to the candidate"]

    SP --> T2["Status mappings to human descriptive response"]

    SP --> T3["Response Structure · Lead with the current status clearly · Follow with what happens next · End with a concrete action if one exists · Never speculate on timeline if not in data"]

    SP --> T4["Sensitive Topics · Rejection: constructive, forward-looking · Offer: factual summary, do not advise on negotiation or decision · Delays: honest, no false reassurance"]
```

**Named Response Templates**

For recurring query patterns, response templates provide consistent structure. The
LLM fills in the candidate-specific data; the template enforces the shape.

| Template | Trigger Pattern | Structure |
|---|---|---|
| `status-update` | "What's the status of my application?" | Current stage → time in stage (relative) → what happens next |
| `next-steps-guide` | "What should I do now?" / "What do I need to prepare?" | Stage-specific actions → preparation tips → expected timeline |
| `assessment-summary` | "How did I do in the assessment?" | Score context → pass/fail → next stage if passed |
| `rejection-debrief` | Application status is `REJECTED` | Acknowledgement → details if reason available |
| `workflow-overview` | "Can you walk me through all the statuses of my application?" | Chronological list → statuses in history data → any requiring action |

---

### 5.5 Agent Guardrails & Anti-Hallucination

This section describes critical production guardrails implemented to prevent infinite loops, ID hallucination, and other agent failure modes.

#### 5.5.1 Recursion & Iteration Limits

**Problem**: Without hard limits, the agent can enter infinite tool-calling loops, consuming resources and providing poor user experience.

**Solution — Three-Layer Limit Strategy**:

| Layer | Limit | Configuration | Purpose |
|---|---|---|---|
| **StateGraph recursion_limit** | 25 iterations | `StateGraph(AgentState, recursion_limit=25)` | Hard stop on LangGraph execution — prevents infinite graph loops |
| **Request timeout** | 30 seconds | `asyncio.wait_for(agent_executor.ainvoke(), timeout=30.0)` | Hard stop at API layer — protects backend resources |
| **Tool call limit per request** | 10 tool calls | Tracked in `AgentState.tool_call_count` | Soft limit — agent returns helpful message when exceeded |

**Implementation**:

```python
graph = StateGraph(
    AgentState,
    recursion_limit=25  # Max 25 iterations before hard stop
)
```

**Rationale**:
- Prevents infinite loops and runaway requests
- 15 iterations = approximately 3-4 tool calls with reasonable reasoning steps
- Industry standard for production LLM agents
- Provides multiple layers of protection (graph-level, API-level, application-level)

**Request-Level Timeout Implementation**:

```python
@router.post("/api/v2/agent/invoke")
async def invoke_agent(request: AgentRequest):
    try:
        # Add 30-second timeout for entire request
        result = await asyncio.wait_for(
            agent_executor.ainvoke(request),
            timeout=30.0  # 30 seconds max
        )
        return result
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Agent execution timeout. Please try a simpler query or contact support."
        )
```

**Tool Call Tracking in State**:

```python
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

#### 5.5.2 ID Validation Strategy

**Problem**: Agent hallucinates entity IDs by inferring them from names/titles.

**Solution — Two-Layer Validation**:

All tool parameters representing entity IDs MUST be validated against these patterns before downstream calls:

| Entity Type | Format Pattern | Valid Examples | Invalid Examples |
|---|---|---|---|
| **Job ID** | WD `R-XXX`, CP `CP-XXXX-XXXX` or `XXXXXXXX` | R-123456, CP-1234-5678, 12345678 | Cashier, R123, Engineer |
| **Profile ID** | `UUID` | valid UUIDs | name, email |
| **Application document ID** | `UUID` | valid UUIDs | ats application id, job IDs |

**Validation occurs in two layers**:

```mermaid
flowchart LR
    LLM["LLM generates tool call"]
    SCHEMA["Layer 1: Tool Schema (Pydantic/JSON Schema) Documents format in description"]
    IMPL["Layer 2: Tool Implementation (Java @Tool method) Rejects invalid format before downstream call"]
    DS["Downstream Service"]

    LLM --> SCHEMA
    SCHEMA --> IMPL
    IMPL -->|"Valid ID"| DS
    IMPL -->|"Invalid ID"| ERROR["Structured Error returned to LLM"]
```

---

#### 5.5.3 Convergence Patterns

**Problem**: Agent calls tools repeatedly without making progress toward answering the user's question.

**Solution — Explicit Tool Calling Sequence Rules**:

The agent follows these convergence patterns to ensure it stops when sufficient data is collected:

**1. Tool Call Sequencing**: Call foundational tools first before detail tools

| Query Type | Correct Sequence |
|---|---|
| "Show all my applications with it's location details" | `getActionableApplications(talentProfileId)` → Extract job IDs → Calling `getJobDetails()` for all the actionable applications |
| "What's the application status history of the application?" | `getActionableApplications(talentProfileId)` → Extract `applicationDocumentId`  → `getApplicationDetails(applicationDocumentId)` |

**2. Stop Conditions**: Agent stops when:

| Condition | Example |
|---|---|
| Sufficient data collected to answer query | User asks "What's my application status?" → `getApplicationStatus()` returns complete status → STOP (don't call `getAssessmentResults()` unless asked) |
| Last tool call returned complete information | `getActionableApplications()` returns full list → STOP (don't call `getJobDetails()` for each unless user asks for job details) |
| Tool call count reaches limit (10) | Agent has called 10 tools → return helpful message asking user to rephrase |
| Recursion limit reached (25 iterations) | Graph hits 25 iterations → hard stop with timeout error |

**3. No Speculative Calls**: Agent does NOT call tools "just in case" or "for completeness"

| Anti-Pattern | Why It's Wrong | Correct Behavior |
|---|---|---|
| "Let me also check your assessments in case they're relevant" | Adds unnecessary latency and cost | Only call `getAssessmentResults()` if user asks about assessments |
| Calling `getJobDetails()` for all 5 applications when user asks "How many applications do I have?" | User didn't ask for job details | Answer "You have 5 applications" directly from `getActionableApplications()` |
| Calling same tool multiple times with same parameters | Redundant, wastes resources | Use exact result from first call (or rely on session cache) |

---

#### 5.5.4 System Prompt Enhancements

**Problem**: LLM lacks explicit rules on ID usage, tool calling convergence, and anti-patterns.

**Solution — Strict ID Usage Rules in System Prompt**:

```python

POST_APPLY_ASSISTANT_PROMPT = """
You are post_apply_assistant, helping candidates track their job applications.

## CRITICAL RULES

1. **NEVER guess or infer IDs from names/titles**
     WRONG: User mentions "Senior SRE job" → You call getJobDetails("JSeniorSRE")
     CORRECT: Call getActionableApplications() first → Extract job_id from response → Use exact ID

2. **Tool Calling Sequence**:
   - To show all applications: getActionableApplications(talent_profile_id) → Extract job IDs → DONE
   - To get job details: getActionableApplications() FIRST → Then getJobDetails(exact_job_id)

3. **When to STOP calling tools**:
   - You have enough information to answer the user's question
   - Last tool call returned complete data
   - More tool calls won't add value to the answer
   - Don't call tools "just to check" or "for completeness"

4. **Answer Directly When Possible**:
   - If user asks "show my applications" and you already called getActionableApplications() → ANSWER immediately
   - Don't call getJobDetails() for every application unless user specifically asks for job details

## Response Format

Always structure your response:
1. **Direct Answer First** (1-2 sentences)
2. **Supporting Details** (bullet points or table)
3. **Next Steps** (optional, only if relevant)

Example:
"You have 3 active applications. Here's your current status:

• **Senior SRE (R-12345)**: Technical Interview stage - 2 interviews scheduled next week
• **Frontend Engineer (R-12346)**: Offer Extended - Expires in 4 days
• **Data Engineer (R-12347)**: Rejected - Received feedback on yyyy-mm-dd

Your most urgent action: Respond to the Frontend Engineer offer by [date]."
"""
```

**Anti-Patterns Explicitly Prohibited**:

| Anti-Pattern | Example | Why It's Prohibited |
|---|---|---|
| **ID Inference** | "Senior SRE" → "seniorSre" | Causes `Job not found` errors, breaks tool calls |
| **Redundant Calls** | Calling `getApplicationDetails(uuid)` twice in same conversation | Wastes resources, session cache should handle this |
| **Unbounded Loops** | Looping through all 5 applications calling `getJobDetails()` for each | Hits tool call limit, poor UX |
| **Speculative Completeness** | "Let me also check your assessments just in case" | Adds unnecessary latency |

---

#### 5.5.5 Tool Schema Improvements

Tools that take entity IDs as parameters include explicit format instructions in their schema descriptions. This guides the LLM to provide correctly formatted IDs and reduces hallucination.

```java
@Tool(
    description = """
    Get current details of a specific application.

    REQUIRED: applicationDocumentId (format: uuid).
    DO NOT guess. ONLY use IDs from getActionableApplications() results if you have them.

    Valid: any UUIDs
    INVALID: app-doc-1, JOB_AP_12345
    """
)
public ApplicationDetails getApplicationDetails(
    @ToolParam(
        description = "Exact application ID in format UUID (e.g., 123e4567-e89b-12d3-a456-426614174000).",
        required = true
    )
    String applicationDocumentId
) {
    // Validate format before downstream call
    if (!applicationDocumentId_PATTERN.matcher(applicationDocumentId).matches()) {
        throw new IllegalArgumentException(
            "Invalid applicationDocumentId format: '" + applicationDocumentId + "'. " +
            "Expected format: UUID (e.g., 123e4567-e89b-12d3-a456-426614174000). "
        );
    }
}
```

---
### 5.7 Schema Bridge

This section describes how canonical Java domain models defined in `careers-data-schema` are made available to the Python LLM agent without any Python-side model definitions or code generation.

#### 5.7.1 The Problem

The Careers platform is a Java-first ecosystem. All domain models are defined once in the shared
`careers-data-schema` Maven library and used by every backend service, including
`cx-applications` and `talent-profile-service`.

The Python LangGraph agent sits outside this ecosystem. Without a bridge, three
problems arise:

- The LLM does not know the shape of data returned from tool calls, leading to
  hallucinated field names and incorrect reasoning.
- Schema changes in `careers-data-schema` silently break agent behaviour.
- Teams are forced to maintain parallel model definitions in Python alongside the
  authoritative Java ones.

#### 5.7.2 The Solution — MCP Static Resources as Schema Carrier

`candidate-mcp` takes `careers-data-schema` as a compile-time Maven dependency. At
startup, it serialises the **projected** `AgentContext` DTO shapes — not the raw
Cosmos document shapes — to JSON Schema and exposes them as MCP static resources.
The Python agent fetches these once at startup and embeds them into the LLM system
prompt before any conversation begins.

```mermaid
flowchart LR
    subgraph "Java Ecosystem"
        CDS["careers-data-schema (Maven compile)"]

        CMCP["candidate-mcp (Serialises models JSON Schema and expose)"]

        CDS -->|"dependency"| CMCP
    end

    subgraph "Python Agent — Startup"
        REG["MCPToolRegistry init_registry()"]
        LLM["LLM System Prompt (schema aware)"]

        REG -->|"build prompts"| LLM
    end

    CMCP -->|"ats://schema/* ats://workflow/application-stages"| REG
```

#### 5.7.3 Benefits

| Benefit | Detail |
|---|---|
| **Single source of truth** | Schema is authored once in `careers-data-schema`. No Python model to maintain alongside it. |
| **Zero schema drift** | A field rename or new enum value in Java propagates to the agent automatically when `candidate-mcp` is rebuilt and redeployed. |
| **No code generation pipeline** | No OpenAPI → Python dataclass step. The MCP resource is the contract. |
| **LLM grounding** | The LLM receives precise field names, types, required fields, and enum values in its system prompt. This directly improves tool call accuracy and eliminates hallucinated field names. |
| **Cross-team alignment** | Java engineers own the schema in a familiar Maven package. Python engineers consume it with no Java knowledge required. |
| **Deployment audit trail** | The schemas embedded in the prompt are version-locked to the `candidate-mcp` release. Every deployment produces a traceable snapshot of the schema the agent was operating with. |

#### 5.7.4 Schema Resources Exposed by candidate-mcp

Each schema resource describes the **projected agent-context shape** — the fields that
survive PII stripping and the Layer 1 transformer. Raw Cosmos document fields that are
stripped (PII, internal metadata, database artefacts) are not present.

| MCP Resource URI | Projected Source | Content (agent-safe fields only) |
|---|---|---|
| `ats://schema/profile` | `TalentProfileV2` | Assessment results, experience summary, questionnaire responses — no raw contact details |
| `ats://schema/application` | `AtsApplication` | Stage, status enum, history, metadata — no internal fields |
| `ats://schema/job` | `JobRequisition` | Title, location, job type, shift details — no internal fields |
| `ats://schema/application-stages` | `ApplicationStage` | Enum of all possible application stages with descriptions — no internal fields |

---

## 6. Key Data Flows

### 6.1 Agent Startup — Tool and Schema Loading

The Python application loads tools and embeds schemas once during startup, before
serving any request.

```mermaid
sequenceDiagram
    participant App as Python App (lifespan)
    participant Reg as MCPToolRegistry
    participant CMCP as candidate-mcp

    App->>Reg: init_registry(settings)
    Reg->>CMCP: get_tools()
    CMCP-->>Reg: post_apply tool list
    Reg->>CMCP: get_resources(schema URIs)
    note over CMCP: Resources serialised from careers-data-schema
    CMCP-->>Reg: JSON Schema blobs
    Reg->>App: registry ready
    App->>App: build_post_apply_prompt(schemas)
    note over App: Schemas embedded in LLM<br/>system prompt — LLM now knows<br/>exact field names and enums
    App->>App: compile StateGraph (add post_apply node)
    App->>App: serve requests
```

### 6.2 Happy Path — Post-Apply Query

End-to-end flow for a candidate querying their application status and next steps.

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI /invoke
    participant PA as primary_assistant
    participant PAA as post_apply_assistant
    participant CMCP as candidate-mcp
    participant CXA as cx-applications

    User->>API: POST /api/v2/agent/invoke {"message": "What is the status of my applications?"}
    API->>PA: ainvoke(AgentState)

    note over PA: primary_assistant_2 detects post-apply intent
    PA->>PA: call transfer_to_post_apply_assistant(reason)
    PA-->>API: Command(goto=post_apply_assistant)

    API->>PAA: ainvoke(AgentState)
    note over PAA: LLM selects tools, aided<br/>by embedded schema context

    PAA->>CMCP: getActionableApplications(talentProfileId) + WM-AUTH-SIGNATURE
    CMCP->>CXA: GET /v2/applications?talentProfileId={talentProfileId}
    CXA-->>CMCP: AtsApplicationDto (status, stage, history)
    CMCP-->>PAA: JSON  (fields match careers-data-schema)

    PAA->>CMCP: getJobDetails([jobIds]) + WM-AUTH-SIGNATURE
    CMCP->>CXA: GET /v1/unified-bulk-job-details?jobIds={jobIds}
    CXA-->>CMCP: List~JobRequisitionDto~ (title, location, job type)
    CMCP-->>PAA: JSON

    note over PAA: LLM synthesises empathetic<br/>response from tool outputs
    PAA-->>API: AIMessage (final answer)
    API-->>User: InvokeResponse {agent_used: "post_apply_assistant"}
```

### 6.3 Profile Query Flow

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI /invoke
    participant PA as primary_assistant
    participant PAA as post_apply_assistant
    participant CMCP as candidate-mcp
    participant TPS as talent-profile-service

    User->>API: POST /invoke {"message": "Show me the candidate's skills and assessment scores"}
    API->>PA: ainvoke(AgentState)

    note over PA: Profile/assessment intent detected
    PA->>PA: call transfer_to_post_apply_assistant(reason)
    PA-->>API: Command(goto=post_apply_assistant)

    API->>PAA: ainvoke(AgentState)

    PAA->>CMCP: getTalentProfile(talentProfileId)
    CMCP->>TPS: GET /v1/candidates/{id}/profile
    TPS-->>CMCP: CandidateProfileDto
    CMCP-->>PAA: JSON

    PAA->>CMCP: getAssessmentResults(talentProfileId)
    CMCP->>TPS: GET /v1/candidates/{id}/assessments
    TPS-->>CMCP: List~AssessmentResultDto~
    CMCP-->>PAA: JSON

    PAA-->>API: AIMessage
    API-->>User: InvokeResponse
```

### 6.4 SSE Streaming Path

```mermaid
sequenceDiagram
    actor User
    participant API as FastAPI /stream
    participant Graph as LangGraph astream_events
    participant PAA as post_apply_assistant
    participant CMCP as candidate-mcp

    User->>API: POST /stream (SSE)
    API->>Graph: astream_events(input, version="v2")

    Graph-->>API: on_chain_start {name: post_apply_assistant}
    API-->>User: event: handoff {from: primary, to: post_apply}

    Graph-->>API: on_tool_start {name: getApplicationStatus}
    API-->>User: event: tool_call {name: getApplicationStatus}

    CMCP-->>Graph: tool result
    Graph-->>API: on_tool_end

    loop LLM token generation
        Graph-->>API: on_chat_model_stream {chunk}
        API-->>User: event: token {content}
    end

    Graph-->>API: on_chain_end
    API-->>User: event: done {active_agent, tool_calls}
```

### 6.5 Downstream Call with Resilience

```mermaid
sequenceDiagram
    participant Tool as ApplicationTools
    participant CB as Circuit Breaker
    participant Retry as Retry (R4j)
    participant Client as CxApplicationsClient
    participant CXA as cx-applications

    Tool->>CB: check state
    alt Circuit CLOSED
        CB->>Retry: execute with retry
        Retry->>Client: getStatus(atsRequisitionId)
        Client->>CXA: GET /v1/applications/{id}/status
        alt Success
            CXA-->>Client: 200 ApplicationStatusDto
            Client-->>Tool: DTO → JSON string
        else Transient failure (5xx / timeout)
            CXA-->>Client: 503
            Client-->>Retry: throw (retryable)
            Retry->>Client: retry (up to 3×, 200ms backoff)
            Client-->>Tool: JSON on eventual success
        else Client error (4xx)
            CXA-->>Client: 404
            Client-->>Tool: typed error JSON (no retry)
        end
    else Circuit OPEN
        CB-->>Tool: short-circuit immediately
        Tool-->>Tool: return graceful degraded JSON
    end
```

---

## 7. Integration Design

### 7.1 MCP Protocol and TLS Handshake Optimisation

`candidate-mcp` uses **stateless streamable HTTP**. This means `langchain-mcp-adapters`
creates a new HTTP session (including a full TLS handshake) for every individual tool
call. A typical `post_apply_assistant` workflow makes 3–5 tool calls in a single
user request (e.g. `getActionableApplications` → `getJobDetails` → `getApplicationStatus` →
`getNextSteps` → `getInterviewFeedback`), resulting in 3–5 consecutive TLS handshakes.

Without mitigation, this adds ~50–150ms of unnecessary overhead per tool call and
saturates the TCP connection pool.

#### Problem — Per-Call TLS Overhead

```mermaid
sequenceDiagram
    participant PAA as post_apply_assistant
    participant MCP as candidate-mcp

    note over PAA,MCP: Without connection reuse — 4 full TLS handshakes

    PAA->>MCP: TCP SYN + TLS ClientHello (tool call 1)
    MCP-->>PAA: TLS ServerHello + cert + Finished
    PAA->>MCP: getActionableApplications → result
    note over PAA,MCP: connection closed

    PAA->>MCP: TCP SYN + TLS ClientHello (tool call 2)
    MCP-->>PAA: TLS ServerHello + cert + Finished
    PAA->>MCP: getJobDetails → result
    note over PAA,MCP: connection closed

    PAA->>MCP: TCP SYN + TLS ClientHello (tool call 3)
    MCP-->>PAA: TLS ServerHello + cert + Finished
    PAA->>MCP: getApplicationStatus → result
    note over PAA,MCP: connection closed
```

#### Solution — httpx Connection Pool with TLS Session Resumption

`langchain-mcp-adapters` uses `httpx` under the hood. Configuring a shared
**persistent httpx connection pool** with TLS session resumption eliminates redundant
handshakes across tool calls within the same agent invocation.

```mermaid
sequenceDiagram
    participant PAA as post_apply_assistant
    participant POOL as httpx Connection Pool (shared across tool calls)
    participant MCP as candidate-mcp

    note over PAA,MCP: One TLS handshake — subsequent calls reuse the connection

    PAA->>POOL: acquire connection
    POOL->>MCP: TCP SYN + TLS ClientHello (first call only)
    MCP-->>POOL: TLS ServerHello + cert + Finished
    POOL->>MCP: getActionableApplications → result
    note over POOL,MCP: connection kept alive (HTTP/1.1 keep-alive or HTTP/2)

    PAA->>POOL: acquire connection (reused)
    POOL->>MCP: getJobDetails → result  (no new handshake)

    PAA->>POOL: acquire connection (reused)
    POOL->>MCP: getApplicationStatus → result  (no new handshake)
```

**Implementation approach:**

A shared `httpx.AsyncClient` instance (not created per-call) is configured in the
`MCPToolRegistry` at startup and passed to the `MultiServerMCPClient` transport.

| Configuration | Value | Reason |
|---|---|---|
| `http2=True` | Enabled | HTTP/2 multiplexes tool calls over a single connection; eliminates TCP overhead entirely for concurrent calls |
| `limits.max_keepalive_connections` | 5 | One per `candidate-mcp` replica; supports load-balanced round-robin |
| `limits.keepalive_expiry` | 30s | Prevents stale connections; matches Kubernetes service mesh idle timeout |
| `verify` | CA bundle path | Validates `candidate-mcp` TLS certificate against internal CA |
| TLS session tickets | Enabled by default in httpx | `candidate-mcp` returns a `Session-Ticket` on first handshake; subsequent reconnects reuse it, skipping full certificate exchange |

**candidate-mcp — keep-alive configuration:**

Spring Boot's embedded Tomcat must be configured to hold connections open long enough
for the agent to reuse them.

| Property | Value | Reason |
|---|---|---|
| `server.tomcat.connection-timeout` | `20s` | How long Tomcat waits for a new request on a kept-alive connection |
| `server.tomcat.keep-alive-timeout` | `15s` | Slightly below the agent's 30s expiry to avoid race conditions |
| `server.tomcat.max-keep-alive-requests` | `100` | Maximum requests on one connection before forcing a new one |

**Result:** a `post_apply_assistant` workflow making 4 tool calls to the same
`candidate-mcp` pod performs **one TLS handshake** (on the first call) and
**three keep-alive reuses** for the remainder.

```mermaid
flowchart LR
    subgraph "Python Process"
        MC["httpx.AsyncClient (shared · HTTP/2) Persistent connection pool"]
    end
    subgraph "candidate-mcp Pod A"
        EP_A["/mcp (keep-alive enabled)"]
    end
    subgraph "candidate-mcp Pod B"
        EP_B["/mcp (keep-alive enabled)"]
    end

    MC -->|"HTTP/2 stream 1 — tool call 1 TLS handshake once per pod connection"| EP_A
    MC -->|"HTTP/2 stream 2 — tool call 2 reuses connection (no new handshake)"| EP_A
    MC -->|"HTTP/2 stream 3 — tool call 3 reuses connection"| EP_A
    MC -->|"different pod — one handshake then reused"| EP_B
```

Any pod handles any call — no sticky sessions required. Connection pool distributes
across all healthy pods; a new handshake occurs only when a connection to a previously
unseen pod is first established.

### 7.2 Downstream Service Contracts

`candidate-mcp` consumes three downstream services in production:

**talent-profile-service** — profile, assessments, preferences

| Tool | Endpoint |
|---|---|
| `getTalentProfile` | `GET /v1/candidates/{id}/profile` |
| `getSkillsGap` | `GET /v1/candidates/{id}/skills-gap?jobId={jobId}` |
| `getAssessmentResults` | `GET /v1/candidates/{id}/assessments` |
| `getAssessmentByType` | `GET /v1/candidates/{id}/assessments?type={type}` |
| `compareToPercentile` | `GET /v1/candidates/{id}/assessments/percentile` |

**cx-applications** — application status and workflow history

| Tool | Endpoint |
|---|---|
| `getApplicationStatus` | `GET /v1/applications/{id}/status` |
| `getActionableApplications` | `GET /v1/applications?talentProfileId={id}` |
| `getCandidateJourney` | `GET /v1/candidates/{id}/journey` |
| `getNextSteps` | `GET /v1/applications/{id}/next-steps` |
| `getStageDuration` | `GET /v1/applications/{id}/stage-duration` |
| `getInterviewFeedback` | `GET /v1/applications/{id}/interviews` |

**job-sync-service** — job requisition details

| Tool | Endpoint |
|---|---|
| `getJobDetails` | `GET /v1/jobs/{id}` — returns title, location, department, job type, required assessment codes, and requisition status |

> `job-sync-service` is an existing service. `candidate-mcp` calls it via a new
> `JobSyncClient` (WebClient + circuit breaker). The v1 primary assistant's existing
> direct HTTP calls to `job-sync-service` are a separate connection and are unaffected.

---

## 8. Security Design

All service-to-service authentication uses **App2App HMAC-SHA256 signature auth**.
The same mechanism applies to both hops:
`careers-ai-service` → `candidate-mcp` and `candidate-mcp` → downstream services.
Each hop uses independently registered app IDs and shared secrets.

### 8.1 App2App Signature Auth — careers-ai-service to candidate-mcp

Trust is established via an HMAC-SHA256 request signature computed by the caller
and validated by the receiver.

#### Signature Header Contract

Each MCP request from `careers-ai-service` carries three additional HTTP headers:

| Header | Content |
|---|---|
| `X-App-Id` | Registered caller identifier (e.g. `careers-ai-service-prod`) |
| `X-Timestamp` | UTC Unix epoch seconds at signing time |
| `X-Signature` | `HMAC-SHA256(shared_secret, X-App-Id + ":" + X-Timestamp + ":" + request_path)` hex-encoded |

#### Signature Flow

```mermaid
sequenceDiagram
    participant Agent as careers-ai-service (SignatureProvider)
    participant MCP as candidate-mcp (SignatureFilter)
    participant SR as ServiceRegistry (in-memory / Redis)

    Agent->>Agent: compute signature (app_id + timestamp + path)
    Agent->>MCP: POST /mcp + X-App-Id, X-Timestamp, X-Signature

    MCP->>SR: lookup(app_id) → secret + ttl_seconds
    SR-->>MCP: shared_secret, ttl=300

    MCP->>MCP: verify: now - X-Timestamp ≤ ttl_seconds
    MCP->>MCP: verify: HMAC-SHA256(secret, payload) == X-Signature

    alt Valid
        MCP-->>Agent: 200 tool response
    else Expired (replay attack window exceeded)
        MCP-->>Agent: 401 SIGNATURE_EXPIRED
    else Invalid signature
        MCP-->>Agent: 401 SIGNATURE_INVALID
    end
```

#### Service Registry — Signature TTL Configuration

`candidate-mcp` maintains a **Service Registry** that maps each registered caller to
its shared secret and optional TTL override. The default TTL is 5 minutes.

| Field | Description |
|---|---|
| `app_id` | Unique caller identifier |
| `shared_secret` | Secret used to verify the HMAC |
| `ttl_seconds` | Signature validity window. Default: `300` (5 min). Can be reduced per client for higher-security environments. |
| `enabled` | If false, all requests from this app ID are rejected without verification |

```mermaid
flowchart LR
    SR["Service Registry ──────────────────── app_id → secret + ttl stored in application.yml or external config (Vault / K8s Secret)"]
    SF["SignatureFilter (Spring OncePerRequestFilter)"]
    REQ["Inbound MCP request"]

    REQ --> SF
    SF -->|"lookup app_id"| SR
    SR -->|"secret + ttl"| SF
    SF -->|"HMAC verify + TTL check"| REQ
```

#### Python — SignatureProvider

`careers-ai-service` wraps the `MultiServerMCPClient` with a `SignatureProvider` that
injects the three signature headers into every outgoing MCP HTTP request. The
provider reads `APP_ID` and `APP_SECRET` from the environment.

```mermaid
flowchart LR
    PAA["post_apply_assistant tool call"]
    SP["SignatureProvider ──────────────────── reads APP_ID, APP_SECRET computes HMAC-SHA256 injects X-* headers"]
    MC["MultiServerMCPClient (httpx transport)"]
    CMCP["candidate-mcp /mcp"]

    PAA --> SP
    SP --> MC
    MC -->|"POST /mcp + signature headers"| CMCP
```

---

### 8.2 App2App Signature Auth — candidate-mcp to Downstream Services

`candidate-mcp` uses the same HMAC-SHA256 signature scheme when calling downstream
services. Each downstream service registers `candidate-mcp` as a trusted `app_id`
in its own Service Registry. A `SignatureProvider` in `candidate-mcp` computes and
injects `X-App-Id`, `X-Timestamp`, and `X-Signature` on every outbound REST call.

```mermaid
flowchart LR
    subgraph "candidate-mcp"
        SP["SignatureProvider computes HMAC-SHA256 injects X-* headers"]
        PT["ProfileTools"]
        AT["ApplicationTools"]
        JT["JobTools"]
        PT & AT & JT --> SP
    end

    TPS["talent-profile-service (validates X-App-Id/Signature)"]
    CXA["cx-applications (validates X-App-Id/Signature)"]
    JSS["job-sync-service (validates X-App-Id/Signature)"]

    SP -->|"REST + App2App Signature"| TPS
    SP -->|"REST + App2App Signature"| CXA
    SP -->|"REST + App2App Signature"| JSS
```

---

### 8.3 Security Principles

| Principle | Implementation |
|---|---|
| **App2App — no shared user context** | The agent-to-MCP hop is machine-to-machine. No user bearer token is forwarded through the agent. |
| **Replay attack prevention** | Signature TTL (default 5 min) prevents reuse of a captured signature. Clock skew tolerance is not added — clocks must be synchronised (NTP). |
| **Per-client TTL control** | High-sensitivity deployments can reduce TTL below 5 min at the service registry level without redeploying the agent. |
| **Least privilege (downstream)** | Each downstream service registers `candidate-mcp` with its own app_id and independent shared secret. Secrets are never shared across services. |
| **No secrets in code** | App secret (`APP_SECRET`) injected via Kubernetes `Secret` → env variable. MCP service registry secrets stored in Vault or K8s Secrets, never in `application.yml`. |
| **MCP endpoint hardened** | `/mcp/**` requires a valid App2App signature. `/actuator/health/**` is public for probe access only. |

---

## 9. Resilience Design

### 9.1 Circuit Breaker — State Machine

One circuit breaker per downstream service, independently tripped. A failure in
`cx-applications` does not affect `talent-profile-service` or `job-sync-service`
calls. Three circuit breakers in total: one per service.

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> Open : failure rate ≥ 50% across 20-call sliding window
    Open --> HalfOpen : after 30 seconds
    HalfOpen --> Closed : 5 probe calls succeed
    HalfOpen --> Open : any probe call fails
```

### 9.2 Retry Configuration

| Parameter | Value | Applies To |
|---|---|---|
| Max attempts | 3 | All downstream services |
| Wait between retries | 200ms | All downstream services |
| Retry on | 5xx, connection timeout | Network / server errors |
| Do not retry | 4xx | Client errors (not found, access denied) |

### 9.3 Timeout Hierarchy

| Layer | Timeout | Purpose |
|---|---|---|
| MCP tool handler total | 10s | LLM tool call budget |
| WebClient response | 5s | Per downstream HTTP call |
| WebClient connect | 2s | TCP connection establishment |

### 9.4 Graceful Degradation

When a circuit is open or all retries are exhausted, every tool handler returns a
structured error JSON rather than throwing an exception. The LLM reads this and
generates a helpful message about the temporary unavailability rather than
hallucinating data or producing an error trace.

---

## 10. Observability Design

Production observability uses a **three-layer stack**:

- **Langfuse**: LLM tracing, cost tracking, prompt management, user feedback
- **Prometheus**: Service metrics, SLOs, alerting
- **OpenObserve**: Application logs, structured logging, dashboards

This section describes the comprehensive observability strategy validated through
implementation implementation and production deployment planning.

---

### 10.1 Three-Layer Observability Stack

```mermaid
flowchart TD
    subgraph "Layer 1: LLM Observability"
        LF["Langfuse ──────────────────── • Trace every LLM call • Track token usage & cost • Session tracking • User feedback collection • Prompt versioning"]
    end

    subgraph "Layer 2: Service Metrics"
        PROM["Prometheus ──────────────────── • Request rates • Latency P50/P95/P99 • Tool call metrics • Circuit breaker state • SLO tracking"]
    end

    subgraph "Layer 3: Application Logs"
        OO["OpenObserve ──────────────────── • Structured logs • Strategic log events • Alert rules • Production dashboards"]
    end

    subgraph "Services"
        PA["careers-ai-service (Python)"]
        MC["candidate-mcp (Java)"]
    end

    PA -->|"LangfuseCallbackHandler"| LF
    PA -->|"/metrics endpoint"| PROM
    MC -->|"Micrometer metrics"| PROM
    PA -->|"structlog JSON"| OO
    MC -->|"logback JSON"| OO
```

---

### 10.2 Langfuse: LLM Tracing & Cost Management

#### A. Enhanced Trace Configuration

**Langfuse callback handler** integrated with v2 API routes provides:

- **Session tracking** via `thread_id` (multi-turn conversation grouping)
- **User segmentation** via `talent_profile_id` (per-candidate metrics)
- **Rich metadata**: agent version, environment, ats_requisition_id context
- **Tags**: `production`, `post_apply_assistant`, `application_specific`

**Implementation**:
```python
from langfuse.langchain import CallbackHandler

langfuse_handler = CallbackHandler(
    session_id=thread_id,       # Multi-turn conversation tracking
    user_id=talent_profile_id,        # Per-candidate cost and performance metrics
    tags=["production", "post_apply_assistant"],
    metadata={
        "agent_version": "v2.0",
        "environment": "production",
        "talent_profile_id": talent_profile_id,
        "ats_requisition_id": ats_requisition_id,
    }
)

config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse_handler]}
final_state = await graph.ainvoke(input_state, config=config)
```

#### B. Cost Tracking Features

Langfuse automatically tracks:
- **Per-request cost** (prompt + completion tokens × model pricing)
- **Session-level cost** (multi-turn conversation total)
- **Per-candidate cost** (grouped by `user_id`)
- **Model usage breakdown** (cost by model type)

**Custom cost calculation** for local/self-hosted LLMs:
```python
def calculate_custom_model_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    CUSTOM_MODEL_PRICING = {
        "openai/gpt-oss-20b": {"prompt": 0.50, "completion": 1.50},  # Per 1M tokens
    }
    pricing = CUSTOM_MODEL_PRICING.get(model, {"prompt": 0, "completion": 0})
    return (prompt_tokens / 1_000_000) * pricing["prompt"] + (completion_tokens / 1_000_000) * pricing["completion"]
```

#### C. User Feedback Integration

**Feedback endpoint** allows candidates to rate agent responses:

```python
@router.post("/api/v2/agent/feedback")
async def submit_feedback(trace_id: str, score: float, comment: str | None = None):
    """
    Args:
        trace_id: Langfuse trace ID from response
        score: -1.0 (thumbs down), 0.0 (neutral), 1.0 (thumbs up)
        comment: Optional feedback text
    """
    client = Langfuse()
    client.score(trace_id=trace_id, name="user_feedback", value=score, comment=comment)
    return {"status": "success"}
```

**Use case**: Frontend displays thumbs up/down buttons, sends feedback to this endpoint

#### D. Prompt Management

**Centralized prompt versioning** in Langfuse UI:
- Store system prompts in Langfuse (version-controlled)
- Fetch at runtime: `prompt = client.get_prompt("post_apply_assistant_system_prompt", version=3)`
- A/B test prompt variations
- Rollback to previous versions on quality regression

#### E. Key Metrics Tracked by Langfuse

| Metric | Description | Alert Threshold |
|---|---|---|
| **P95 latency** | 95th percentile request duration | > 10s for 5 min |
| **Cost per trace** | LLM cost for one user request | > $0.50 (expensive query) |
| **Tool call patterns** | Most frequently used tools | - |
| **Error rate** | Percentage of failed requests | > 5% for 10 min |
| **User feedback score** | Average thumbs up/down | < 0.6 (low satisfaction) |
| **Session duration** | Multi-turn conversation length | - |
| **Token usage trend** | Prompt + completion tokens over time | - |

---

### 10.3 Prometheus: Service Metrics & SLOs

#### A. Python Agent Metrics (careers-ai-service)

**Exposed at**: `http://localhost:8000/metrics`

| Metric | Type | Labels | Description |
|---|---|---|---|
| `agent_requests_total` | Counter | `agent_version`, `agent_used`, `status` | Total agent requests (success/error) |
| `agent_request_duration_seconds` | Histogram | `agent_version`, `agent_used` | Request latency distribution |
| `mcp_tool_calls_total` | Counter | `tool_name`, `status` | MCP tool invocations |
| `mcp_tool_duration_seconds` | Histogram | `tool_name` | Tool call latency |
| `agent_handoff_total` | Counter | `from_agent`, `to_agent` | Agent handoff events |
| `mcp_connection_status` | Gauge | - | MCP connection health (1=up, 0=down) |
| `mcp_tools_loaded` | Gauge | `agent_type` | Number of tools loaded |
| `llm_tokens_total` | Counter | `token_type`, `model` | LLM tokens used (prompt/completion) |
| `llm_cost_usd_total` | Counter | `model` | LLM cost in USD |

**Implementation**:
```python
from prometheus_client import Counter, Histogram, Gauge

agent_requests_total = Counter(
    "agent_requests_total",
    "Total agent requests",
    ["agent_version", "agent_used", "status"]
)

agent_request_duration_seconds = Histogram(
    "agent_request_duration_seconds",
    "Agent request duration",
    ["agent_version", "agent_used"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

# GUARDRAIL METRICS (NEW)
agent_tool_calls_total = Counter(
    "agent_tool_calls_total",
    "Total tool calls by tool name",
    ["tool_name", "talent_profile_id"]
)

tool_call_errors = Counter(
    "agent_tool_call_errors_total",
    "Tool call errors by tool name and error type",
    ["tool_name", "error_type"]
)

agent_iterations_count = Histogram(
    "agent_iterations_count",
    "Number of iterations per request",
    buckets=[1, 3, 5, 10, 15, 20, 25, 30]
)

agent_recursion_limit_hit_total = Counter(
    "agent_recursion_limit_hit_total",
    "Requests that hit recursion limit"
)

# Expose via FastAPI
from prometheus_client import make_asgi_app
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

#### B. Java MCP Server Metrics (candidate-mcp)

**Exposed at**: `http://localhost:8081/actuator/prometheus`

| Metric | Type | Labels | Description |
|---|---|---|---|
| `mcp.tool.calls.total` | Counter | `tool`, `status` | Tool invocations |
| `mcp.tool.duration.seconds` | Timer | `tool` | Tool execution time |
| `mcp.transformations.total` | Counter | `transformer`, `status` | PII transformation calls |
| `mcp.transformation.duration.seconds` | Timer | `transformer` | Transformation time |
| `mcp.downstream.calls.total` | Counter | `service`, `endpoint`, `status` | Downstream REST calls |
| `mcp.downstream.duration.seconds` | Timer | `service`, `endpoint` | Downstream latency |
| `mcp.circuit_breaker.open.total` | Counter | `service` | Circuit breaker opens |
| `resilience4j.circuitbreaker.state` | Gauge | `name` | Circuit state (0=closed, 1=open) |

**Spring Boot Configuration**:
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
      environment: ${ENVIRONMENT:production}
```

#### C. Prometheus Alert Rules

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
        annotations:
          summary: "Agent error rate > 5% for 5 minutes"

      # Slow responses (P95 > 10s)
      - alert: SlowAgentResponses
        expr: |
          histogram_quantile(0.95,
            rate(agent_request_duration_seconds_bucket[5m])
          ) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 latency exceeds 10 seconds"

      # MCP connection down
      - alert: McpConnectionDown
        expr: mcp_connection_status == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "MCP server connection lost"

      # High LLM cost
      - alert: HighLlmCost
        expr: increase(llm_cost_usd_total[1h]) > 100
        labels:
          severity: warning
        annotations:
          summary: "LLM cost exceeds $100 in 1 hour"

      # Circuit breaker open
      - alert: CircuitBreakerOpen
        expr: resilience4j_circuitbreaker_state{state="open"} == 1
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Circuit breaker opened for downstream service"

      # High downstream latency
      - alert: HighDownstreamLatency
        expr: |
          histogram_quantile(0.95,
            rate(mcp_downstream_duration_seconds_bucket[5m])
          ) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 downstream latency > 5s"

      # GUARDRAIL ALERTS (NEW)

      # Excessive tool calls
      - alert: AgentExcessiveToolCalls
        expr: rate(agent_tool_calls_total[5m]) > 50
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "Agent making excessive tool calls"
          description: "Tool call rate {{ $value }} calls/sec exceeds threshold"

      # Recursion limit hits
      - alert: AgentRecursionLimitHit
        expr: increase(agent_recursion_limit_hit_total[5m]) > 5
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Multiple requests hitting recursion limit"
          description: "{{ $value }} requests hit recursion limit in last 5 minutes"

      # Tool call errors
      - alert: AgentToolCallErrors
        expr: rate(agent_tool_call_errors_total[5m]) > 0.1
        for: 2m
        labels:
          severity: warning
        annotations:
          summary: "High tool call error rate"
          description: "Error rate {{ $value }} errors/sec for tool {{ $labels.tool_name }}"

      # High iteration count
      - alert: AgentHighIterationCount
        expr: |
          histogram_quantile(0.95,
            rate(agent_iterations_count_bucket[5m])
          ) > 20
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "P95 iteration count approaching recursion limit"
          description: "Agent averaging {{ $value }} iterations per request"
```

---

### 10.4 OpenObserve: Application Logs & Alerting

#### A. Strategic Logging Points — Python Agent

| Event | Level | Fields | Alert Trigger |
|---|---|---|---|
| `agent_invoke_start` | INFO | `thread_id`, `correlation_id`, `talent_profile_id`, `message` | - |
| `handoff_to_post_apply_assistant` | INFO | `reason`, `talent_profile_id`, `ats_requisition_id` | - |
| `mcp_tool_call_start` | DEBUG | `tool_name`, `args`, `correlation_id` | - |
| `mcp_tool_call_complete` | INFO | `tool_name`, `duration_ms`, `status` | If `duration_ms > 5000` |
| `mcp_tool_call_error` | ERROR | `tool_name`, `error`, `correlation_id` | Immediate |
| `agent_invoke_complete` | INFO | `agent_used`, `tool_calls`, `duration_ms` | If `duration_ms > 30000` |
| `agent_invoke_error` | ERROR | `error`, `error_type`, `stack_trace` | Immediate |
| `mcp_connection_failed` | CRITICAL | `error`, `mcp_url`, `retry_attempt` | Immediate |
| `llm_call_complete` | INFO | `model`, `prompt_tokens`, `cost_usd`, `duration_ms` | If `cost_usd > 1.0` |
| `user_feedback_received` | INFO | `trace_id`, `score`, `has_comment` | - |
| `circuit_breaker_opened` | CRITICAL | `service`, `failure_rate` | Immediate |

**Implementation**:
```python
import structlog

logger = structlog.get_logger(__name__)

# Example: Log tool call with timing
with tool_call_span("getTalentProfile", {"talentProfileId": "C001"}):
    result = await tool.ainvoke(args)
    logger.info(
        "mcp_tool_call_complete",
        tool_name="getTalentProfile",
        duration_ms=duration_ms,
        status="success"
    )
```

#### B. Strategic Logging Points — Java MCP Server

| Event | Level | Fields | Alert Trigger |
|---|---|---|---|
| `tool_called` | INFO | `tool`, `talent_profile_id`, `trace_id` | - |
| `tool_completed` | INFO | `tool`, `duration_ms`, `result_size_bytes` | If `duration_ms > 5000` |
| `tool_error` | ERROR | `tool`, `error`, `trace_id` | Immediate |
| `transformation_complete` | INFO | `transformer`, `duration_ms`, `fields_stripped` | - |
| `pii_violation_detected` | CRITICAL | `transformer`, `field`, `value_hash` | **Immediate + page on-call** |
| `downstream_call_complete` | INFO | `service`, `endpoint`, `status_code`, `duration_ms` | If `status_code >= 500` |
| `downstream_call_error` | ERROR | `service`, `endpoint`, `error`, `retry_attempt` | If 3+ failures in 5 min |
| `circuit_breaker_opened` | CRITICAL | `service`, `failure_rate`, `call_count` | Immediate |
| `sla_breach_detected` | WARN | `ats_requisition_id`, `stage`, `days_in_stage`, `threshold` | If count > 10 in 1 hour |
| `mcp_request_received` | INFO | `x_correlation_id`, `x_talent_profile_id`, `method` | - |
| `mcp_response_sent` | INFO | `x_correlation_id`, `status`, `duration_ms` | If `duration_ms > 10000` |

**Implementation**:
```java
import org.slf4j.Logger;
import org.slf4j.MDC;

MDC.put("tool", toolName);
MDC.put("talent_profile_id", talentProfileId);
MDC.put("trace_id", traceId);
log.info("tool_called args_hash={}", hashArgs(args));
MDC.clear();
```

#### C. Production Dashboards

**Dashboard 1: Agent Performance Overview**

Panels:
1. **Request Rate** — Requests per minute by agent type
2. **P50/P95/P99 Latency** — Latency distribution over time
3. **Error Rate** — Percentage of failed requests (gauge)
4. **Top Tools Used** — Bar chart of most frequently called tools
5. **LLM Cost** — Cumulative cost over time
6. **Tool Call Heatmap** — Usage patterns by hour of day

**Dashboard 2: MCP Server Health**

Panels:
1. **Tool Success Rate** — Success percentage per tool (gauge grid)
2. **Downstream Service Latency** — Average latency by service
3. **Circuit Breaker Status** — Open/closed status per service
4. **Transformation Performance** — Average duration by transformer
5. **PII Violations** — Counter (should be 0 always)

**Dashboard 3: User Experience & SLOs**

Panels:
1. **SLO Compliance** — % of requests < 10s (target: 95%)
2. **User Feedback Trends** — Average feedback score over time
3. **SLA Breaches** — Count of applications exceeding stage thresholds
4. **Session Duration** — Distribution of multi-turn conversation lengths
5. **Multi-Turn Conversations** — % of sessions with > 1 turn

#### D. OpenObserve Alert Rules

```json
{
  "alerts": [
    {
      "name": "critical_agent_error_rate",
      "query": "count(agent_invoke_error) / count(agent_invoke_start) * 100 > 10",
      "duration": "5m",
      "severity": "critical",
      "notification": ["slack_oncall", "pagerduty"]
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
      "notification": ["slack_oncall"]
    },
    {
      "name": "excessive_sla_breaches",
      "query": "count(sla_breach_detected) > 50",
      "duration": "1h",
      "severity": "warning",
      "notification": ["slack_recruiting"]
    }
  ]
}
```

---

### 10.5 Distributed Trace Propagation

```mermaid
flowchart LR
    CL["Client (trace ID generated)"]
    PY["Python Agent (FastAPI + OTel)"]
    MC["MCP HTTP call (httpx instrumented)"]
    JV["candidate-mcp (Micrometer + OTel)"]
    DS["Downstream Service"]
    COLL[("OTLP Collector → Jaeger / Tempo")]

    CL -->|"traceparent"| PY
    PY -->|"traceparent injected by httpx"| MC
    MC --> JV
    JV -->|"traceparent injected by WebClient"| DS
    PY -.->|"spans"| COLL
    JV -.->|"spans"| COLL
```

A `correlation_id` is generated at the API layer, carried in `AgentState`, and
included in every structured log record throughout the Python process. The W3C
`traceparent` header carries the trace across service boundaries into the Java layer.

---

### 10.6 Implementation Roadmap

**Phase 1: Foundation (Week 1)**
- ✅ Basic Langfuse integration (already done)
- 🔲 Enhanced Langfuse with session tracking and metadata
- 🔲 Prometheus metrics endpoints (Python + Java)
- 🔲 Basic structured logging (correlation IDs, candidate IDs)

**Phase 2: Comprehensive Instrumentation (Week 2)**
- 🔲 All strategic log events implemented
- 🔲 Prometheus alert rules configured
- 🔲 OpenObserve dashboards created
- 🔲 Tool call metrics tracking

**Phase 3: Advanced Features (Week 3)**
- 🔲 Langfuse prompt management integration
- 🔲 Dataset creation from production traces
- 🔲 User feedback collection endpoint
- 🔲 Cost tracking and optimization

**Phase 4: Production Hardening (Week 4)**
- 🔲 Alert rule tuning based on real traffic
- 🔲 Dashboard refinement
- 🔲 SLO definition and tracking
- 🔲 On-call runbook creation

---

## 11. Caching Design

The production `careers-ai-service` service already operates a Redis cluster shared
across all worker processes and pods. The v2 primary assistant flow uses this same
Redis instance for four distinct caching concerns, each with its own key namespace
and TTL policy.

```mermaid
flowchart LR
    subgraph "careers-ai-service process (8 workers × N pods)"
        W1["Worker 1"]
        W2["Worker 2"]
        WN["Worker N"]
    end

    subgraph "Shared Redis Cluster"
        NS1["mcp:schema:* Static resource schemas"]
        NS2["langgraph:checkpoint:* Thread conversation state"]
        NS3["agent:tool:* Within-session tool cache"]
    end

    subgraph "candidate-mcp"
        NS4["cmcp:tool:* Tool response cache"]
        SR["Static Resources (source of truth)"]
    end

    W1 & W2 & WN <-->|"read / write"| NS1
    W1 & W2 & WN <-->|"read / write"| NS2
    W1 & W2 & WN <-->|"read / write"| NS3
    W1 & W2 & WN -->|"MCP tool call (if agent:tool miss)"| NS4
    SR -.->|"fetched once then cached in mcp:schema"| W1
```

---

### 11.1 MCP Static Resource Schema Cache — careers-ai-service side

**Problem:** `candidate-mcp` exposes 4–5 static JSON Schema resources
(`ats://schema/*`). The Python agent fetches these during `init_registry()` at
startup and embeds them in the LLM system prompt. With **8 Uvicorn worker
processes per pod** and multiple pods, each worker starts independently and calls
`init_registry()` — resulting in up to `8 × N_pods` redundant fetches of the same
immutable schemas on every deployment.

**Solution — distributed lock + Redis schema cache:**

```mermaid
sequenceDiagram
    participant W1 as Worker 1 (first to start)
    participant W2 as Worker 2 (concurrent start)
    participant Redis as Redis
    participant CMCP as candidate-mcp

    par Worker 1 startup
        W1->>Redis: GET mcp:schema:ats://schema/candidate
        Redis-->>W1: (nil — cache miss)
        W1->>Redis: SET mcp:lock:schema_init EX 30 NX
        Redis-->>W1: OK (lock acquired)
        W1->>CMCP: fetch all static resources
        CMCP-->>W1: schema blobs (4 keys)
        W1->>Redis: SET mcp:schema:* EX 86400 (24h)
        W1->>Redis: DEL mcp:lock:schema_init
    and Worker 2 startup (concurrent)
        W2->>Redis: GET mcp:schema:ats://schema/candidate
        Redis-->>W2: (nil — not yet populated)
        W2->>Redis: SET mcp:lock:schema_init EX 30 NX
        Redis-->>W2: (nil — lock held by W1)
        note over W2: poll Redis every 500ms (max 15s)
        W2->>Redis: GET mcp:schema:ats://schema/candidate
        Redis-->>W2: schema blob (populated by W1)
        note over W2: all schemas present — skip fetch
    end

    note over W1,W2: both workers build system prompt<br/>from cached schemas — zero extra MCP calls
```

**Key design rules:**

| Rule | Detail |
|---|---|
| Lock TTL | 30 seconds — prevents deadlock if the locking worker crashes mid-fetch |
| Schema cache TTL | 24 hours — schemas change only on `candidate-mcp` redeploy |
| Invalidation on redeploy | `candidate-mcp` writes a new `mcp:schema:version` key on startup. Workers detect the version change on their next startup and force a cache refresh. |
| Fallback | If Redis is unavailable at startup, each worker falls back to fetching directly from `candidate-mcp` (degraded but functional) |
| Key namespace | `mcp:schema:{uri}` — e.g. `mcp:schema:ats://schema/candidate` |

**Result:** regardless of how many workers or pods start simultaneously, `candidate-mcp`
receives at most **one schema fetch per deployment** rather than one per worker.

---

### 11.2 LangGraph Thread State — Conversation Checkpointer

**Problem:** The current v1 and v2 graphs use `MemorySaver` — an in-process
Python dictionary. With 8 workers per pod and multiple pods, any turn of a
multi-turn conversation may be served by a **different worker or pod** than the
previous turn. `MemorySaver` is invisible across process boundaries. The
conversation history is lost on every cross-worker or cross-pod request.

**Solution — Redis-backed LangGraph checkpointer:**

Replace `MemorySaver` with an `AsyncRedisSaver` that stores the full LangGraph
checkpoint (conversation message history + agent state) in Redis, keyed by
`thread_id`. All workers and all pods read and write the same checkpoint store.

```mermaid
sequenceDiagram
    participant C as Client (thread_id: T1)
    participant W1 as Worker 1 (Pod A)
    participant W3 as Worker 3 (Pod B)
    participant Redis as Redis

    C->>W1: Turn 1 — "What's my application status?"
    W1->>Redis: SAVE checkpoint {T1, messages: [turn1]}
    W1-->>C: response

    C->>W3: Turn 2 — "What do I need to prepare?"
    W3->>Redis: LOAD checkpoint {T1}
    Redis-->>W3: {messages: [turn1]}
    note over W3: full context available<br/>even though different worker + pod
    W3->>Redis: SAVE checkpoint {T1, messages: [turn1, turn2]}
    W3-->>C: response (contextually aware of turn 1)
```

| Parameter | Value | Reason |
|---|---|---|
| Key namespace | `langgraph:v2:checkpoint:{thread_id}` | Separate from v1 (`langgraph:v1:*`) — no cross-version state pollution |
| TTL | 2 hours from last write | Matches expected candidate session length; prevents stale checkpoints accumulating |
| Serialisation | JSON (LangGraph native) | Human-readable, inspectable in Redis CLI for debugging |
| v1 graph checkpointer | Also migrated to Redis (same cluster, `langgraph:v1:*` namespace) | Consistent across both graphs; eliminates same problem in v1 |

---

### 11.3 Within-Session Tool Response Cache — careers-ai-service side

**Problem:** Within a single multi-turn conversation, the candidate may ask several
related questions. Each question may trigger the same MCP tool call with the same
arguments (e.g. `getTalentProfile` called on turn 1, turn 3, and turn 5 of the
same session). Each call incurs an MCP HTTP round-trip.

**Solution — short-TTL per-session tool response cache:**

After a tool call completes, store the result in Redis keyed by
`{tool_name}:{talent_profile_id}:{args_hash}` with a short TTL. Subsequent tool calls
with the same arguments within the TTL window return the cached result without
hitting `candidate-mcp`.

```mermaid
flowchart TD
    PAA["post_apply_assistant tool call: getTalentProfile(C001)"]
    AC{{"Redis agent:tool:getTalentProfile:C001 (session-scoped · short TTL)"}}
    CMCP["candidate-mcp (HTTP + TLS + downstream call)"]

    PAA -->|"lookup"| AC
    AC -->|"HIT (< 5 min old)"| PAA
    AC -->|"MISS"| CMCP
    CMCP -->|"result"| AC
    AC --> PAA
```

| Tool | Agent-side cache TTL | Notes |
|---|---|---|
| `getTalentProfile` | 5 min | Profile stable within a session |
| `getSkillsGap` | 5 min | Keyed by talentProfileId + jobId |
| `getJobDetails` | 10 min | Job data changes rarely; same job enriched across multiple applications |
| `getAssessmentResults` | 5 min | Assessment results don't change mid-session |
| `getAssessmentByType` | 5 min | Subset of above |
| `compareToPercentile` | 10 min | Pool percentiles update daily |
| `getApplicationStatus` | Not cached | Live status — must always be fresh |
| `getActionableApplications` | Not cached | New applications could arrive |
| `getCandidateJourney` | Not cached | Stage transitions are live |
| `getNextSteps` | Not cached | Stage-dependent, must reflect current status |
| `getStageDuration` | Not cached | Increments daily |
| `getInterviewFeedback` | Not cached | Updated post-interview |

Key namespace: `agent:tool:{tool_name}:{talent_profile_id}:{args_hash}` where `args_hash`
is a SHA-256 of the serialised tool arguments. TTL resets on every read (sliding).

---

### 11.4 Tool Response Cache — candidate-mcp side

`candidate-mcp` maintains its own Redis cache for calls to downstream services. This
is **separate from and independent of** the agent-side cache in 12.3. The two caches
serve different purposes: the candidate-mcp cache reduces downstream load across all
callers; the agent-side cache reduces MCP round-trips within a session.

```mermaid
flowchart LR
    subgraph "careers-ai-service process"
        PAA_CACHE["agent:tool:* (12.3) Prevents repeat MCP HTTP calls within one session"]
    end
    subgraph "candidate-mcp"
        MCP_CACHE["cmcp:tool:* (12.4) Prevents repeat downstream REST calls across all callers"]
    end
    subgraph "Downstream"
        TPS["talent-profile-service"]
        CXA["cx-applications"]
        JSS["job-sync-service"]
    end

    PAA_CACHE -->|"miss → MCP call"| MCP_CACHE
    MCP_CACHE -->|"miss → REST"| TPS & CXA & JSS
```

| Tool | candidate-mcp cache TTL | Invalidation |
|---|---|---|
| `getTalentProfile` | 5 min | Profile update event (event-driven invalidation) |
| `getAssessmentResults` | 5 min | Assessment completion event |
| `getAssessmentByType` | 5 min | TTL only |
| `compareToPercentile` | 10 min | TTL only (pool updates daily) |
| `getSkillsGap` | 5 min | Profile update event |
| `getJobDetails` | 15 min | Job update event |
| `getApplicationStatus` | Not cached | Live status |
| `getActionableApplications` | Not cached | New applications may arrive |
| `getCandidateJourney` | Not cached | Stage transitions are live |
| `getNextSteps` | Not cached | Stage-dependent |
| `getStageDuration` | Not cached | Updates daily |
| `getInterviewFeedback` | Not cached | Updated post-interview |

---

### 11.5 Cache Hierarchy Summary

| Cache | Owner | Redis namespace | What it prevents |
|---|---|---|---|
| Static schema cache | careers-ai-service | `mcp:schema:*` | 8N redundant schema fetches at startup |
| Thread state (checkpointer) | careers-ai-service | `langgraph:v2:checkpoint:*` | Lost conversation context across workers and pods |
| Session tool cache | careers-ai-service | `agent:tool:*` | Repeat MCP HTTP calls within one conversation turn sequence |
| Tool response cache | candidate-mcp | `cmcp:tool:*` | Repeat downstream REST calls across all callers |

---

## 12. Error Handling

### 12.1 Error Envelope Contract

Every MCP tool returns a JSON string. On failure, a typed error envelope is returned
so the LLM can interpret it and generate a helpful user-facing message.

| Field | Description |
|---|---|
| `error` | Machine-readable error code |
| `message` | Human-readable description safe to surface |
| `retriable` | Whether the caller should suggest trying again |

### 12.2 Error Classification

| Scenario | Error Code | HTTP Status | Retriable | Example Response |
|---|---|---|---|---|
| Resource not found (404) | `{resource}_not_found` | 404 | No | `{"error": "job_not_found", "message": "Job J001 not found"}` |
| Access denied (403) | `access_denied` | 403 | No | `{"error": "access_denied", "message": "Access denied"}` |
| Service timeout | `service_timeout` | 504 | Yes | `{"error": "service_timeout", "message": "Request timed out"}` |
| Circuit breaker open | `service_unavailable` | 503 | Yes | `{"error": "service_unavailable", "message": "Service temporarily unavailable"}` |
| Unexpected error | `internal_error` | 500 | No | `{"error": "internal_error", "message": "An unexpected error occurred"}` |
| **Invalid ID format** (NEW) | `invalid_id_format` | 400 | No | `{"error": "invalid_id_format", "message": "Invalid job_id format: 'JSeniorSRE'. Expected format: J### (e.g., J001). Use exact IDs from getActionableApplications()."}` |
| **Recursion limit exceeded** (NEW) | `recursion_limit_exceeded` | 504 | No | `{"error": "recursion_limit_exceeded", "message": "Request exceeded maximum iteration limit (25). Please simplify your query."}` |
| **Request timeout** (NEW) | `request_timeout` | 504 | No | `{"error": "request_timeout", "message": "Agent execution timeout. Please try a simpler query or contact support."}` |

**Guardrail Error Response Examples**:

```python
# Invalid ID format error (400 Bad Request)
{
    "error": "invalid_id_format",
    "message": "Invalid job_id format: 'JSeniorSRE'. Expected format: J### (e.g., J001, J002). Do not guess job IDs. Use exact IDs from getActionableApplications() results.",
    "retriable": false,
    "details": {
        "provided_id": "JSeniorSRE",
        "expected_pattern": "^J\\d{3}$",
        "valid_examples": ["J001", "J002", "J003"]
    }
}

# Recursion limit exceeded (504 Gateway Timeout)
{
    "error": "recursion_limit_exceeded",
    "message": "Request exceeded maximum iteration limit (25). Please simplify your query.",
    "retriable": false,
    "details": {
        "iterations": 25,
        "limit": 25,
        "tool_calls": 12
    }
}

# Request timeout (504 Gateway Timeout)
{
    "error": "request_timeout",
    "message": "Agent execution timeout after 60 seconds. Please try a simpler query or contact support.",
    "retriable": false,
    "details": {
        "timeout_seconds": 60,
        "elapsed_seconds": 60.2
    }
}
```

Stack traces, internal URLs, and raw downstream response bodies are never included
in the error envelope.

---

## 13. Testing Strategy

### 13.1 Test Layers

```mermaid
flowchart TB
    E2E["End-to-End Tests ────────────────────── Python pytest · live stack Full conversation scenarios"]
    INT_PY["Python Integration Tests ────────────────────── ASGI client + real candidate-mcp Lifespan-managed fixture"]
    INT_JAVA["Java Integration Tests ────────────────────── Spring Boot Test + WireMock Downstream services stubbed"]
    CONTRACT["Contract Tests  Pact ────────────────────── candidate-mcp as consumer cx-apps and talent-profile as providers Published to Pact Broker"]
    UNIT["Unit Tests ────────────────────── Tool handlers: JSON shape Error paths: 4xx · 5xx · circuit open Token provider: refresh boundary"]

    E2E --> INT_PY
    INT_PY --> INT_JAVA
    INT_JAVA --> CONTRACT
    CONTRACT --> UNIT
```

### 13.2 Key Scenarios by Layer

**Unit (Java — candidate-mcp tool handlers)**
- Nominal: correct JSON shape matching `careers-data-schema` DTO fields.
- 404 from downstream: typed `not_found` envelope returned, no exception propagated.
- Circuit open: graceful degraded envelope returned without touching the downstream client.
- Retry: client retries on 503, succeeds on the third attempt.

**Integration (Java — Spring Boot + WireMock)**
- Full tool call through WebClient to a WireMocked downstream service.
- Circuit breaker trips after 20 consecutive failures.
- App2App signature headers are computed and injected into the downstream request header.
- Schema resources are served at startup and contain the expected JSON Schema fields.

**Contract (Pact)**
- `candidate-mcp` publishes consumer contracts for each endpoint it calls on `talent-profile-service` and `cx-applications`.
- Downstream teams run provider verification in their own CI pipeline.
- Breaking API changes are caught before any deployment, not at runtime.

**Integration (Python — pytest)**
- Handoff from primary to `post_apply_assistant` fires for recognised intent patterns.
- `post_apply_assistant` reaches END with a non-empty response.
- Schema resources are loaded and embedded in the system prompt during lifespan startup.

**v2 Scenario Test Runner (`tests/test_v2_scenarios.py`)**

A standalone script that exercises 14 end-to-end scenarios against a live stack and
reports per-scenario pass/fail with tool call names, agent used, response preview,
and total run time. Soft assertions check:
- `agent_used` equals `"post_apply_assistant"` for all domain queries
- Expected tools appear in `tool_calls`
- Key domain keywords appear in the response text

Scenarios covered:

| # | Group | Candidate / Application | Scenario |
|---|---|---|---|
| 1 | Profile | C002 | Profile — no ats_requisition_id → `getTalentProfile` |
| 2 | Profile | C001 / J002 | Skills gap vs unapplied role → `getSkillsGap` |
| 3 | Application Status | C001 / A001 | FINAL_INTERVIEW status → `getApplicationStatus` |
| 4 | Application Status | C004 / A004 | OFFER_EXTENDED — offer surfaced |
| 5 | Application Status | C001 / A006 | REJECTED — constructive tone |
| 6 | All Applications | C001 | Full history without ats_requisition_id → `getActionableApplications` |
| 7 | All Applications | C006 | Journey narrative without ats_requisition_id |
| 8 | Assessments | C004 / A004 | All 3 assessments (97–98th percentile) → `getAssessmentResults` |
| 9 | Assessments | C002 / A002 | Percentile comparison (94th) → `compareToPercentile` |
| 10 | Next Steps | C002 / A002 | PHONE_INTERVIEW prep → `getNextSteps` |
| 11 | Next Steps | C006 / A007 | Stage duration / SLA check → `getStageDuration` |
| 12 | Streaming | C003 / A003 | SSE stream — status + next steps (SCREENING stage) |
| 13 | Edge Cases | C005 / A005 | HIRED candidate — journey summary → `getCandidateJourney` |
| 14 | Edge Cases | C001 / A001 | Interview feedback (3 rounds + recruiter notes) → `getInterviewFeedback` |

**Guardrail-Specific Tests** (NEW):

```python
# File: careers-ai-service/tests/test_guardrails.py
import pytest
from fastapi.testclient import TestClient

def test_recursion_limit_prevents_infinite_loop(client: TestClient):
    """Test that recursion limit stops infinite loops"""
    response = client.post("/api/v2/agent/invoke", json={
        "thread_id": "test-recursion",
        "talent_profile_id": "C001",
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
    # Simulate agent trying to call getJobDetails with invalid ID
    with pytest.raises(ValueError, match="Invalid job_id format"):
        job_tools.getJobDetails("JSeniorSRE")  # Should fail validation

    with pytest.raises(ValueError, match="Invalid job_id format"):
        job_tools.getJobDetails("job-001")  # Should fail validation

    # Valid ID should pass
    result = job_tools.getJobDetails("J001")
    assert result is not None

def test_request_timeout_enforced(client: TestClient):
    """Test that requests timeout after 60 seconds"""
    import time
    start = time.time()

    response = client.post("/api/v2/agent/invoke", json={
        "thread_id": "test-timeout",
        "talent_profile_id": "C001",
        "message": "Some query that might loop"
    })

    elapsed = time.time() - start

    # Should timeout within 65 seconds (60s limit + 5s grace)
    assert elapsed < 65
    assert response.status_code in [200, 504]

def test_tool_call_limit_enforced(client: TestClient):
    """Test that tool call limit prevents excessive calls"""
    response = client.post("/api/v2/agent/invoke", json={
        "thread_id": "test-tool-limit",
        "talent_profile_id": "C001",
        "message": "Show me detailed information about every single field"
    })

    assert response.status_code == 200
    data = response.json()
    # Should not exceed tool call limit
    assert data.get("tool_calls_made", 0) <= 10

def test_id_validation_error_message_helpful(client: TestClient):
    """Test that ID validation errors provide helpful guidance"""
    try:
        job_tools.getJobDetails("JSeniorSRE")
        pytest.fail("Should have raised error")
    except ValueError as e:
        error_msg = str(e)
        # Error message should include:
        assert "Invalid job_id format" in error_msg
        assert "J###" in error_msg  # Expected format
        assert "J001" in error_msg  # Valid example
        assert "getActionableApplications" in error_msg  # Correct approach
```

**Test Coverage Requirements**:

| Guardrail | Test Scenarios | Acceptance Criteria |
|---|---|---|
| **Recursion limit** | Infinite loop query, nested tool calls | ✅ Stops at 25 iterations, returns 504 or helpful message |
| **Request timeout** | Long-running query, slow downstream | ✅ Stops at 60 seconds, returns 504 with user-friendly message |
| **Tool call limit** | Overly broad query | ✅ Stops at 10 tool calls, agent asks user to rephrase |
| **ID validation** | Hallucinated IDs (JSeniorSRE, job-001, A1) | ✅ Rejects with 400, error message teaches correct format |
| **Convergence patterns** | Sequential tool calls | ✅ Agent stops after sufficient data collected |

**End-to-End**
- Candidate asks for application status → `agent_used: post_apply_assistant`, response references atsRequisitionId.
- Candidate asks for skills gap against a role → `getTalentProfile` and `getSkillsGap` both called.
- `cx-applications` unavailable → user receives a degraded but helpful response.
- Existing job search query → still routed to existing job search assistant, untouched.

---

## 14. Deployment

### 14.1 Service Topology

```mermaid
flowchart TD
    subgraph "Kubernetes Cluster"
        subgraph "Agent Namespace"
            AGT["careers-agent Python · Uvicorn replicas: 2"]
        end

        subgraph "MCP Namespace"
            CMCP["candidate-mcp Java · Spring AI replicas: 2"]
        end

        subgraph "Infrastructure"
            REDIS[("Redis")]
        end

        INGRESS["Ingress / API Gateway"]
    end

    subgraph "Downstream"
        CXA["cx-applications"]
        TPS["talent-profile-service"]
    end

    INGRESS --> AGT
    AGT -->|"MCP"| CMCP
    CMCP --> REDIS
    CMCP --> CXA
    CMCP --> TPS

```

### 14.2 Health Checks

| Service | Liveness Probe | Readiness Probe |
|---|---|---|
| careers-agent (Python) | `GET /health` → 200 | `GET /health` → `mcp_connected: true` |
| candidate-mcp (Java) | `GET /actuator/health/liveness` | `GET /actuator/health/readiness` |

The readiness probe on `candidate-mcp` returns unhealthy if any circuit breaker is in
the `OPEN` state, removing the pod from the load balancer until the downstream service
recovers.

### 14.3 Configuration Injection

| Config Type | Mechanism |
|---|---|
| Service URLs | Kubernetes `ConfigMap` → environment variables |
| App2App shared secrets | Kubernetes `Secret` → environment variables (one per service pair) |
| Redis connection | Kubernetes `Secret` → Spring config |
| candidate-mcp URL (Python) | Kubernetes `ConfigMap` → `.env` |

---

## 15. Design Decisions

### DD-01: Three-Layer Transformation — Separation of PII Safety, Context Relevance, and Presentation

**Decision:** Data is transformed in three discrete, independently owned layers:
Layer 1 (candidate-mcp — PII stripping and agent-neutral projection), Layer 2
(post_apply_assistant — query-specific context filtering for the LLM), Layer 3
(post_apply_assistant — candidate-facing response formatting).

**Alternatives considered:**
- Single transformation in candidate-mcp, fully assistant-specific → rejected:
  `candidate-mcp` would need to know about each assistant's specific output
  requirements. Adding a new assistant would require changes to the MCP server.
- Single transformation in the Python agent → rejected: PII would flow from downstream
  services through the MCP transport into the Python process. Any logging or tracing
  in the agent would risk capturing PII.
- No explicit formatting — rely entirely on LLM → rejected: LLM tone and structure
  are non-deterministic. Candidate-facing communication requires consistent, predictable
  language around sensitive events like rejection and offer.

**Consequence:** Each layer has a clear owner and change boundary. A change to PII
policy requires only a `candidate-mcp` change. A change to how the LLM is prompted
for context filtering requires only a Python system prompt change. A change to
candidate-facing language requires only the response template. None of these cross
the layer boundary.

---

### DD-02: v2 Route as Isolation Strategy

**Decision:** A new `/api/v2/agent/` route backed by a new LangGraph graph is
introduced in the same `careers-ai-service` process. The existing v1 graph and
`/api/v1/agent/` routes are untouched.

**Alternatives considered:**
- Inject `post_apply_assistant` into the existing v1 graph → rejected: risks
  destabilising the live job search assistant; changes the routing logic of a
  production graph that is currently working.
- Deploy a separate microservice for post-apply → rejected: disproportionate
  operational overhead for a new sub-assistant.

**Consequence:** v1 and v2 graphs coexist in the same process, sharing only the
MCP tool registry and settings. v2 can be iterated independently. Future
consolidation replaces v1 with v2 once all sub-assistants are stable.

---

### DD-03: App2App HMAC-SHA256 Signature for All Service-to-Service Calls

**Decision:** All internal service-to-service authentication uses HMAC-SHA256
signature — both `careers-ai-service` → `candidate-mcp` and `candidate-mcp` →
downstream services. No OAuth2 server is involved at any hop.

**Alternatives considered:**
- Mutual TLS (mTLS) → certificate lifecycle complexity for internal service hops.
- OAuth2 client credentials (JWT bearer) → requires an OAuth2 server; adds a
  network dependency on the hot path for every service call.
- No authentication → rejected immediately; all endpoints expose live candidate data.

**Consequence:** Authentication is entirely self-contained. No external auth server
dependency at any hop. All secrets managed via K8s Secrets / Vault. Rotation
requires coordinated redeployment of the affected service pair (or live Vault reload).

---

### DD-04: Reuse candidate-mcp Rather Than Creating a New MCP Server

**Decision:** `post_apply_assistant` connects to the existing `candidate-mcp` server,
which is evolved to call real downstream services. A new separate MCP server is not
created.

**Alternatives considered:**
- New dedicated MCP server for post-apply domain → rejected: duplicates the MCP
  infrastructure, splits the schema resource mechanism, increases operational overhead.

**Consequence:** All candidate domain tooling lives in one MCP server. Any extension
to the candidate domain (new tools, new schema resources) happens in one place.

---

### DD-05: MCP Static Resources as Schema Carrier for careers-data-schema

**Decision:** `candidate-mcp` takes `careers-data-schema` as a compile-time dependency,
serialises the Java models to JSON Schema, and exposes them as MCP static resources.
The Python agent embeds these in the LLM system prompt at startup.

**Alternatives considered:**
- Maintain parallel Python Pydantic models → rejected: dual maintenance, silent drift risk.
- OpenAPI spec → Python code generation → rejected: extra pipeline, still a separate artefact to synchronise.
- No schema context for LLM → rejected: LLM hallucinates field names; tool call accuracy degrades.

**Consequence:** A `careers-data-schema` breaking change requires rebuilding and
redeploying `candidate-mcp`. This is an intentional and auditable deployment gate.

---

### DD-06: Stateless MCP over Stateful Sessions

**Decision:** `candidate-mcp` uses `STATELESS` protocol mode. Each tool call is an
independent HTTP request.

**Alternatives considered:**
- Stateful SSE sessions → requires session affinity in Kubernetes; no benefit for
  this use case since all domain calls are stateless by nature.

**Consequence:** Horizontal scaling is trivial. Each tool call has a small
session-init overhead but latency is dominated by the downstream service call itself.

---

### DD-07: Shared httpx Connection Pool with HTTP/2 to Eliminate Per-Tool TLS Overhead

**Decision:** A single `httpx.AsyncClient` instance with `http2=True` and a
configured keep-alive pool is shared across all MCP tool calls within a process
lifetime. TLS session resumption reuses session tickets across reconnects to the
same pod.

**Problem observed:** Without a shared pool, every tool call in a multi-step workflow
triggers a full TCP + TLS handshake. A 4-tool workflow = 4 handshakes = ~200–600ms
of avoidable overhead on top of actual tool execution.

**Alternatives considered:**
- Accept per-call handshakes → rejected: latency is visible to candidates on slower
  queries; a 4-tool workflow that takes 2s of LLM + tool time adding 400ms of TLS
  overhead is a 20% regression for no benefit.
- HTTP/1.1 keep-alive only (no HTTP/2) → acceptable fallback, but HTTP/2 multiplexing
  allows concurrent tool calls to the same pod over a single connection — strictly
  better if `candidate-mcp` supports it (Tomcat does by default with `h2`).
- Connection per request (current default in langchain-mcp-adapters) → baseline,
  rejected for production.

**Consequence:** The `SignatureProvider` transport patch (R-01) must also configure
the shared `httpx.AsyncClient`. These two concerns are implemented together in the
same transport wrapper.

---

### DD-08: Redis-Backed LangGraph Checkpointer Replaces MemorySaver

**Decision:** Both the v1 and v2 graphs use `AsyncRedisSaver` as their LangGraph
checkpointer rather than `MemorySaver`.

**Problem:** With 8 uvicorn workers per pod and multiple pods, `MemorySaver` is
per-process. Any multi-turn conversation whose second request lands on a different
worker or pod silently loses all conversation history. The candidate sees the agent
"forget" everything from the previous turn.

**Alternatives considered:**
- Sticky sessions (route by `thread_id`) → requires session affinity in the ingress
  / load balancer; defeats horizontal scaling; single pod failure loses all
  in-flight sessions.
- External session store per worker → still requires cross-process synchronisation;
  effectively reinvents a distributed cache.
- Accept conversation loss → rejected immediately; multi-turn context is a core
  product requirement.

**Consequence:** Adds a Redis write on every checkpoint (every LLM turn). Redis
is already in production infrastructure. The write cost is a single JSON set
operation per turn — negligible compared to LLM and downstream service latency.
Thread TTL of 2 hours prevents unbounded key growth.

---

### DD-09: Distributed Lock + Redis Cache for MCP Static Resource Startup

**Decision:** `init_registry()` checks Redis for cached schema blobs before
fetching from `candidate-mcp`. The first worker to acquire a distributed lock
performs the fetch and populates the cache; all other workers wait and then read
from the cache.

**Problem:** 8 workers × N pods each calling `init_registry()` independently would
send up to 8N identical requests to `candidate-mcp` for immutable schema data on
every deployment restart. This unnecessarily loads the MCP server and slows startup.

**Alternatives considered:**
- Pre-startup script (init container) fetches and seeds Redis → rejected: adds a
  Kubernetes init container dependency; complicates the deployment manifest;
  schemas still need to be refreshed on `candidate-mcp` redeploy.
- Single-worker startup model (run `init_registry()` once in the parent process
  before forking) → not compatible with uvicorn's `--workers` process model where
  each worker is a forked process that runs its own lifespan.
- Accept redundant fetches → acceptable in small deployments, but at 8 workers ×
  5 pods = 40 fetches on a rolling restart this produces a measurable spike.

**Consequence:** Schemas are effectively immutable per `candidate-mcp` deployment.
A 24-hour TTL with version-key invalidation ensures the agent is never more than
one restart away from picking up a new schema. The distributed lock introduces a
short (< 1s typical) startup delay for workers that lose the lock race — acceptable
given startup only happens on deployment.

---

### DD-10: Circuit Breaker per Downstream Service

**Decision:** Three independent Resilience4j circuit breakers — one each for
`talent-profile-service`, `cx-applications`, and `job-sync-service`.

**Alternatives considered:**
- Single shared circuit breaker → rejected: a failure in `cx-applications` would
  block profile lookups from `talent-profile-service`; incorrect blast radius.

**Consequence:** A full outage of one service degrades only the tools that depend on
it. Profile and assessment tools remain functional if `cx-applications` is down.
`getJobDetails` enrichment degrades gracefully if `job-sync-service` is unavailable — the
assistant can still answer application status queries without job details.

---

## 16. Open Issues & Risks

| ID | Issue / Risk | Severity | Owner | Status |
|---|---|---|---|---|
| R-01 | `langchain-mcp-adapters` does not natively support custom per-request header injection. The `SignatureProvider` must wrap or patch the httpx transport layer. Verify compatibility with `langchain-mcp-adapters 0.2.x`. Same httpx transport patch also enables shared connection pool for TLS reuse. | High | Platform team | Open — spike required |
| R-02 | App2App shared secret rotation requires coordinated redeployment of the affected service pair (or live Vault reload). Applies to all three service hops. Rotation procedure not yet defined. | High | Security / Infra | Open |
| R-03 | Clock drift between the Python agent host and `candidate-mcp` pods may cause valid signatures to be rejected if drift exceeds TTL. NTP synchronisation must be enforced across all pods. | Medium | Infra team | Open |
| R-04 | `careers-data-schema` does not currently produce JSON Schema output. Serialisation logic must be added to `candidate-mcp`. | Medium | Backend team | Open |
| R-05 | Downstream service API contracts with `cx-applications` and `talent-profile-service` are not yet formalised as Pact consumer contracts. Schema drift is undetected until runtime. | Medium | QA / Backend teams | Open — Pact adoption planned for Q3 |
| R-06 | Redis unavailability at careers-ai-service startup: if Redis is down, the distributed lock cannot be acquired and workers fall back to fetching schemas directly from `candidate-mcp` (8N fetches). Acceptable degraded path but must be tested. | Low | Infra team | Accepted |
| R-07 | Redis unavailability during request handling: LangGraph checkpointer fails to save/load → conversation context is lost for that turn. Agent should catch the exception and respond without context rather than returning a 500. Circuit breaker around Redis operations recommended. | Medium | Platform team | Open |
| R-08 | Embedding all schema resources in the LLM system prompt consumes context window tokens. Impact to be measured in staging. | Low | AI team | Open |
| R-09 | v1 and v2 graphs share no state. A user switching between `/api/v1` and `/api/v2` endpoints within the same session will lose conversation context. Cross-version thread continuity is not supported and must be communicated to consumers. | Low | Platform team | Accepted for now |

---

---

**Document Version**: 2.0
**Status**: Production Implementation Plan
