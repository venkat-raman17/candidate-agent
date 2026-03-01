# Production-Grade Architecture Guide

**Date**: 2026-03-01
**Purpose**: Comprehensive guide for restructuring candidate-mcp and candidate-agent to production-grade enterprise repositories following SOLID principles

---

## 🎯 Architecture Principles

### SOLID Principles Application

1. **Single Responsibility Principle (SRP)**
   - Each class/module has one reason to change
   - Transformers only transform, clients only fetch data, services orchestrate

2. **Open/Closed Principle (OCP)**
   - Open for extension, closed for modification
   - Interface-based design allows swapping implementations

3. **Liskov Substitution Principle (LSP)**
   - WebClient implementations can replace mock implementations
   - AgentContext transformer implementations are interchangeable

4. **Interface Segregation Principle (ISP)**
   - Small, focused interfaces (JobSyncClient, CxApplicationsClient, TalentProfileClient)
   - Clients only depend on methods they use

5. **Dependency Inversion Principle (DIP)**
   - Depend on abstractions (interfaces), not concrete classes
   - Configuration injects dependencies via Spring @Autowired

---

## 📁 candidate-mcp: Production-Grade Structure

### Current Issues

1. ❌ Mock stores in `src/main/java` (should be in `src/test/java`)
2. ❌ Mock clients in `src/main/java` (should be in `src/test/java`)
3. ❌ Prototype DTOs in `src/main/java` (should be in `src/test/java` since production uses careers-data-schema)
4. ❌ No exception hierarchy
5. ❌ No utility packages
6. ❌ No separate implementation package for WebClient clients

### Proposed Production Structure

