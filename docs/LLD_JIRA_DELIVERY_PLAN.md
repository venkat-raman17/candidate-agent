# Low Level Design
## post_apply_assistant Delivery Plan (Jira Stories + LOE)

| Field | Detail |
|---|---|
| **Document Version** | 1.0 |
| **Status** | Draft |
| **Last Updated** | 2026-03-03 |
| **Primary Input** | `LLD_POST_APPLY_ASSISTANT.md` |
| **In Scope** | Backend delivery plan for `careers-ai-service` and `candidate-mcp` |
| **Out of Scope** | UI implementation (handled by separate UI team) |

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Scope Boundaries](#2-scope-boundaries)
3. [Team Model and Working Agreement](#3-team-model-and-working-agreement)
4. [Epic Breakdown](#4-epic-breakdown)
5. [Jira Story List](#5-jira-story-list)
6. [Effort and Timeline](#6-effort-and-timeline)
7. [Suggested Sprint Plan (2 Developers)](#7-suggested-sprint-plan-2-developers)
8. [Definition of Done](#8-definition-of-done)
9. [Risks and Mitigations](#9-risks-and-mitigations)

---

## 1. Purpose

This document translates the existing post-apply assistant LLD into a Jira-ready
delivery plan for two backend developers:

- **Dev A**: Python-strong (can work across repos)
- **Dev B**: Java-strong (can work across repos)

The goal is to provide:
- A practical story backlog
- Clear ownership with cross-repo collaboration
- Level-of-effort estimates in story points and dev-days
- A realistic sprint sequence

---

## 2. Scope Boundaries

### 2.1 Included

- `careers-ai-service` v2 routes and v2 LangGraph flow
- `post_apply_assistant` behavior, guardrails, and tool usage
- `candidate-mcp` tools, downstream clients, transformers, and schema resources
- App2App signature auth on both hops
- Resilience, observability, caching, and error handling
- Unit, integration, contract, and E2E backend tests

### 2.2 Excluded

- Frontend/UI work (`cx-web`) and UI tickets
- v1 route migration or retirement
- Non-LLD feature expansions

---

## 3. Team Model and Working Agreement

### 3.1 Ownership Pattern

- Each story has a **Lead Owner** and optional **Support Reviewer**.
- Repo expertise guides ownership, but does not restrict contribution.
- Cross-repo pairing is intentional for contract-heavy stories.

### 3.2 Capacity Assumptions

- Sprint length: **2 weeks**
- Effective capacity per developer: **9–11 SP/sprint**
- Team velocity (2 devs): **18–22 SP/sprint**
- 1 SP ≈ **0.6–0.9 dev-day** (blended backend complexity)

---

## 4. Epic Breakdown

| Epic ID | Epic Name | Target Repos |
|---|---|---|
| EPA | Python v2 Agent Foundation | `candidate-agent` |
| EPB | Java MCP Tools & Contracts | `candidate-mcp` |
| EPC | Security & Auth | Both |
| EPD | Resilience, Performance, Caching | Both |
| EPE | Observability & Operations | Both |
| EPF | Error Semantics & Guardrails | Both |
| EPG | Test Matrix & Release Readiness | Both |

---

## 5. Jira Story List (4-Week Timebox)

> The original 22-story plan is reduced to a 4-week MVP backlog for 2 developers.
> Key format below is `PAA-4W-##`.

### 5.1 Committed Stories (Must Ship in 4 Weeks)

| Key | Epic | Story | Lead | Support | Repo(s) | Depends On | SP | Est. Dev-Days |
|---|---|---|---|---|---|---|---:|---:|
| PAA-4W-01 | EPA | Add `/api/v2/agent/invoke` and `/api/v2/agent/stream` routes with dedicated v2 graph wiring | Dev A | Dev B | candidate-agent | - | 3 | 2 |
| PAA-4W-02 | EPA | Implement `primary_assistant_2` handoff + callable prompt context injection (`talent_profile_id`, `ats_requisition_id`) | Dev A | Dev B | candidate-agent | PAA-4W-01 | 8 | 6 |
| PAA-4W-03 | EPA | Implement MCP startup integration (registry init, tool load, post-apply tool filter) | Dev A | Dev B | candidate-agent | PAA-4W-01 | 3 | 2 |
| PAA-4W-04 | EPB | Implement `candidate-mcp` MVP tool slice: baseline wiring + `getActionableApplications`, `getApplicationDetails`, `getJobDetails` with Layer-1 projection | Dev B | Dev A | candidate-mcp | - | 8 | 6 |
| PAA-4W-05 | EPC | Implement App2App auth MVP on both hops (Python→MCP and MCP→downstream): signing + validation + TTL config | Dev B | Dev A | Both | PAA-4W-03,PAA-4W-04 | 8 | 6 |
| PAA-4W-06 | EPF | Implement guardrails + error mapping MVP: recursion cap, request timeout, tool-call cap, typed error envelope alignment | Dev A | Dev B | Both | PAA-4W-02,PAA-4W-04 | 5 | 4 |
| PAA-4W-07 | EPG | Java MVP tests: tool handler tests + integration tests for auth and core tool flows | Dev B | Dev A | candidate-mcp | PAA-4W-04,PAA-4W-05 | 4 | 3 |
| PAA-4W-08 | EPG | Python MVP tests: v2 invoke/stream integration + happy path + degraded response E2E scenarios | Dev A | Dev B | candidate-agent | PAA-4W-02,PAA-4W-03,PAA-4W-06 | 5 | 4 |

### 5.2 Stories Modified or Merged from Original Plan

| Original Story | Action in 4-Week Plan |
|---|---|
| PAA-002 + PAA-003 | Merged into PAA-4W-02 |
| PAA-005 + PAA-007 + PAA-008 | Reduced/Merged into PAA-4W-04 |
| PAA-010 + PAA-011 + PAA-012 | Merged into PAA-4W-05 |
| PAA-014 + PAA-019 | Merged into PAA-4W-06 |
| PAA-020 (full scope) | Reduced into PAA-4W-07 |
| PAA-021 (full scope) | Reduced into PAA-4W-08 |

### 5.3 Deferred Stories (Deleted from Current 4-Week Release Scope)

| Deferred Original Story | Reason |
|---|---|
| PAA-006 | Profile/preference/assessment full domain slice deferred to Phase 2 |
| PAA-009 | Schema resource publication deferred |
| PAA-013 | Full resilience policy tuning deferred (MVP uses minimal safe defaults) |
| PAA-015 | HTTP/2 shared pool/TLS optimization deferred |
| PAA-016 | Redis single-flight lock hardening deferred |
| PAA-017 | Python advanced observability deferred |
| PAA-018 | Java advanced observability deferred |
| PAA-022 | Full cross-service contract gating deferred |

### 5.4 Suggested Acceptance Criteria Pattern (apply per story)

- Code merged with passing CI in touched repo(s)
- Backward compatibility preserved for v1 paths
- Structured logs include `correlation_id` / trace metadata
- Security-sensitive flows validated (when applicable)
- Tests added/updated for changed behavior
- Documentation updated for runtime config and ops behavior

---

## 6. Effort and Timeline

### 6.1 Total Estimated Effort

- **Total committed stories**: 8
- **Total committed scope**: **44 SP**
- **Total engineering effort**: **~33 dev-days**

### 6.2 Expected Delivery Window (2 Developers)

For a fixed **4-week window** (2 sprints), target team velocity is:

- **22 SP per sprint** (upper bound of expected range)
- **44 SP total** across 2 sprints

Practical recommendation:

- Scope freeze after Sprint 1 planning; only swap stories if blocked by external dependency
- Reserve ~10% time inside Sprint 2 for integration defect burn-down

---

## 7. Suggested Sprint Plan (2 Developers)

### Sprint 1 (Weeks 1–2) — Build Vertical Slice (Target 22 SP)

- Dev A lead: PAA-4W-01, PAA-4W-02, PAA-4W-03
- Dev B lead: PAA-4W-04
- Shared: API/tool contract alignment and pair review

### Sprint 2 (Weeks 3–4) — Secure, Harden, Validate (Target 22 SP)

- Dev B lead: PAA-4W-05, PAA-4W-07
- Dev A lead: PAA-4W-06, PAA-4W-08
- Shared: end-to-end integration fixes and release checklist

### Stretch (Only if Ahead of Plan)

- Pull one deferred item: either PAA-015 (transport optimization) or PAA-017 (Python observability)

---

## 8. Definition of Done

A story is done only when:

1. Functional acceptance criteria are met.
2. Unit/integration tests pass in impacted repo(s).
3. Security and resilience checks are validated for that change.
4. Observability hooks (logs/metrics/traces) are present where applicable.
5. Documentation and configuration notes are updated.

---

## 9. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| MCP adapter extension point limitations for header injection | High | Time-box a spike in Week 1 under PAA-4W-05 and keep fallback transport wrapper ready |
| Contract drift between MCP tool outputs and Python expectations | Medium | Expand acceptance checks in PAA-4W-07 and PAA-4W-08 before release sign-off |
| Cross-service auth misconfiguration delays integration | Medium | Pair-review on PAA-4W-05 and validate in lower environment by end of Sprint 1 |
| Deferred hardening creates post-release reliability risk | Medium | Track deferred stories as Phase 2 backlog and pull one stretch item if capacity remains |

---

## Notes for Jira Setup

- Create one board with swimlanes by Epic (`EPA` to `EPG`).
- Add labels: `post-apply`, `v2-agent`, `candidate-mcp`, `security`, `resilience`, `observability`.
- Add components: `candidate-agent`, `candidate-mcp`, `shared-contracts`.
- Use `Blocked By` links exactly as listed in the story dependency column.
