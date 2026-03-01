# Mock Data & Test Prompts

**Date**: 2026-03-01
**Status**: ✅ Comprehensive Mock Data Added
**Purpose**: End-to-end testing guide with realistic enterprise scenarios

---

## Mock Data Overview

The candidate-mcp server now contains **comprehensive mock data** for all three enterprise services:

### 1. JobSync Mock Data (8 Jobs)

| Job ID | Title | Department | Shift | Status | Target Candidates |
|---|---|---|---|---|---|
| **J001** | Senior SRE | Engineering - SRE | DAY | OPEN | C001, C004 |
| **J002** | Senior Frontend (React) | Engineering - Frontend | FLEXIBLE | OPEN | C001, C005 |
| **J003** | Data Engineer (ETL) | Data Engineering | NIGHT | OPEN | - |
| **J004** | DevOps Engineer | Engineering - DevOps | ROTATING | OPEN | C001 |
| **J005** | Junior Backend | Engineering - Backend | DAY | CLOSED | C002 |
| **J006** | Customer Support | Customer Success | ON_CALL | OPEN | - |
| **J007** | ML Engineer | AI/ML | DAY | DRAFT | - |
| **J008** | Security Engineer | Security | FLEXIBLE | OPEN | C003 (hired) |

### 2. CxApplications Mock Data (7 Applications + 3 Application Groups)

#### Applications

| App ID | Candidate | Job | Status | Key Feature |
|---|---|---|---|---|
| **A001** | C001 | J001 (SRE) | TECHNICAL_INTERVIEW | Has 2 upcoming interviews scheduled |
| **A002** | C001 | J002 (Frontend) | OFFER_EXTENDED | Offer pending (expires in 4 days) |
| **A003** | C001 | J003 (Data Eng) | REJECTED | Shift incompatibility |
| **A004** | C002 | J005 (Junior) | SCREENING | Early stage, new grad |
| **A005** | C003 | J008 (Security) | HIRED | Complete journey with offer negotiation |
| **A006** | C004 | J001 (SRE) | SCREENING | **SLA BREACHED** (12 days in screening) |
| **A007** | C005 | J002 (Frontend) | WITHDRAWN | Accepted offer elsewhere |

#### Application Groups

| Group ID | Candidate | Jobs | Status | Completion |
|---|---|---|---|---|
| **AG001** | C002 | [J001, J002, J004] | DRAFT | 60% |
| **AG002** | C001 | [J004, J008] | SUBMITTED | 100% |
| **AG003** | C003 | [J001, J004] | ABANDONED | 25% (35 days idle) |

### 3. TalentProfile Mock Data (5 Candidates)

| Candidate | Name | Experience | Role | Key Skills | Status |
|---|---|---|---|---|---|
| **C001** | Alex Thompson | 7 years | Senior SRE | Kubernetes, Python, Terraform | ACTIVE |
| **C002** | Maya Patel | 0 years (new grad) | MIT CS Graduate | Java, Python, Algorithms | ACTIVE |
| **C003** | Jordan Rivera | 8 years | Senior Security Eng | AppSec, Pen Testing, OSCP | HIRED |
| **C004** | Chris Martinez | 5 years | Backend Engineer | Python, Django, PostgreSQL | ACTIVE |
| **C005** | Taylor Kim | 6 years | Senior Frontend | React, TypeScript, Design Systems | WITHDRAWN |

---

## Test Prompts by Scenario

### Scenario 1: Profile Information (Candidate C001)

**Test Candidate**: C001 (Alex Thompson - Senior SRE, 7 years experience)

#### Prompt 1.1: Basic Profile Query
```bash
curl -X POST "http://localhost:8000/api/v2/agent/invoke" \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "test-001",
    "correlation_id": "corr-001",
    "candidate_id": "C001",
    "message": "Tell me about my profile and experience"
  }'
```

**Expected Response**: Should mention 7 years experience, Senior SRE role at CloudScale Inc, Kubernetes/Python expertise, Berkeley CS degree

#### Prompt 1.2: Skills Gap Analysis
```json
curl -X POST "http://localhost:8000/api/v2/agent/invoke" \
  -H "Content-Type: application/json" \
  -d '{
    "thread_id": "test-002",
    "correlation_id": "corr-002",
    "candidate_id": "C001",
    "application_id": "A001",
    "message": "What skills do I need to improve for the Senior SRE job I applied to?"
  }'
```

**Expected Response**: Should compare C001's skills (Kubernetes 95th percentile, Python 82nd percentile, System Design 90th percentile) against J001 requirements (KUBERNETES_03, SYS_DESIGN_02, PYTHON_04)