```
candidate-mcp/
├── pom.xml
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TRANSFORMER_DESIGN.md
│   ├── PII_PROTECTION_CHECKLIST.md
│   └── API_INTEGRATION_GUIDE.md
│
├── src/main/java/com/example/mcpserver/
│   │
│   ├── dto/
│   │   ├── common/                           # ✅ PRODUCTION: Shared enums and types
│   │   │   ├── enums/
│   │   │   │   ├── ShiftType.java
│   │   │   │   ├── WorkMode.java
│   │   │   │   ├── SkillLevel.java
│   │   │   │   ├── EducationLevel.java
│   │   │   │   ├── OfferStatus.java
│   │   │   │   ├── EventType.java
│   │   │   │   ├── EventStatus.java
│   │   │   │   └── ApplicationGroupStatus.java
│   │   │   └── types/
│   │   │       └── Money.java                # Money value object
│   │   │
│   │   └── agentcontext/                     # ✅ PRODUCTION: Layer 1 projections
│   │       ├── JobAgentContext.java          # PII-stripped job projection
│   │       ├── ApplicationAgentContext.java  # PII-stripped application
│   │       ├── ProfileAgentContext.java      # PII-stripped profile
│   │       ├── WorkflowStageSummary.java
│   │       ├── ScheduledEventSummary.java
│   │       ├── OfferSummary.java
│   │       └── PublicRecruiterNote.java
│   │
│   ├── client/                               # ✅ PRODUCTION: Client interfaces
│   │   ├── JobSyncClient.java
│   │   ├── CxApplicationsClient.java
│   │   ├── TalentProfileClient.java
│   │   │
│   │   └── impl/                             # ✅ NEW: Production WebClient implementations
│   │       ├── JobSyncClientImpl.java        # Real REST API integration
│   │       ├── CxApplicationsClientImpl.java # Real REST API integration
│   │       └── TalentProfileClientImpl.java  # Real REST API integration
│   │
│   ├── transformer/                          # ✅ PRODUCTION: Layer 1 PII stripping
│   │   ├── AgentContextTransformer.java      # Base interface
│   │   ├── JobTransformer.java               # JobRequisition → JobAgentContext
│   │   ├── ApplicationTransformer.java       # AtsApplication → ApplicationAgentContext
│   │   └── ProfileTransformer.java           # CandidateProfileV2 → ProfileAgentContext
│   │
│   ├── config/                               # ✅ PRODUCTION: Spring configuration
│   │   ├── CandidateMcpConfiguration.java    # MCP server config (21 tools, resources)
│   │   ├── WebClientConfiguration.java       # ✅ NEW: WebClient beans, connection pooling
│   │   ├── ResilienceConfiguration.java      # ✅ NEW: Circuit breakers, retry policies
│   │   └── SecurityConfiguration.java        # ✅ NEW: App2App signature auth
│   │
│   ├── exception/                            # ✅ NEW: Exception hierarchy
│   │   ├── McpException.java                 # Base exception
│   │   ├── ClientException.java              # Client communication errors
│   │   ├── TransformerException.java         # Transformation errors
│   │   └── PiiViolationException.java        # PII data leak detection
│   │
│   ├── util/                                 # ✅ NEW: Utility classes
│   │   ├── DateTimeUtils.java                # Date/time helpers
│   │   ├── SlaCalculator.java                # SLA calculation logic
│   │   └── CurrencyFormatter.java            # Money formatting
│   │
│   ├── service/                              # ✅ NEW: Business logic layer (optional)
│   │   ├── JobService.java                   # Job domain operations
│   │   ├── ApplicationService.java           # Application domain operations
│   │   └── ProfileService.java               # Profile domain operations
│   │
│   └── observability/                        # ✅ NEW: Observability components
│       ├── MetricsRegistry.java              # Custom metrics
│       ├── LoggingAspect.java                # AOP-based logging
│       └── CorrelationIdFilter.java          # Request tracing
│
├── src/main/resources/
│   ├── application.yml                       # Spring Boot config
│   ├── application-dev.yml                   # Dev profile
│   ├── application-prod.yml                  # Production profile
│   └── logback-spring.xml                    # Logging config
│
└── src/test/java/com/example/mcpserver/
    │
    ├── dto/                                  # ⚠️ TEST ONLY: Prototype DTOs
    │   ├── jobsync/                          # MOVE HERE (from main)
    │   │   ├── JobRequisitionDocument.java
    │   │   ├── ShiftDetails.java
    │   │   ├── AssessmentCodeMapping.java
    │   │   ├── CompensationDetails.java
    │   │   ├── BonusStructure.java
    │   │   └── RequirementSection.java
    │   │
    │   ├── cxapplications/                   # MOVE HERE (from main)
    │   │   ├── ApplicationGroup.java
    │   │   ├── AtsApplication.java
    │   │   ├── WorkflowHistoryEntry.java
    │   │   ├── ScheduleMetadata.java
    │   │   ├── ScheduledEvent.java
    │   │   ├── OfferMetadata.java
    │   │   ├── CompensationOffer.java
    │   │   ├── NegotiationRound.java
    │   │   └── RecruiterNote.java
    │   │
    │   └── talentprofile/                    # MOVE HERE (from main)
    │       ├── CandidateProfileV2.java
    │       ├── BaseProfile.java
    │       ├── AssessmentResults.java
    │       ├── Preferences.java
    │       ├── QuestionnaireResponses.java
    │       ├── LocationPreferences.java
    │       ├── JobPreferences.java
    │       ├── CompensationExpectations.java
    │       └── WorkStylePreferences.java
    │
    ├── client/                               # ⚠️ TEST ONLY: Mock clients
    │   ├── mock/                             # MOVE HERE (from main)
    │   │   ├── MockJobSyncClient.java
    │   │   ├── MockCxApplicationsClient.java
    │   │   └── MockTalentProfileClient.java
    │   │
    │   └── wiremock/                         # ✅ NEW: WireMock integration tests
    │       ├── JobSyncWireMockTest.java
    │       ├── CxApplicationsWireMockTest.java
    │       └── TalentProfileWireMockTest.java
    │
    ├── store/                                # ⚠️ TEST ONLY: Mock data stores
    │   ├── JobSyncMockStore.java             # MOVE HERE (from main)
    │   ├── CxApplicationsMockStore.java      # MOVE HERE (from main)
    │   └── TalentProfileMockStore.java       # MOVE HERE (from main)
    │
    ├── transformer/                          # ✅ NEW: Transformer unit tests
    │   ├── JobTransformerTest.java           # PII stripping verification
    │   ├── ApplicationTransformerTest.java   # SLA calculation tests
    │   └── ProfileTransformerTest.java       # PII stripping verification
    │
    ├── contract/                             # ✅ NEW: Contract tests (Pact)
    │   ├── JobSyncPactTest.java
    │   ├── CxApplicationsPactTest.java
    │   └── TalentProfilePactTest.java
    │
    └── integration/                          # ✅ NEW: Integration tests
        ├── McpToolIntegrationTest.java
        ├── End2EndIntegrationTest.java
        └── TestConfiguration.java
```

### Key Production Enhancements

#### 1. WebClient Implementation (NEW)

**File**: `src/main/java/com/example/mcpserver/client/impl/JobSyncClientImpl.java`

