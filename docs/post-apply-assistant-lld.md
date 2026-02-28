# Low Level Design: post_apply_assistant + post-apply-mcp

**Scope:** Integration of `post_apply_assistant` sub-assistant into the existing primary assistant
LangGraph workflow, plus the full implementation of the backing Java MCP server
(`post-apply-mcp`) that makes real API calls to `job-sync-service`,
`talent-profile-service`, and `cx-applications`.

**Prototype reference:** `candidate-mcp` (Java) + `candidate-agent` (Python) in this repo.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Python LangGraph Application (Uvicorn)                                     │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  StateGraph (AgentState)                                             │   │
│  │                                                                      │   │
│  │   START ──► [primary_assistant] ──────────────────────────► END     │   │
│  │                    │                                                  │   │
│  │                    │ transfer_to_post_apply_assistant()               │   │
│  │                    ▼                                                  │   │
│  │             [post_apply_assistant] ──────────────────────► END      │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│          │                          │                                        │
│          │ MCP (existing tools)     │ MCP (post-apply tools)                │
└──────────┼──────────────────────────┼────────────────────────────────────────┘
           │                          │
           ▼                          ▼
  ┌─────────────────┐       ┌──────────────────────────────────────────────┐
  │  existing-mcp   │       │  post-apply-mcp  (Spring AI 1.1.x, Java 21) │
  │  (out of scope) │       │                                              │
  └─────────────────┘       │  Tools                                       │
                             │    getApplicationStatus                      │
                             │    getApplicationTimeline                    │
                             │    getApplicationNextSteps                   │
                             │    getInterviewFeedback                      │
                             │    getJobDetails                             │
                             │    getCandidateProfile                       │
                             │    getCandidateAssessments                   │
                             │    getCandidatePreferences                   │
                             │                                              │
                             │  Resources (static)                          │
                             │    ats://workflow/application-stages         │
                             │    ats://workflow/post-apply-guide           │
                             └──────────────────────────────────────────────┘
                                  │              │              │
                    ┌─────────────┘   ┌──────────┘   ┌─────────┘
                    ▼                 ▼               ▼
           ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
           │  cx-apps     │  │  job-sync    │  │  talent-profile  │
           │  service     │  │  service     │  │  service         │
           └──────────────┘  └──────────────┘  └──────────────────┘
```

---

## 2. Python Side — post_apply_assistant Integration

### 2.1 State Schema

The existing `AgentState` must expose `application_id` so the post_apply_assistant
can reference it without re-extracting it from the conversation.

```python
# agents/state.py  (additions to existing AgentState)
class AgentState(MessagesState):
    # --- existing fields ---
    candidate_id: str
    correlation_id: str
    active_agent: str
    remaining_steps: NotRequired[Annotated[int, RemainingStepsManager]]

    # --- new field ---
    application_id: str   # populated by primary_assistant when a post-apply
                          # context is detected; passed to post_apply_assistant
```

`application_id` uses `str` (not `Optional`) because `MessagesState` uses
append-only reducers — missing optional fields cause reducer errors in LangGraph 1.x.
Default to `""` in the initial state payload.

### 2.2 MCP Registry — second server connection

The existing `MCPToolRegistry` connects to one MCP server. The production registry
must hold connections to both:

```python
# mcp/client.py

# Tools owned exclusively by post_apply_assistant
POST_APPLY_TOOL_NAMES: frozenset[str] = frozenset({
    "getApplicationStatus",
    "getApplicationTimeline",
    "getApplicationNextSteps",
    "getInterviewFeedback",
    "getJobDetails",
    "getCandidateProfile",
    "getCandidateAssessments",
    "getCandidatePreferences",
})

@dataclass
class MCPToolRegistry:
    client: MultiServerMCPClient

    # primary_assistant tools (existing MCP server)
    primary_tools: list[BaseTool]

    # post_apply_assistant tools (post-apply-mcp)
    post_apply_tools: list[BaseTool]

    # all tools (primary_tools union post_apply_tools) for primary agent
    all_tools: list[BaseTool]

    # static knowledge resources
    workflow_stages_json: str      # ats://workflow/application-stages
    post_apply_guide_json: str     # ats://workflow/post-apply-guide


async def init_registry(settings: Settings) -> MCPToolRegistry:
    client = MultiServerMCPClient({
        "existing_mcp": {
            "url": settings.existing_mcp_url,
            "transport": "streamable_http",
            "headers": {"Accept": "application/json, text/event-stream"},
        },
        "post_apply_mcp": {
            "url": settings.post_apply_mcp_url,
            "transport": "streamable_http",
            "headers": {
                "Accept": "application/json, text/event-stream",
                # service-to-service auth — token fetched from token provider
                "Authorization": f"Bearer {await get_service_token(settings)}",
            },
        },
    })

    all_tools = await client.get_tools()
    post_apply_tools = [t for t in all_tools if t.name in POST_APPLY_TOOL_NAMES]
    primary_tools   = [t for t in all_tools if t.name not in POST_APPLY_TOOL_NAMES]

    blobs = await client.get_resources("post_apply_mcp", uris=[
        "ats://workflow/application-stages",
        "ats://workflow/post-apply-guide",
    ])
    ...