#### Prompt 1.3: Candidate Preferences
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-003",
  "correlation_id": "corr-003",
  "candidate_id": "C001",
  "message": "What are my job preferences and location requirements?"
}
```

**Expected Response**: Should mention preferred locations (San Francisco, Seattle, Austin), open to relocation, prefers hybrid work (2 days onsite), accepts DAY or FLEXIBLE shifts, willing to be on-call

---

### Scenario 2: Application Status - Multiple Applications (Candidate C001)

**Test Candidate**: C001 (Has 3 applications: technical interview, offer extended, rejected)

#### Prompt 2.1: All Applications Overview
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-004",
  "correlation_id": "corr-004",
  "candidate_id": "C001",
  "message": "Show me all my applications and their current status"
}
```

**Expected Response**: Should list 3 applications:
- A001 (J001 Senior SRE): TECHNICAL_INTERVIEW stage
- A002 (J002 Frontend): OFFER_EXTENDED (pending response)
- A003 (J003 Data Engineer): REJECTED (shift incompatibility)

#### Prompt 2.2: Specific Application Status
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-005",
  "correlation_id": "corr-005",
  "candidate_id": "C001",
  "application_id": "A001",
  "message": "What's the status of my Senior SRE application?"
}
```

**Expected Response**: Should mention TECHNICAL_INTERVIEW stage, 4 days in current stage (SLA healthy), last transition from phone interview

#### Prompt 2.3: Application with Offer
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-006",
  "correlation_id": "corr-006",
  "candidate_id": "C001",
  "application_id": "A002",
  "message": "Tell me about my Frontend Engineer offer"
}
```

**Expected Response**: Should mention:
- Offer extended 3 days ago
- $155k base + $10k signing bonus + 5k shares (~$75k equity)
- Expires in 4 days
- Benefits: Health insurance, 401k match, unlimited PTO, remote work stipend

---

### Scenario 3: Interview Schedule (Candidate C001, Application A001)

**Test Candidate**: C001 - Application A001 has 2 upcoming interviews

#### Prompt 3.1: Upcoming Interviews
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-007",
  "correlation_id": "corr-007",
  "candidate_id": "C001",
  "application_id": "A001",
  "message": "When are my upcoming interviews?"
}
```

**Expected Response**: Should list:
- Technical Interview: 2 days from now at 2:00 PM (90 min) with Sarah Chen and David Park
- System Design: 3 days from now at 10:00 AM (60 min) with Dr. Lisa Zhang

#### Prompt 3.2: Interview Preparation
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-008",
  "correlation_id": "corr-008",
  "candidate_id": "C001",
  "application_id": "A001",
  "message": "What should I prepare for my technical interview?"
}
```

**Expected Response**: Should mention:
- Job requires KUBERNETES_03, SYS_DESIGN_02, PYTHON_04 assessments
- Candidate already passed KUBERNETES_03 (92/100, 95th percentile)
- 90-minute technical interview with 2 interviewers

---

### Scenario 4: New Graduate (Candidate C002)

**Test Candidate**: C002 (Maya Patel - MIT new grad, 0 years experience)

#### Prompt 4.1: Junior Role Application
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-009",
  "correlation_id": "corr-009",
  "candidate_id": "C002",
  "application_id": "A004",
  "message": "What's the status of my Junior Software Engineer application?"
}
```

**Expected Response**: Should mention:
- SCREENING stage (early stage)
- 3 days in current stage (SLA healthy - threshold is 2 days, but recent application)
- Campus recruitment application from MIT

#### Prompt 4.2: Draft Application Group
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-010",
  "correlation_id": "corr-010",
  "candidate_id": "C002",
  "message": "Do I have any draft applications?"
}
```

**Expected Response**: Should mention:
- Application Group AG001 in DRAFT status
- 3 jobs: J001 (Senior SRE), J002 (Frontend), J004 (DevOps)
- 60% complete
- Resume uploaded, cover letter in progress

#### Prompt 4.3: Assessment Results
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-011",
  "correlation_id": "corr-011",
  "candidate_id": "C002",
  "message": "How did I do on my assessments?"
}
```

**Expected Response**: Should mention:
- JAVA_01: 78/100 (70th percentile) - PASSED
- SQL_BASIC_01: 82/100 (75th percentile) - PASSED
- CODING_FUNDAMENTALS_01: 85/100 (80th percentile) - PASSED

---

### Scenario 5: Hired Candidate with Offer Negotiation (Candidate C003)

**Test Candidate**: C003 (Jordan Rivera - Senior Security Engineer, hired)

#### Prompt 5.1: Complete Application Journey
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-012",
  "correlation_id": "corr-012",
  "candidate_id": "C003",
  "application_id": "A005",
  "message": "Can you walk me through my application journey for the Security Engineer role?"
}
```