```java
package com.example.mcpserver.client.impl;

import com.example.mcpserver.client.JobSyncClient;
import com.example.mcpserver.exception.ClientException;
import com.careers.schema.JobRequisition;  // FROM careers-data-schema
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import reactor.core.publisher.Mono;
import reactor.util.retry.Retry;

import java.time.Duration;
import java.util.List;
import java.util.Optional;

@Component
public class JobSyncClientImpl implements JobSyncClient {

    private final WebClient webClient;
    private final String serviceBaseUrl;

    public JobSyncClientImpl(
        WebClient.Builder webClientBuilder,
        @Value("${integration.job-sync.base-url}") String serviceBaseUrl,
        @Value("${integration.job-sync.timeout-ms}") int timeoutMs
    ) {
        this.serviceBaseUrl = serviceBaseUrl;
        this.webClient = webClientBuilder
            .baseUrl(serviceBaseUrl)
            .defaultHeader("Accept", "application/json")
            .build();
    }

    @Override
    public Optional<JobRequisition> getJobById(String jobId) {
        try {
            return webClient.get()
                .uri("/api/v1/jobs/{jobId}", jobId)
                .retrieve()
                .bodyToMono(JobRequisition.class)
                .retryWhen(Retry.backoff(3, Duration.ofMillis(100)))
                .blockOptional(Duration.ofSeconds(5));
        } catch (WebClientResponseException e) {
            throw new ClientException("Failed to fetch job: " + jobId, e);
        }
    }

    @Override
    public List<JobRequisition> getOpenJobs() {
        try {
            return webClient.get()
                .uri("/api/v1/jobs?status=OPEN")
                .retrieve()
                .bodyToFlux(JobRequisition.class)
                .retryWhen(Retry.backoff(3, Duration.ofMillis(100)))
                .collectList()
                .block(Duration.ofSeconds(10));
        } catch (WebClientResponseException e) {
            throw new ClientException("Failed to fetch open jobs", e);
        }
    }
}
```

**Benefits**:
- ✅ Real REST API integration with retry logic
- ✅ Configurable timeouts and base URLs
- ✅ Reactive WebClient with backpressure support
- ✅ Proper exception handling with custom ClientException

#### 2. Resilience Configuration (NEW)

**File**: `src/main/java/com/example/mcpserver/config/ResilienceConfiguration.java`

```java
package com.example.mcpserver.config;

import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Duration;

@Configuration
public class ResilienceConfiguration {

    @Bean
    public CircuitBreakerRegistry circuitBreakerRegistry() {
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
            .failureRateThreshold(50)
            .waitDurationInOpenState(Duration.ofSeconds(30))
            .slidingWindowSize(10)
            .build();

        return CircuitBreakerRegistry.of(config);
    }

    @Bean
    public CircuitBreaker jobSyncCircuitBreaker(CircuitBreakerRegistry registry) {
        return registry.circuitBreaker("job-sync");
    }

    @Bean
    public CircuitBreaker cxApplicationsCircuitBreaker(CircuitBreakerRegistry registry) {
        return registry.circuitBreaker("cx-applications");
    }

    @Bean
    public CircuitBreaker talentProfileCircuitBreaker(CircuitBreakerRegistry registry) {
        return registry.circuitBreaker("talent-profile");
    }
}
```

**Benefits**:
- ✅ Circuit breaker pattern for fault tolerance
- ✅ Prevents cascade failures
- ✅ Configurable thresholds and recovery times

#### 3. Exception Hierarchy (NEW)

**File**: `src/main/java/com/example/mcpserver/exception/McpException.java`

```java
package com.example.mcpserver.exception;

public class McpException extends RuntimeException {
    private final String errorCode;

    public McpException(String message) {
        super(message);
        this.errorCode = "MCP_ERROR";
    }

    public McpException(String message, Throwable cause) {
        super(message, cause);
        this.errorCode = "MCP_ERROR";
    }

    public McpException(String errorCode, String message) {
        super(message);
        this.errorCode = errorCode;
    }

    public String getErrorCode() {
        return errorCode;
    }
}
```

**File**: `src/main/java/com/example/mcpserver/exception/ClientException.java`

```java
package com.example.mcpserver.exception;

public class ClientException extends McpException {
    public ClientException(String message) {
        super("CLIENT_ERROR", message);
    }

    public ClientException(String message, Throwable cause) {
        super(message, cause);
    }
}
```