```

**Auth note:** The MCP HTTP headers are set at session-creation time. In production
the service token is short-lived (e.g. 5 min). Use a `TokenProvider` that refreshes
lazily before expiry; pass the token per-request by using `client.session(...)` with
fresh headers rather than baking the token into the constructor.

See §6.1 for the token provider pattern.

### 2.3 Graph Wiring

```python
# agents/graph.py  (additions to build_graph)

@tool
def transfer_to_post_apply_assistant(reason: str) -> Command:
    """Transfer to the Post-Apply specialist assistant.

    Use when the user asks about:
    - Status of an application they just submitted
    - What happens next after applying
    - Interview scheduling or preparation
    - Assessment completion or results
    - Offer details or decision support
    - Withdrawal of an application

    Args:
        reason: Why you are transferring.
    """
    logger.info("handoff_to_post_apply_assistant", reason=reason)
    return Command(
        goto="post_apply_assistant",
        update={"active_agent": "post_apply_assistant"},
        graph=Command.PARENT,
    )


post_apply_agent = create_react_agent(
    model=llm,
    tools=registry.post_apply_tools,
    prompt=build_post_apply_prompt(
        stages_json=registry.workflow_stages_json,
        guide_json=registry.post_apply_guide_json,
    ),
    state_schema=AgentState,
    name="post_apply_assistant",
)

primary_agent = create_react_agent(
    model=llm,
    tools=[*registry.primary_tools, transfer_to_post_apply_assistant],
    prompt=build_primary_prompt(...),
    state_schema=AgentState,
    name="primary_assistant",
)

builder = StateGraph(AgentState)
builder.add_node("primary_assistant",    primary_agent)
builder.add_node("post_apply_assistant", post_apply_agent)
builder.add_edge(START, "primary_assistant")
builder.add_edge("primary_assistant",    END)
builder.add_edge("post_apply_assistant", END)
```

### 2.4 post_apply_assistant System Prompt

```python
def build_post_apply_prompt(stages_json: str, guide_json: str) -> str:
    return f"""You are the Post-Apply Journey Specialist — a focused AI expert
on the candidate experience after a job application is submitted.

Your responsibilities:
- Retrieve and explain current application status in plain, empathetic language.
- Narrate the candidate's application timeline with key milestones and dates.
- Explain what the current stage means and exactly what happens next.
- Surface SLA breaches (days in stage exceeding expected window) proactively.
- Surface required candidate actions (assessments pending, documents needed).
- Provide interview preparation guidance appropriate for the upcoming stage.
- Support offer evaluation and withdrawal decisions.

## Tool Usage
Always fetch live data before responding:
- `getApplicationStatus`   — current stage, days in stage, SLA health
- `getApplicationTimeline` — full history of stage transitions
- `getApplicationNextSteps`— stage-specific candidate actions
- `getInterviewFeedback`   — interview rounds completed and recruiter notes
- `getJobDetails`          — role context (JD, team, location, comp band)
- `getCandidateProfile`    — candidate skills and experience for gap analysis
- `getCandidateAssessments`— completed and pending assessments
- `getCandidatePreferences`— location, role, comp preferences for offer fit

## Response Style
- Empathetic and direct. Translate status codes to plain English.
- Always cite applicationId, jobTitle, and current stage.
- When an SLA is breached, flag it constructively with context.
- For pending actions, be specific: what, why, by when.

## Application Stage Reference
```json
{stages_json}
```

## Post-Apply Candidate Guide
```json
{guide_json}
```"""
```

### 2.5 Primary Assistant Routing Rule (prompt addition)

Add to the existing primary assistant prompt:

```
- For queries about **post-application status, next steps after applying,
  interview preparation, assessment completion, offer evaluation, or application
  withdrawal** → call `transfer_to_post_apply_assistant` with a brief reason.
  Pass `application_id` in the state update if known from context.
```

### 2.6 Settings (additions)

```python
# config.py
class Settings(BaseSettings):
    ...
    # existing MCP
    existing_mcp_url: str = "http://existing-mcp-service/mcp"

    # post-apply MCP
    post_apply_mcp_url: str = "http://post-apply-mcp-service/mcp"

    # service-to-service auth
    auth_token_url: str           # token endpoint (OAuth2 client_credentials)
    auth_client_id: str
    auth_client_secret: SecretStr
    auth_scope: str = "mcp:read"