**Expected Response**: Should show complete workflow:
1. RECEIVED (30 days ago via TechRecruit agency)
2. SCREENING (fast-track for senior role)
3. PHONE_INTERVIEW
4. TECHNICAL_INTERVIEW (passed - demonstrated threat modeling)
5. FINAL_INTERVIEW (team consensus: exceptional candidate)
6. OFFER_EXTENDED (15 days ago)
7. OFFER_ACCEPTED (10 days ago after negotiation)
8. HIRED (1 day ago - background check cleared)

#### Prompt 5.2: Offer Details with Negotiation
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-013",
  "correlation_id": "corr-013",
  "candidate_id": "C003",
  "application_id": "A005",
  "message": "What was my final offer package?"
}
```

**Expected Response**: Should mention:
- Final offer: $170k base + $15k signing bonus + 8k shares (~$120k equity)
- Negotiation history: Candidate requested $180k, company counter-offered $170k + $15k signing
- Start date: April 1, 2026

---

### Scenario 6: SLA Breach (Candidate C004)

**Test Candidate**: C004 (Chris Martinez - Backend Engineer with SLA breach)

#### Prompt 6.1: SLA Breach Detection
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-014",
  "correlation_id": "corr-014",
  "candidate_id": "C004",
  "application_id": "A006",
  "message": "How long has my application been in screening?"
}
```

**Expected Response**: Should mention:
- **12 days in SCREENING stage**
- **SLA BREACHED** (threshold is 2 days for screening)
- Assigned to recruiter Emily Martinez

---

### Scenario 7: Job Search & Matching

#### Prompt 7.1: Search by Shift Type
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-015",
  "correlation_id": "corr-015",
  "candidate_id": "C001",
  "message": "Show me all day shift jobs"
}
```

**Expected Response**: Should list:
- J001 (Senior SRE - DAY shift, 9-5)
- J005 (Junior Backend - DAY shift, 9-5)
- J007 (ML Engineer - DAY shift, 10-6)

#### Prompt 7.2: Job Details
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-016",
  "correlation_id": "corr-016",
  "candidate_id": "C001",
  "message": "Tell me more about the Senior SRE role (job J001)"
}
```

**Expected Response**: Should mention:
- Title: Senior Software Engineer - Site Reliability
- Location: San Francisco, CA
- Department: Engineering - SRE
- Shift: DAY (9:00-17:00, Mon-Fri)
- Compensation: $150k-$200k + 15% bonus
- Required assessments: KUBERNETES_03, SYS_DESIGN_02, PYTHON_04
- Hiring manager: Sarah Chen

---

### Scenario 8: Withdrawn Application (Candidate C005)

**Test Candidate**: C005 (Taylor Kim - Frontend Engineer, withdrew)

#### Prompt 8.1: Withdrawn Status
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "test-017",
  "correlation_id": "corr-017",
  "candidate_id": "C005",
  "application_id": "A007",
  "message": "What happened to my Frontend Engineer application?"
}
```

**Expected Response**: Should mention:
- Status: WITHDRAWN
- Withdrew 7 days ago (before technical interview)
- Reason: Accepted offer from another company

---

### Scenario 9: Multi-Turn Conversation (Candidate C001)

**Test a realistic multi-turn conversation**:

#### Turn 1: Initial Query
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "multi-001",
  "correlation_id": "corr-multi-001",
  "candidate_id": "C001",
  "message": "What applications do I have?"
}
```