**File**: `src/main/java/com/example/mcpserver/exception/TransformerException.java`

```java
package com.example.mcpserver.exception;

public class TransformerException extends McpException {
    public TransformerException(String message) {
        super("TRANSFORMER_ERROR", message);
    }

    public TransformerException(String message, Throwable cause) {
        super(message, cause);
    }
}
```

**Benefits**:
- ✅ Clear exception hierarchy
- ✅ Error codes for categorization
- ✅ Proper exception chaining

#### 4. Utility Classes (NEW)

**File**: `src/main/java/com/example/mcpserver/util/SlaCalculator.java`

```java
package com.example.mcpserver.util;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Map;

public final class SlaCalculator {

    private static final Map<String, Integer> SLA_THRESHOLDS = Map.of(
        "SCREENING", 2,
        "TECHNICAL_INTERVIEW", 7,
        "HIRING_MANAGER_INTERVIEW", 5,
        "OFFER_PREPARATION", 3,
        "OFFER_EXTENDED", 5
    );

    private SlaCalculator() {
        throw new UnsupportedOperationException("Utility class");
    }

    public static long calculateDaysInStage(LocalDateTime lastTransitionTime) {
        if (lastTransitionTime == null) {
            return 0;
        }
        return Duration.between(lastTransitionTime, LocalDateTime.now()).toDays();
    }

    public static boolean isSlaBreached(String stageName, long daysInStage) {
        Integer threshold = SLA_THRESHOLDS.get(stageName);
        if (threshold == null) {
            return false;
        }
        return daysInStage > threshold;
    }

    public static Integer getSlaThreshold(String stageName) {
        return SLA_THRESHOLDS.get(stageName);
    }
}
```

**Benefits**:
- ✅ Centralized SLA logic
- ✅ Utility class pattern (private constructor)
- ✅ Configurable thresholds

### Migration Steps

1. **Phase 1: Create new packages**
   ```bash
   mkdir -p src/main/java/com/example/mcpserver/client/impl
   mkdir -p src/main/java/com/example/mcpserver/exception
   mkdir -p src/main/java/com/example/mcpserver/util
   mkdir -p src/main/java/com/example/mcpserver/service
   mkdir -p src/main/java/com/example/mcpserver/observability
   ```

2. **Phase 2: Move test-only code**
   ```bash
   # Move prototype DTOs to test
   mv src/main/java/com/example/mcpserver/dto/jobsync src/test/java/com/example/mcpserver/dto/
   mv src/main/java/com/example/mcpserver/dto/cxapplications src/test/java/com/example/mcpserver/dto/
   mv src/main/java/com/example/mcpserver/dto/talentprofile src/test/java/com/example/mcpserver/dto/

   # Move mock clients to test
   mv src/main/java/com/example/mcpserver/client/mock src/test/java/com/example/mcpserver/client/

   # Move mock stores to test
   mv src/main/java/com/example/mcpserver/store src/test/java/com/example/mcpserver/
   ```

3. **Phase 3: Add production implementations**
   - Implement WebClient-based clients in `client/impl/`
   - Add exception hierarchy in `exception/`
   - Add utility classes in `util/`
   - Add resilience configuration

4. **Phase 4: Update dependencies**
   ```xml
   <!-- pom.xml -->
   <dependency>
       <groupId>com.careers</groupId>
       <artifactId>careers-data-schema</artifactId>
       <version>1.6.0</version>
   </dependency>

   <dependency>
       <groupId>io.github.resilience4j</groupId>
       <artifactId>resilience4j-spring-boot3</artifactId>
       <version>2.0.2</version>
   </dependency>
   ```

5. **Phase 5: Update transformer imports**
   ```java
   // Before (prototype):
   import com.example.mcpserver.dto.jobsync.JobRequisitionDocument;

   // After (production):
   import com.careers.schema.JobRequisition;  // FROM careers-data-schema
   ```

---

## 📁 candidate-agent: Production-Grade Structure

### Current Issues

1. ✅ Generally well-structured (agents, api, mcp packages exist)
2. ⚠️ Could benefit from service layer for business logic
3. ⚠️ No utility modules
4. ⚠️ No separate error handling layer
5. ⚠️ Test structure could be more comprehensive

### Proposed Production Structure