```

---

## 3. Java MCP Server — post-apply-mcp

### 3.1 Project Structure

```
post-apply-mcp/
├── pom.xml
└── src/main/java/com/company/postapply/mcp/
    ├── PostApplyMcpApplication.java
    ├── config/
    │   ├── McpConfiguration.java          # MCP tool/resource registration
    │   ├── WebClientConfiguration.java    # downstream HTTP clients
    │   └── ResilienceConfiguration.java   # circuit breakers, retries
    ├── tool/
    │   ├── ApplicationTools.java          # cx-applications tools
    │   ├── JobTools.java                  # job-sync-service tools
    │   └── ProfileTools.java              # talent-profile-service tools
    ├── resource/
    │   └── StaticResources.java           # workflow-stages, post-apply-guide
    ├── client/
    │   ├── CxApplicationsClient.java
    │   ├── JobSyncClient.java
    │   └── TalentProfileClient.java
    ├── dto/
    │   ├── application/
    │   │   ├── ApplicationStatusDto.java
    │   │   ├── ApplicationTimelineDto.java
    │   │   └── InterviewFeedbackDto.java
    │   ├── job/
    │   │   └── JobDetailsDto.java
    │   └── profile/
    │       ├── CandidateProfileDto.java
    │       ├── AssessmentResultDto.java
    │       └── CandidatePreferencesDto.java
    └── exception/
        ├── DownstreamException.java
        └── GlobalMcpExceptionHandler.java
```

### 3.2 pom.xml (key dependencies)

```xml
<properties>
    <java.version>21</java.version>
    <spring-boot.version>3.5.0</spring-boot.version>
    <spring-ai.version>1.1.2</spring-ai.version>
</properties>

<dependencies>
    <!-- MCP server -->
    <dependency>
        <groupId>org.springframework.ai</groupId>
        <artifactId>spring-ai-starter-mcp-server-webmvc</artifactId>
    </dependency>

    <!-- Downstream HTTP clients -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-webflux</artifactId>  <!-- WebClient -->
    </dependency>

    <!-- Resilience -->
    <dependency>
        <groupId>io.github.resilience4j</groupId>
        <artifactId>resilience4j-spring-boot3</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-aop</artifactId>  <!-- required by R4j -->
    </dependency>

    <!-- Observability -->
    <dependency>
        <groupId>io.micrometer</groupId>
        <artifactId>micrometer-tracing-bridge-otel</artifactId>
    </dependency>
    <dependency>
        <groupId>io.opentelemetry</groupId>
        <artifactId>opentelemetry-exporter-otlp</artifactId>
    </dependency>

    <!-- Security -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-oauth2-resource-server</artifactId>
    </dependency>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-oauth2-client</artifactId>
    </dependency>

    <!-- Validation & serialisation -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-validation</artifactId>
    </dependency>
    <dependency>
        <groupId>com.fasterxml.jackson.module</groupId>
        <artifactId>jackson-module-blackbird</artifactId>
    </dependency>
</dependencies>
```

### 3.3 McpConfiguration.java

```java
@Configuration(proxyBeanMethods = false)
public class McpConfiguration {

    // ── Application tools (cx-applications) ──────────────────────────────────

    @Bean
    List<McpStatelessServerFeatures.SyncToolSpecification> applicationTools(
            ApplicationTools tools) {

        return List.of(
            syncTool("getApplicationStatus",
                "Current status of a job application: stage, days in stage, SLA health.",
                schema(Map.of(
                    "applicationId", strField("Application ID (e.g. APP-12345)"),
                    "candidateId",   strField("Candidate ID")
                ), List.of("applicationId", "candidateId")),
                (ctx, args) -> tools.getStatus(
                    args.get("applicationId").asText(),
                    args.get("candidateId").asText()
                )
            ),

            syncTool("getApplicationTimeline",
                "Full ordered history of stage transitions for an application.",
                schema(Map.of(
                    "applicationId", strField("Application ID")
                ), List.of("applicationId")),
                (ctx, args) -> tools.getTimeline(args.get("applicationId").asText())
            ),

            syncTool("getApplicationNextSteps",
                "Concrete next actions the candidate must take for their current stage.",
                schema(Map.of(
                    "applicationId", strField("Application ID"),
                    "candidateId",   strField("Candidate ID")
                ), List.of("applicationId", "candidateId")),
                (ctx, args) -> tools.getNextSteps(
                    args.get("applicationId").asText(),
                    args.get("candidateId").asText()
                )
            ),

            syncTool("getInterviewFeedback",
                "Completed interview rounds and any released recruiter notes.",
                schema(Map.of(
                    "applicationId", strField("Application ID")
                ), List.of("applicationId")),
                (ctx, args) -> tools.getInterviewFeedback(args.get("applicationId").asText())
            )
        );
    }

    // ── Job tools (job-sync-service) ──────────────────────────────────────────