#### Turn 2: Follow-up on Specific Application
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "multi-001",
  "correlation_id": "corr-multi-002",
  "candidate_id": "C001",
  "message": "Tell me more about the SRE one"
}
```

**Expected**: Should understand context from Turn 1 and provide details about A001 (SRE application)

#### Turn 3: Interview Preparation
```json
POST http://localhost:8000/api/v2/agent/invoke
{
  "thread_id": "multi-001",
  "correlation_id": "corr-multi-003",
  "candidate_id": "C001",
  "message": "What should I prepare for the upcoming interview?"
}
```

**Expected**: Should remember we're discussing A001 and mention the 2 upcoming interviews (technical + system design)

---

### Scenario 10: Streaming API Test

#### Streaming Request Example
```json
POST http://localhost:8000/api/v2/agent/stream
{
  "thread_id": "stream-001",
  "correlation_id": "corr-stream-001",
  "candidate_id": "C001",
  "message": "Give me a detailed summary of all my applications, interviews, and next steps"
}
```

**Expected**: Should stream SSE events showing:
- Tool calls (getApplicationsByCandidate, getScheduledEvents, etc.)
- Incremental response tokens
- Final complete answer with all details

---

## Testing Checklist

### Basic Functionality
- [ ] Profile queries return correct candidate data (C001-C005)
- [ ] Skills gap analysis compares candidate assessments with job requirements
- [ ] Application status queries return accurate workflow stage
- [ ] SLA calculation correctly identifies breaches (A006: 12 days in screening)
- [ ] Interview schedule returns names (NOT IDs) - PII stripping validated

### Enterprise Features
- [ ] ApplicationGroups (AG001, AG002, AG003) return correct status
- [ ] Offer details show compensation WITHOUT negotiation notes (PII stripped)
- [ ] Shift matching works (day/night/rotating/flexible/on-call)
- [ ] Assessment code mapping matches job requirements with candidate results

### Edge Cases
- [ ] Rejected application (A003) returns constructive message
- [ ] Withdrawn application (A007) explains candidate action
- [ ] Hired candidate (A005) shows complete journey
- [ ] New grad (C002) handles zero years of experience correctly
- [ ] Draft application group (AG001) shows 60% completion

### Multi-Turn Conversations
- [ ] Context preserved across turns (same thread_id)
- [ ] Follow-up questions work without repeating candidate_id
- [ ] Agent remembers which application is being discussed

### PII Protection
- [ ] No SSN, DOB, or addresses in responses
- [ ] Interviewer names included (transparency)
- [ ] Interviewer IDs stripped (PII)
- [ ] Offer letter URLs stripped (contain PII)
- [ ] Compensation expectations stripped from profile

---

## Quick Start Test Commands

### Test All Candidates
```bash
# C001 - Experienced SRE (3 applications)
curl -X POST http://localhost:8000/api/v2/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test-c001","correlation_id":"corr-001","candidate_id":"C001","message":"Show me all my applications"}'

# C002 - New grad (1 application + 1 draft group)
curl -X POST http://localhost:8000/api/v2/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test-c002","correlation_id":"corr-002","candidate_id":"C002","message":"Do I have any draft applications?"}'

# C003 - Hired candidate (complete journey)
curl -X POST http://localhost:8000/api/v2/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test-c003","correlation_id":"corr-003","candidate_id":"C003","application_id":"A005","message":"Walk me through my application journey"}'

# C004 - SLA breach scenario
curl -X POST http://localhost:8000/api/v2/agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"thread_id":"test-c004","correlation_id":"corr-004","candidate_id":"C004","application_id":"A006","message":"How long has my application been in screening?"}'
```

---

## Mock Data Statistics

- **Jobs**: 8 requisitions (6 OPEN, 1 CLOSED, 1 DRAFT)
- **Applications**: 7 applications across 5 candidates
- **Application Groups**: 3 groups (1 DRAFT, 1 SUBMITTED, 1 ABANDONED)
- **Candidates**: 5 profiles (3 ACTIVE, 1 HIRED, 1 WITHDRAWN)
- **Interviews Scheduled**: 2 upcoming (for C001/A001)
- **Offers**: 2 (1 pending for C001/A002, 1 accepted for C003/A005)
- **SLA Breaches**: 1 (C004/A006 - 12 days in screening)
- **Assessment Results**: 11 total across 5 candidates

---

## Production Readiness

This mock data validates:

✅ **Three-layer transformation** (Cosmos → PII strip → Context filter → Response format)
✅ **16 tools** (all working with realistic data)
✅ **PII protection** (SSN, DOB, addresses, IDs stripped; names retained)
✅ **SLA tracking** (derived field computed on-the-fly)
✅ **ApplicationGroups** (multi-job application flows)
✅ **Shift matching** (DAY/NIGHT/ROTATING/FLEXIBLE/ON_CALL)
✅ **Assessment code mapping** (KUBERNETES_03, JAVA_01, etc.)
✅ **Interview schedule** (names safe, IDs PII)
✅ **Offer negotiation** (compensation visible, notes stripped)
✅ **Multi-stage workflows** (RECEIVED → SCREENING → ... → HIRED)

**Next Step**: Run the tests above to validate end-to-end integration! 🚀

---

**Created**: 2026-03-01
**Mock Data Location**: `candidate-mcp/src/main/java/com/example/mcpserver/store/`
**Total Test Scenarios**: 17 comprehensive prompts + 4 multi-turn + streaming examples