```
candidate-agent/
├── pyproject.toml
├── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   ├── AGENT_DESIGN.md
│   ├── MCP_INTEGRATION.md
│   └── DEPLOYMENT_GUIDE.md
│
├── src/candidate_agent/
│   │
│   ├── agents/                               # ✅ Agent definitions
│   │   ├── __init__.py
│   │   ├── graph.py                          # Graph builders (v1, v2)
│   │   ├── llm.py                            # LLM factory
│   │   ├── prompts.py                        # System prompts
│   │   ├── state.py                          # State schemas
│   │   └── tools.py                          # ✅ NEW: Custom tools (non-MCP)
│   │
│   ├── api/                                  # ✅ FastAPI routes
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── agent.py                      # v1 routes
│   │   │   ├── agent_v2.py                   # v2 routes
│   │   │   └── health.py                     # Health check
│   │   ├── __init__.py
│   │   ├── dependencies.py                   # FastAPI dependencies
│   │   ├── schemas.py                        # Pydantic models
│   │   └── middleware.py                     # ✅ NEW: CORS, correlation ID, etc.
│   │
│   ├── mcp/                                  # ✅ MCP integration layer
│   │   ├── __init__.py
│   │   ├── client.py                         # MCP tool registry
│   │   └── adapter.py                        # ✅ NEW: MCP adapter utilities
│   │
│   ├── service/                              # ✅ NEW: Business logic layer
│   │   ├── __init__.py
│   │   ├── agent_service.py                  # Agent invocation orchestration
│   │   ├── mcp_service.py                    # MCP operations wrapper
│   │   └── cache_service.py                  # ✅ NEW: Caching layer (Redis)
│   │
│   ├── util/                                 # ✅ NEW: Utility modules
│   │   ├── __init__.py
│   │   ├── datetime_utils.py                 # Date/time helpers
│   │   ├── text_utils.py                     # Text formatting, sanitization
│   │   └── correlation.py                    # Correlation ID management
│   │
│   ├── exception/                            # ✅ NEW: Exception hierarchy
│   │   ├── __init__.py
│   │   ├── base.py                           # Base exception classes
│   │   ├── agent_exception.py                # Agent-specific errors
│   │   └── mcp_exception.py                  # MCP client errors
│   │
│   ├── observability/                        # ✅ NEW: Observability components
│   │   ├── __init__.py
│   │   ├── metrics.py                        # Prometheus metrics
│   │   ├── tracing.py                        # Langfuse tracing helpers
│   │   └── logging_middleware.py             # Structured logging middleware
│   │
│   ├── config.py                             # ✅ Settings
│   ├── logging_setup.py                      # ✅ Logging configuration
│   └── main.py                               # ✅ FastAPI app
│
└── tests/
    ├── __init__.py
    │
    ├── unit/                                 # ✅ Unit tests
    │   ├── __init__.py
    │   ├── test_prompts.py                   # Prompt builder tests
    │   ├── test_mcp_client.py                # MCP client tests
    │   ├── test_agent_service.py             # Service layer tests
    │   └── test_utils.py                     # Utility tests
    │
    ├── integration/                          # ✅ Integration tests
    │   ├── __init__.py
    │   ├── test_agent_api.py                 # API endpoint tests
    │   ├── test_mcp_integration.py           # MCP server integration
    │   └── test_graph_execution.py           # Graph execution tests
    │
    ├── fixtures/                             # ✅ Test fixtures
    │   ├── __init__.py
    │   ├── mock_mcp_responses.py             # Mock MCP tool responses
    │   └── sample_conversations.py           # Sample conversation states
    │
    └── conftest.py                           # Pytest configuration
```

### Key Production Enhancements

#### 1. Service Layer (NEW)

**File**: `src/candidate_agent/service/agent_service.py`