    @Bean
    List<McpStatelessServerFeatures.SyncToolSpecification> jobTools(JobTools tools) {
        return List.of(
            syncTool("getJobDetails",
                "Full job requisition details: title, description, requirements, comp band, location.",
                schema(Map.of(
                    "jobId", strField("Job requisition ID (e.g. JOB-9876)")
                ), List.of("jobId")),
                (ctx, args) -> tools.getJobDetails(args.get("jobId").asText())
            )
        );
    }

    // ── Profile tools (talent-profile-service) ────────────────────────────────

    @Bean
    List<McpStatelessServerFeatures.SyncToolSpecification> profileTools(ProfileTools tools) {
        return List.of(
            syncTool("getCandidateProfile",
                "Candidate's skills, experience, education, and current employment.",
                schema(Map.of(
                    "candidateId", strField("Candidate ID")
                ), List.of("candidateId")),
                (ctx, args) -> tools.getProfile(args.get("candidateId").asText())
            ),

            syncTool("getCandidateAssessments",
                "All assessment results: completed, pending, and scores.",
                schema(Map.of(
                    "candidateId",   strField("Candidate ID"),
                    "applicationId", strField("Filter by application (optional)")
                ), List.of("candidateId")),
                (ctx, args) -> tools.getAssessments(
                    args.get("candidateId").asText(),
                    args.has("applicationId") ? args.get("applicationId").asText() : null
                )
            ),

            syncTool("getCandidatePreferences",
                "Candidate's stated job preferences: location, role type, comp expectations.",
                schema(Map.of(
                    "candidateId", strField("Candidate ID")
                ), List.of("candidateId")),
                (ctx, args) -> tools.getPreferences(args.get("candidateId").asText())
            )
        );
    }

    // ── Static resources ──────────────────────────────────────────────────────

    @Bean
    List<McpStatelessServerFeatures.SyncResourceSpecification> staticResources(
            StaticResources resources) {

        return List.of(
            syncResource(
                "ats://workflow/application-stages",
                "Application Stage Definitions",
                "application/json",
                (ctx, req) -> resources.applicationStages()
            ),
            syncResource(
                "ats://workflow/post-apply-guide",
                "Post-Apply Candidate Guide",
                "application/json",
                (ctx, req) -> resources.postApplyGuide()
            )
        );
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    private static McpSchema.JsonSchema schema(
            Map<String, Object> properties, List<String> required) {
        return new McpSchema.JsonSchema("object", properties, required, false, null, null);
    }

    private static Map<String, Object> strField(String description) {
        return Map.of("type", "string", "description", description);
    }
}
```

---

## 4. Downstream Service Clients

### 4.1 WebClientConfiguration.java

Each downstream service gets its own `WebClient` bean, isolated base URL,
timeout, and auth token relay.

```java
@Configuration(proxyBeanMethods = false)
public class WebClientConfiguration {

    @Bean
    WebClient cxApplicationsClient(WebClient.Builder builder,
                                    AppProperties props,
                                    TokenRelay tokenRelay) {
        return builder
            .baseUrl(props.getCxApplicationsBaseUrl())
            .defaultHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
            .filter(tokenRelay.filter())           // propagate or inject service token
            .filter(tracingFilter())               // inject trace/span headers
            .build();
    }

    @Bean
    WebClient jobSyncClient(WebClient.Builder builder, AppProperties props,
                             TokenRelay tokenRelay) {
        return builder
            .baseUrl(props.getJobSyncBaseUrl())
            .filter(tokenRelay.filter())
            .filter(tracingFilter())
            .build();
    }

    @Bean
    WebClient talentProfileClient(WebClient.Builder builder, AppProperties props,
                                   TokenRelay tokenRelay) {
        return builder
            .baseUrl(props.getTalentProfileBaseUrl())
            .filter(tokenRelay.filter())
            .filter(tracingFilter())
            .build();
    }

    private ExchangeFilterFunction tracingFilter() {
        // Propagates W3C trace context (traceparent, tracestate) downstream
        return (request, next) -> {
            ClientRequest traced = ClientRequest.from(request)
                .headers(h -> Span.current().inject(h, HttpHeaders::set))
                .build();
            return next.exchange(traced);
        };
    }
}
```

### 4.2 CxApplicationsClient.java

```java
@Component
public class CxApplicationsClient {

    private final WebClient client;
    private final ObjectMapper mapper;

    // Circuit breaker & retry applied at the tool layer via @CircuitBreaker
    // Timeout configured in WebClient via HttpClient connection/response timeout

    public ApplicationStatusDto getStatus(String applicationId, String candidateId) {
        return client.get()
            .uri("/v1/applications/{applicationId}/status?candidateId={candidateId}",
                 applicationId, candidateId)
            .retrieve()
            .onStatus(HttpStatusCode::is4xxClientError, resp ->
                resp.bodyToMono(String.class).map(body ->
                    new DownstreamException("cx-applications", resp.statusCode().value(), body)))
            .bodyToMono(ApplicationStatusDto.class)
            .timeout(Duration.ofSeconds(5))
            .block();   // MCP tools are sync; block() is on a virtual-thread executor
    }