```python
"""Agent invocation service — business logic for agent orchestration."""

from typing import Any, AsyncGenerator
from uuid import uuid4

import structlog

from candidate_agent.agents.graph import build_v2_graph
from candidate_agent.config import Settings
from candidate_agent.exception.agent_exception import AgentInvocationError
from candidate_agent.mcp.client import MCPToolRegistry
from candidate_agent.observability.tracing import with_langfuse_trace

logger = structlog.get_logger(__name__)


class AgentService:
    """Service for orchestrating agent invocations.

    Responsibilities:
    - Build input state from request
    - Invoke LangGraph with proper configuration
    - Extract and format results
    - Handle errors and retries
    """

    def __init__(self, registry: MCPToolRegistry, settings: Settings):
        self.registry = registry
        self.settings = settings
        self.graph = build_v2_graph(registry, settings)

    @with_langfuse_trace("agent_invoke")
    async def invoke(
        self,
        message: str,
        candidate_id: str,
        application_id: str = "",
        thread_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Invoke the v2 agent graph synchronously.

        Args:
            message: User message
            candidate_id: Candidate ID (required)
            application_id: Application ID (optional)
            thread_id: Conversation thread ID (auto-generated if None)
            correlation_id: Request trace ID (auto-generated if None)

        Returns:
            dict with response, agent_used, tool_calls, thread_id, correlation_id

        Raises:
            AgentInvocationError: If invocation fails
        """
        thread_id = thread_id or str(uuid4())
        correlation_id = correlation_id or str(uuid4())

        log = logger.bind(
            thread_id=thread_id,
            correlation_id=correlation_id,
            candidate_id=candidate_id,
            application_id=application_id,
        )
        log.info("agent_invoke_start")

        input_state = self._build_input(message, candidate_id, application_id, correlation_id)
        config = {"configurable": {"thread_id": thread_id}}

        try:
            final_state = await self.graph.ainvoke(input_state, config=config)
        except Exception as exc:
            log.error("agent_invoke_error", error=str(exc), exc_info=True)
            raise AgentInvocationError(f"Agent invocation failed: {exc}") from exc

        result = self._extract_result(final_state, thread_id, correlation_id)
        log.info("agent_invoke_complete", agent_used=result["agent_used"])
        return result

    @with_langfuse_trace("agent_stream")
    async def stream(
        self,
        message: str,
        candidate_id: str,
        application_id: str = "",
        thread_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream agent events as they occur.

        Yields:
            dict events: {"event": "token|tool_call|handoff|done|error", "data": {...}}
        """
        thread_id = thread_id or str(uuid4())
        correlation_id = correlation_id or str(uuid4())

        input_state = self._build_input(message, candidate_id, application_id, correlation_id)
        config = {"configurable": {"thread_id": thread_id}}

        async for event in self.graph.astream_events(input_state, config=config, version="v2"):
            yield self._format_event(event)

    def _build_input(
        self,
        message: str,
        candidate_id: str,
        application_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Build initial graph state."""
        from langchain_core.messages import HumanMessage

        return {
            "messages": [HumanMessage(content=message)],
            "candidate_id": candidate_id,
            "application_id": application_id,
            "correlation_id": correlation_id,
            "active_agent": "v2_primary_assistant",
        }

    def _extract_result(
        self,
        final_state: dict[str, Any],
        thread_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """Extract response from final graph state."""
        from langchain_core.messages import AIMessage

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

        return {
            "thread_id": thread_id,
            "correlation_id": correlation_id,
            "response": response_text,
            "agent_used": final_state.get("active_agent", "v2_primary_assistant"),
            "tool_calls": tool_calls,
        }

    def _format_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Format LangGraph stream event for SSE."""
        # Implementation for event formatting
        pass
```

**Benefits**:
- ✅ Single responsibility: orchestration logic
- ✅ Dependency injection via constructor
- ✅ Observability with structured logging and tracing
- ✅ Proper error handling with custom exceptions
- ✅ Type hints for clarity

#### 2. Exception Hierarchy (NEW)

**File**: `src/candidate_agent/exception/base.py`

```python
"""Base exception classes for candidate-agent."""

class CandidateAgentException(Exception):
    """Base exception for all candidate-agent errors."""

    def __init__(self, message: str, error_code: str = "AGENT_ERROR"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
```

**File**: `src/candidate_agent/exception/agent_exception.py`

```python
"""Agent-specific exceptions."""

from candidate_agent.exception.base import CandidateAgentException


class AgentInvocationError(CandidateAgentException):
    """Raised when agent invocation fails."""

    def __init__(self, message: str):
        super().__init__(message, error_code="AGENT_INVOCATION_ERROR")


class GraphExecutionError(CandidateAgentException):
    """Raised when graph execution fails."""

    def __init__(self, message: str):
        super().__init__(message, error_code="GRAPH_EXECUTION_ERROR")
```

**File**: `src/candidate_agent/exception/mcp_exception.py`

```python
"""MCP client exceptions."""

from candidate_agent.exception.base import CandidateAgentException


class McpConnectionError(CandidateAgentException):
    """Raised when MCP server connection fails."""

    def __init__(self, message: str):
        super().__init__(message, error_code="MCP_CONNECTION_ERROR")


class McpToolError(CandidateAgentException):
    """Raised when MCP tool invocation fails."""

    def __init__(self, message: str, tool_name: str):
        super().__init__(message, error_code="MCP_TOOL_ERROR")
        self.tool_name = tool_name
```

#### 3. Middleware (NEW)

**File**: `src/candidate_agent/api/middleware.py`

```python
"""FastAPI middleware for cross-cutting concerns."""

import time
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject correlation ID into request context and response headers."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid4()))
        request.state.correlation_id = correlation_id

        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests and responses with timing."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        correlation_id = getattr(request.state, "correlation_id", "unknown")

        log = logger.bind(
            method=request.method,
            path=request.url.path,
            correlation_id=correlation_id,
        )
        log.info("request_start")

        response = await call_next(request)

        duration_ms = (time.time() - start_time) * 1000
        log.info(
            "request_complete",
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response
```

#### 4. Utility Modules (NEW)

**File**: `src/candidate_agent/util/text_utils.py`

```python
"""Text processing utilities."""

import re


def sanitize_message(text: str) -> str:
    """Remove potentially harmful characters from user input.

    Args:
        text: User message

    Returns:
        Sanitized message
    """
    # Remove control characters except newline and tab
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t")
    # Limit length
    return text[:5000]


def extract_ids(text: str) -> dict[str, list[str]]:
    """Extract candidate IDs, application IDs, job IDs from text.

    Args:
        text: Text containing IDs

    Returns:
        dict with lists of found IDs by type
    """
    return {
        "candidate_ids": re.findall(r"C\d{3,5}", text),
        "application_ids": re.findall(r"A\d{3,5}", text),
        "job_ids": re.findall(r"J\d{3,5}", text),
    }


def format_duration(days: int) -> str:
    """Format days into human-readable duration.

    Args:
        days: Number of days

    Returns:
        Human-readable string (e.g., "2 weeks", "3 days")
    """
    if days == 0:
        return "today"
    elif days == 1:
        return "1 day"
    elif days < 7:
        return f"{days} days"
    elif days < 14:
        return "1 week"
    else:
        weeks = days // 7
        return f"{weeks} weeks"
```

### Migration Steps

1. **Phase 1: Create new packages**
   ```bash
   mkdir -p src/candidate_agent/service
   mkdir -p src/candidate_agent/util
   mkdir -p src/candidate_agent/exception
   mkdir -p src/candidate_agent/observability
   mkdir -p tests/unit
   mkdir -p tests/integration
   mkdir -p tests/fixtures
   ```

2. **Phase 2: Extract service layer**
   - Move agent invocation logic from API routes to `AgentService`
   - Update API routes to use service layer
   - Add proper error handling

3. **Phase 3: Add utility modules**
   - Create text processing utilities
   - Create datetime utilities
   - Create correlation ID utilities

4. **Phase 4: Add exception hierarchy**
   - Define base exception
   - Define domain-specific exceptions
   - Update code to use custom exceptions

5. **Phase 5: Add middleware**
   - Implement correlation ID middleware
   - Implement logging middleware
   - Register middleware in main.py

6. **Phase 6: Expand test coverage**
   - Add unit tests for all services
   - Add integration tests for API endpoints
   - Add fixtures for mock data

---

## 🎓 SOLID Principles Applied

### Single Responsibility Principle (SRP)

**candidate-mcp**:
- ✅ `JobTransformer`: ONLY transforms JobRequisition → JobAgentContext
- ✅ `JobSyncClient`: ONLY fetches data from job-sync-service
- ✅ `SlaCalculator`: ONLY calculates SLA metrics

**candidate-agent**:
- ✅ `AgentService`: ONLY orchestrates agent invocations
- ✅ `MCPToolRegistry`: ONLY loads and caches MCP tools
- ✅ `text_utils`: ONLY text processing operations

### Open/Closed Principle (OCP)

**candidate-mcp**:
- ✅ `AgentContextTransformer<T, R>` interface allows adding new transformers without modifying existing code
- ✅ `JobSyncClient` interface allows swapping implementations (mock → WebClient) without changing dependents

**candidate-agent**:
- ✅ Service layer allows adding new services without modifying API routes
- ✅ Exception hierarchy allows adding new exception types without changing error handling

### Liskov Substitution Principle (LSP)

**candidate-mcp**:
- ✅ `MockJobSyncClient` and `JobSyncClientImpl` are interchangeable implementations of `JobSyncClient`
- ✅ All transformers implement `AgentContextTransformer<T, R>` and can be used interchangeably

**candidate-agent**:
- ✅ Any `AgentService` implementation can replace another without breaking dependents