    public List<TimelineEventDto> getTimeline(String applicationId) {
        return client.get()
            .uri("/v1/applications/{applicationId}/timeline", applicationId)
            .retrieve()
            .bodyToFlux(TimelineEventDto.class)
            .timeout(Duration.ofSeconds(5))
            .collectList()
            .block();
    }

    public InterviewFeedbackDto getInterviewFeedback(String applicationId) {
        return client.get()
            .uri("/v1/applications/{applicationId}/interviews", applicationId)
            .retrieve()
            .bodyToMono(InterviewFeedbackDto.class)
            .timeout(Duration.ofSeconds(5))
            .block();
    }
}
```

> **Virtual threads note:** Spring Boot 3.2+ with `spring.threads.virtual.enabled=true`
> makes `block()` safe on the request thread. MCP tool handlers run on Tomcat
> request threads. Enable virtual threads so blocking IO does not exhaust the
> thread pool.

### 4.3 Resilience Configuration

```java
// ResilienceConfiguration.java — R4j beans wired explicitly (avoids AspectJ magic)
@Configuration(proxyBeanMethods = false)
public class ResilienceConfiguration {

    @Bean
    CircuitBreakerRegistry circuitBreakerRegistry() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
            .failureRateThreshold(50)
            .waitDurationInOpenState(Duration.ofSeconds(30))
            .slidingWindowSize(20)
            .permittedNumberOfCallsInHalfOpenState(5)
            .recordExceptions(DownstreamException.class, WebClientRequestException.class)
            .build();
        return CircuitBreakerRegistry.of(config);
    }

    @Bean
    RetryRegistry retryRegistry() {
        RetryConfig config = RetryConfig.custom()
            .maxAttempts(3)
            .waitDuration(Duration.ofMillis(200))
            .retryExceptions(WebClientRequestException.class)   // network errors only
            .ignoreExceptions(DownstreamException.class)        // 4xx — don't retry
            .build();
        return RetryRegistry.of(config);
    }
}
```

**Applied at the tool handler level:**

```java
// ApplicationTools.java
@Component
public class ApplicationTools {

    private final CxApplicationsClient client;
    private final CircuitBreaker cxBreaker;
    private final Retry cxRetry;
    private final ObjectMapper mapper;

    public ApplicationTools(CxApplicationsClient client,
                             CircuitBreakerRegistry cbRegistry,
                             RetryRegistry retryRegistry,
                             ObjectMapper mapper) {
        this.client   = client;
        this.cxBreaker = cbRegistry.circuitBreaker("cx-applications");
        this.cxRetry   = retryRegistry.retry("cx-applications");
        this.mapper    = mapper;
    }

    public String getStatus(String applicationId, String candidateId) {
        try {
            ApplicationStatusDto dto = Retry.decorateSupplier(cxRetry,
                CircuitBreaker.decorateSupplier(cxBreaker,
                    () -> client.getStatus(applicationId, candidateId)
                )
            ).get();
            return mapper.writeValueAsString(dto);
        } catch (CallNotPermittedException e) {
            // Circuit is open — return a graceful degraded response
            return """
                {"error":"application_status_unavailable",
                 "message":"Application status is temporarily unavailable. Please try again shortly.",
                 "applicationId":"%s"}""".formatted(applicationId);
        } catch (DownstreamException e) {
            return """
                {"error":"downstream_error","status":%d,"applicationId":"%s","detail":"%s"}"""
                .formatted(e.getStatusCode(), applicationId, e.getMessage());
        }
    }
}
```

---

## 5. Service-to-Service Authentication

### 5.1 Token Provider Pattern (Python side)

The MCP client headers are set once per session. For short-lived tokens, use a
lazy-refreshing provider:

```python
# auth/token_provider.py
import asyncio
import time
import httpx