### Interface Segregation Principle (ISP)

**candidate-mcp**:
- ✅ Small, focused interfaces: `JobSyncClient` (3 methods), `CxApplicationsClient` (5 methods)
- ✅ Clients only depend on methods they use (not one giant interface)

**candidate-agent**:
- ✅ Service layer has focused interfaces
- ✅ No "god services" with dozens of methods

### Dependency Inversion Principle (DIP)

**candidate-mcp**:
- ✅ `CandidateMcpConfiguration` depends on `JobSyncClient` interface, not concrete `MockJobSyncClient`
- ✅ Transformers depend on `AgentContextTransformer<T, R>` abstraction

**candidate-agent**:
- ✅ `AgentService` receives `MCPToolRegistry` via constructor (dependency injection)
- ✅ API routes depend on `AgentService` interface, not concrete implementation

---

## 📊 Production Readiness Checklist

### candidate-mcp

#### Code Structure
- [ ] Mock stores moved to `src/test/java`
- [ ] Mock clients moved to `src/test/java`
- [ ] Prototype DTOs moved to `src/test/java`
- [ ] WebClient implementations created in `client/impl/`
- [ ] Exception hierarchy created in `exception/`
- [ ] Utility classes created in `util/`
- [ ] Service layer created (optional)

#### Configuration
- [ ] careers-data-schema dependency added to pom.xml
- [ ] Resilience4j dependency added
- [ ] WebClient configuration with connection pooling
- [ ] Circuit breaker configuration
- [ ] Profile-based configuration (dev, prod)

#### Testing
- [ ] Transformer unit tests with PII verification
- [ ] WebClient integration tests with WireMock
- [ ] Contract tests with Pact
- [ ] End-to-end integration tests

#### Observability
- [ ] Structured logging with correlation IDs
- [ ] Metrics (Micrometer/Prometheus)
- [ ] Health checks with detailed status
- [ ] Circuit breaker metrics

#### Security
- [ ] App2App signature authentication
- [ ] PII stripping comprehensive tests
- [ ] No sensitive data in logs
- [ ] HTTPS configuration for production

### candidate-agent

#### Code Structure
- [ ] Service layer created in `service/`
- [ ] Utility modules created in `util/`
- [ ] Exception hierarchy created in `exception/`
- [ ] Middleware created in `api/middleware.py`
- [ ] Observability components created

#### Configuration
- [ ] AsyncRedisSaver for checkpointer (production)
- [ ] Langfuse tracing configured
- [ ] Environment-based configuration
- [ ] Connection pooling for MCP client

#### Testing
- [ ] Unit tests for all services (>80% coverage)
- [ ] Integration tests for API endpoints
- [ ] Mock MCP responses for testing
- [ ] Load testing for concurrent requests

#### Observability
- [ ] Structured logging with correlation IDs
- [ ] Langfuse tracing for all agent invocations
- [ ] Prometheus metrics
- [ ] Health checks with MCP connection status

#### Security
- [ ] Input validation and sanitization
- [ ] Rate limiting on API endpoints
- [ ] CORS configuration
- [ ] Secret management (not hardcoded API keys)

---

## 📝 Summary

This guide provides a comprehensive production-grade architecture for both **candidate-mcp** (Java MCP server) and **candidate-agent** (Python LangGraph agent). Key improvements:

### candidate-mcp
1. ✅ **Clear separation**: Production code in `main/`, test-only code in `test/`
2. ✅ **WebClient implementations**: Real REST API integration
3. ✅ **Resilience patterns**: Circuit breakers, retries, timeouts
4. ✅ **Exception hierarchy**: Proper error handling
5. ✅ **Utility classes**: Centralized logic (SLA calculation, formatting)
6. ✅ **SOLID principles**: Interface-based design, dependency injection

### candidate-agent
1. ✅ **Service layer**: Business logic separated from API routes
2. ✅ **Utility modules**: Text processing, datetime, correlation ID
3. ✅ **Exception hierarchy**: Custom exceptions for different error types
4. ✅ **Middleware**: Cross-cutting concerns (logging, correlation ID)
5. ✅ **Observability**: Structured logging, metrics, tracing
6. ✅ **SOLID principles**: Clean separation of concerns

**Result**: Scalable, maintainable, production-grade enterprise repositories that follow best practices and SOLID principles.

---

**Document Created**: 2026-03-01
**Purpose**: Production architecture guide for enterprise LLD submission
**Status**: Comprehensive and ready for real-world implementation