class ServiceTokenProvider:
    """OAuth2 client_credentials token with lazy refresh."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._token: str | None = None
        self._expires_at: float = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        if time.monotonic() < self._expires_at - 30:   # 30s buffer
            return self._token
        async with self._lock:
            if time.monotonic() < self._expires_at - 30:
                return self._token
            await self._refresh()
        return self._token

    async def _refresh(self) -> None:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                self._settings.auth_token_url,
                data={
                    "grant_type":    "client_credentials",
                    "client_id":     self._settings.auth_client_id,
                    "client_secret": self._settings.auth_client_secret.get_secret_value(),
                    "scope":         self._settings.auth_scope,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token      = body["access_token"]
            self._expires_at = time.monotonic() + body["expires_in"]
```

Because `langchain-mcp-adapters` creates a new session per tool call, the token
header must be injected per-session. Override the session factory rather than
baking it into the constructor:

```python
# mcp/client.py  — session-level token injection
async def _create_post_apply_session(client, token_provider):
    token = await token_provider.get_token()
    async with client.session(
        "post_apply_mcp",
        # langchain-mcp-adapters 0.2+ does not yet support per-session header
        # override; workaround: recreate the MultiServerMCPClient connection
        # config with the fresh token before each call.
        # Track: https://github.com/langchain-ai/langchain-mcp-adapters
    ) as session:
        yield session
```

> **Workaround until langchain-mcp-adapters supports per-session headers:**
> Store the token in a `contextvars.ContextVar` and use a custom
> `httpx.AsyncClient` with an auth flow that reads from the context var.
> Alternatively, spin up a thin local proxy (e.g. Envoy sidecar) that handles
> token injection so the Python client stays auth-free.

### 5.2 Resource Server (Java side)

```yaml
# application.yml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: ${AUTH_ISSUER_URI}
          audiences: post-apply-mcp
```

```java
@Configuration(proxyBeanMethods = false)
@EnableWebSecurity
public class SecurityConfiguration {

    @Bean
    SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/actuator/health/**").permitAll()
                .requestMatchers("/mcp/**").hasAuthority("SCOPE_mcp:read")
                .anyRequest().denyAll()
            )
            .oauth2ResourceServer(oauth2 -> oauth2.jwt(Customizer.withDefaults()))
            .csrf(AbstractHttpConfigurer::disable)   // stateless MCP endpoint
            .build();
    }
}
```

### 5.3 Downstream Service Auth (Java side)

The MCP server acts as both a resource server (validates inbound tokens) and an
OAuth2 client (injects service tokens for downstream calls):

```yaml
spring:
  security:
    oauth2:
      client:
        registration:
          cx-applications:
            authorization-grant-type: client_credentials
            client-id: ${CX_APPS_CLIENT_ID}
            client-secret: ${CX_APPS_CLIENT_SECRET}
            scope: applications:read
          job-sync:
            authorization-grant-type: client_credentials
            client-id: ${JOB_SYNC_CLIENT_ID}
            client-secret: ${JOB_SYNC_CLIENT_SECRET}
            scope: jobs:read
          talent-profile:
            authorization-grant-type: client_credentials
            client-id: ${TALENT_PROFILE_CLIENT_ID}
            client-secret: ${TALENT_PROFILE_CLIENT_SECRET}
            scope: profiles:read
        provider:
          cx-applications:
            token-uri: ${AUTH_TOKEN_URI}
          job-sync:
            token-uri: ${AUTH_TOKEN_URI}
          talent-profile:
            token-uri: ${AUTH_TOKEN_URI}
```

`ServerOAuth2AuthorizedClientExchangeFilterFunction` manages token acquisition
and refresh automatically per registered client.

---

## 6. Observability

### 6.1 Distributed Tracing

The correlation ID generated at the Python API layer must propagate through:

```
Browser / API client
  → Python agent (correlation_id in AgentState)
    → MCP HTTP call (traceparent header, injected by httpx)
      → post-apply-mcp (Spring reads traceparent, continues trace)
        → cx-applications (WebClient propagates trace context)
```

**Python:** Enable OpenTelemetry with the OTLP exporter:

```python
# main.py — before app creation
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
trace.set_tracer_provider(provider)
FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()   # instruments httpx used by MCP client
```

**Java:** Auto-configured by Micrometer + OTel bridge when the dependencies are
present. Configure the exporter:

```yaml
management:
  tracing:
    sampling:
      probability: 1.0   # 100% in dev; use 0.1 in prod or tail-sampling
  otlp:
    tracing:
      endpoint: ${OTEL_EXPORTER_OTLP_ENDPOINT}
```

### 6.2 Structured Logging

**Python:** `structlog` (already in place). Bind `trace_id` and `span_id` from
the active OTel span:

```python
# logging_setup.py — add OTel processor
def _otel_processor(logger, method, event_dict):
    span = trace.get_current_span()
    if span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"]  = format(ctx.span_id,  "016x")
    return event_dict
```

**Java:** Logback with `logstash-logback-encoder` outputs JSON. Micrometer
auto-adds `traceId` and `spanId` MDC fields.

### 6.3 Metrics

Expose application-level metrics from the MCP server:

| Metric | Type | Labels |
|---|---|---|
| `mcp.tool.calls.total` | Counter | `tool`, `status` (success/error) |
| `mcp.tool.duration.seconds` | Histogram | `tool` |
| `downstream.calls.total` | Counter | `service`, `status` |
| `downstream.duration.seconds` | Histogram | `service`, `endpoint` |
| `circuit_breaker.state` | Gauge | `name`, `state` (closed/open/half_open) |

R4j + Micrometer auto-exports circuit breaker metrics. Add tool-level metrics
with a custom `McpToolMetricsInterceptor`.

---

## 7. Caching Strategy

Not all tool calls need live data. Apply cache-aside at the Java client layer:

| Tool | Cacheable? | TTL | Cache key |
|---|---|---|---|
| `getJobDetails` | Yes | 10 min | `jobId` |
| `getCandidateProfile` | Yes | 5 min | `candidateId` |
| `getCandidatePreferences` | Yes | 5 min | `candidateId` |
| `getCandidateAssessments` | Partial | 2 min | `candidateId:applicationId` |
| `getApplicationStatus` | No | — | status changes frequently |
| `getApplicationTimeline` | No | — | may have new events |
| `getInterviewFeedback` | No | — | may be updated post-interview |

Use Spring Cache with Redis:

```java
@Cacheable(value = "job-details", key = "#jobId")
public JobDetailsDto getJobDetails(String jobId) { ... }

@Cacheable(value = "candidate-profile", key = "#candidateId")
public CandidateProfileDto getProfile(String candidateId) { ... }

@CacheEvict(value = "candidate-profile", key = "#candidateId")
public void evictProfile(String candidateId) { ... }  // called on profile-update event
```

```yaml
spring:
  cache:
    type: redis
  data:
    redis:
      host: ${REDIS_HOST}
      port: 6379
      ssl:
        enabled: true
```

---

## 8. Error Handling Contract

MCP tools must never throw — unhandled exceptions crash the LLM's tool-use loop.
All tool handlers return a JSON string. The error envelope:

```json
{
  "error": "application_not_found",
  "message": "Application APP-99999 was not found or you do not have access.",
  "applicationId": "APP-99999",
  "retriable": false
}
```

The LLM reads this and generates a user-friendly message. Never expose stack
traces or internal service URLs in the error envelope.

**Error taxonomy:**

| Scenario | `error` value | `retriable` |
|---|---|---|
| Resource not found (404) | `{resource}_not_found` | false |
| Caller unauthorised (403) | `access_denied` | false |
| Downstream timeout | `service_timeout` | true |
| Circuit open | `service_unavailable` | true |
| Unexpected error | `internal_error` | false |

---

## 9. Data Models

### ApplicationStatusDto

```java
public record ApplicationStatusDto(
    String applicationId,
    String candidateId,
    String jobId,
    String jobTitle,
    String currentStage,          // e.g. "TECHNICAL_INTERVIEW"
    String currentStageLabel,     // e.g. "Technical Interview"
    int    daysInCurrentStage,
    int    expectedStageDurationDays,
    boolean slaBreached,
    String  lastUpdatedAt,        // ISO-8601
    List<String> pendingActions   // candidate actions required
) {}
```

### ApplicationTimelineDto

```java
public record ApplicationTimelineDto(
    String applicationId,
    List<TimelineEventDto> events
) {}

public record TimelineEventDto(
    String stage,
    String label,
    String timestamp,
    String actor,         // "system" | "recruiter-{id}" | "candidate"
    String reason
) {}
```

### JobDetailsDto

```java
public record JobDetailsDto(
    String jobId,
    String title,
    String department,
    String location,
    String workType,              // REMOTE | HYBRID | ONSITE
    String employmentType,        // FULL_TIME | CONTRACT
    String compBandMin,
    String compBandMax,
    String compCurrency,
    String description,
    List<String> requiredSkills,
    List<String> preferredSkills,
    int    minYearsExperience
) {}
```

### CandidateProfileDto / AssessmentResultDto / CandidatePreferencesDto

Align field names exactly with what `talent-profile-service` returns to avoid
transformation layers. Use `@JsonAlias` for snake_case ↔ camelCase normalisation.

---

## 10. Configuration Reference

```yaml
# application.yml

spring:
  application:
    name: post-apply-mcp
  threads:
    virtual:
      enabled: true        # Java 21 virtual threads — safe to block in tool handlers
  ai:
    mcp:
      server:
        enabled: true
        type: SYNC
        name: post-apply-mcp
        version: 1.0.0
        protocol: STATELESS
        instructions: "Post-apply MCP server for cx-applications, job-sync, and talent-profile"

# Downstream service URLs (injected from K8s ConfigMap)
app:
  cx-applications-base-url:  ${CX_APPLICATIONS_BASE_URL}
  job-sync-base-url:         ${JOB_SYNC_BASE_URL}
  talent-profile-base-url:   ${TALENT_PROFILE_BASE_URL}

# Resilience
resilience4j:
  circuitbreaker:
    instances:
      cx-applications:
        failure-rate-threshold: 50
        wait-duration-in-open-state: 30s
        sliding-window-size: 20
      job-sync:
        failure-rate-threshold: 50
        wait-duration-in-open-state: 30s
        sliding-window-size: 10
      talent-profile:
        failure-rate-threshold: 50
        wait-duration-in-open-state: 30s
        sliding-window-size: 10
  retry:
    instances:
      cx-applications:
        max-attempts: 3
        wait-duration: 200ms
      job-sync:
        max-attempts: 3
        wait-duration: 200ms
      talent-profile:
        max-attempts: 3
        wait-duration: 200ms

# WebClient timeouts
spring.webflux.client:
  connect-timeout: 2000    # ms
  read-timeout:    5000    # ms

management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus,circuitbreakers
  endpoint:
    health:
      show-details: always
      probes:
        enabled: true   # /actuator/health/liveness, /actuator/health/readiness
  health:
    circuitbreakers:
      enabled: true
```

---

## 11. Testing Strategy

### Unit Tests

- Each `XxxTools` method: mock the client, assert JSON output shape and error envelopes.
- `ApplicationTools.getStatus` with circuit open: assert graceful degraded response.
- Token provider: assert refresh triggers at expiry boundary.

### Integration Tests (Spring Boot Test)

Use `@SpringBootTest` + `WireMock` to stub downstream services:

```java
@SpringBootTest
@AutoConfigureWireMock(port = 0)
class CxApplicationsClientIntegrationTest {

    @Test
    void returnsStatus_whenServiceResponds200() {
        stubFor(get(urlEqualTo("/v1/applications/APP-001/status?candidateId=C001"))
            .willReturn(aResponse()
                .withStatus(200)
                .withBodyFile("application-status.json")));

        ApplicationStatusDto dto = client.getStatus("APP-001", "C001");
        assertThat(dto.currentStage()).isEqualTo("TECHNICAL_INTERVIEW");
    }

    @Test
    void opensCircuitBreaker_afterConsecutiveFailures() {
        stubFor(get(anyUrl()).willReturn(aResponse().withStatus(503)));

        // Trip the breaker
        IntStream.range(0, 20).forEach(i ->
            assertThatThrownBy(() -> client.getStatus("APP-001", "C001"))
                .isInstanceOf(DownstreamException.class));

        // Next call should be short-circuited immediately
        assertThatThrownBy(() -> client.getStatus("APP-001", "C001"))
            .isInstanceOf(CallNotPermittedException.class);
    }
}
```

### Contract Tests (Pact)

Generate consumer-driven contracts from the MCP tool DTOs. Publish to Pact Broker.
`cx-applications`, `job-sync-service`, and `talent-profile-service` verify against
the published contracts in their CI pipelines.

### End-to-End (Python side)

Extend the existing `pytest` suite with post_apply_assistant scenarios:

```python
async def test_post_apply_status_routes_to_subagent(client):
    response = await client.post("/api/v1/agent/invoke", json={
        "message": "What is the current status of my application APP-001?",
        "candidate_id": "C001",
        "application_id": "APP-001",
        "thread_id": "test-postapply-001",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["agent_used"] == "post_apply_assistant"
    assert body["response"]
```

---

## 12. Deployment

### Kubernetes resources (post-apply-mcp)

```yaml
# deployment.yaml
spec:
  template:
    spec:
      containers:
        - name: post-apply-mcp
          image: registry/post-apply-mcp:${VERSION}
          ports:
            - containerPort: 8080
          env:
            - name: CX_APPLICATIONS_BASE_URL
              valueFrom:
                configMapKeyRef:
                  name: post-apply-mcp-config
                  key: cx_applications_base_url
            - name: CX_APPS_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: post-apply-mcp-secrets
                  key: cx_apps_client_secret
          livenessProbe:
            httpGet:
              path: /actuator/health/liveness
              port: 8080
            initialDelaySeconds: 20
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /actuator/health/readiness
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1"
              memory: "1Gi"
```

The readiness probe failing (e.g. circuit breaker open on a downstream service)
will remove the pod from the Service endpoint list, preventing traffic from reaching
a degraded instance.

---

## 13. Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| MCP transport | Stateless streamable HTTP | No session state to manage; scales horizontally |
| Blocking vs reactive in MCP tools | `block()` with virtual threads | MCP SDK is sync; virtual threads make blocking safe |
| Circuit breaker granularity | Per downstream service | Isolates faults; prevents cascade failures |
| Cache layer | Redis (Spring Cache) | Job/profile data changes infrequently; reduces load on upstream services |
| Auth model | Client credentials (service-to-service) | No user context flows to MCP; service acts on behalf of the platform |
| Token injection | Per-session header (Python side) | langchain-mcp-adapters creates fresh sessions; token freshness guaranteed |
| Error contract | Typed JSON envelope from every tool | LLM can reason about errors; never surfaces raw stack traces |
| Tracing propagation | W3C `traceparent` (OTel) | Standard; works across Python httpx → Java WebClient |
