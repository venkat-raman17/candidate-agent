"""v2 API scenario tests — runs all use cases against a live server.

Requires:
  - candidate-mcp running at http://localhost:8081/mcp
  - candidate-agent running at http://localhost:8000
  - Valid ANTHROPIC_API_KEY in environment

Run:
    python tests/test_v2_scenarios.py
    python tests/test_v2_scenarios.py --base-url http://localhost:8000
    python tests/test_v2_scenarios.py --scenario 3    # run a single scenario by number
"""

import argparse
import json
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

BASE_URL = "http://localhost:8000"
INVOKE_PATH = "/api/v2/agent/invoke"
STREAM_PATH = "/api/v2/agent/stream"
TIMEOUT = 120  # seconds — LLM + tool calls can take a while


# ── Colour helpers ────────────────────────────────────────────────────────────

RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"


def green(s): return f"{GREEN}{s}{RESET}"
def red(s):   return f"{RED}{s}{RESET}"
def cyan(s):  return f"{CYAN}{s}{RESET}"
def bold(s):  return f"{BOLD}{s}{RESET}"
def dim(s):   return f"{DIM}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"


# ── Scenario definitions ──────────────────────────────────────────────────────

@dataclass
class Scenario:
    number: int
    group: str
    title: str
    candidate_id: str
    message: str
    application_id: str = ""
    stream: bool = False
    # Soft assertions — checked but don't fail the run
    expect_agent: Optional[str] = None          # "post_apply_assistant"
    expect_tools: list[str] = field(default_factory=list)   # tool names that must appear
    expect_keywords: list[str] = field(default_factory=list) # words that must appear in response


SCENARIOS: list[Scenario] = [
    # ── Profile ───────────────────────────────────────────────────────────────
    # Scenario(
    #     number=1,
    #     group="Profile",
    #     title="View candidate profile (Bob, C002)",
    #     candidate_id="C002",
    #     message="Show me my candidate profile.",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getCandidateProfile"],
    #     expect_keywords=["Bob", "Python", "Machine Learning"],
    # ),
    # Scenario(
    #     number=2,
    #     group="Profile",
    #     title="Skills gap vs a role not yet applied to (Alice C001 vs J002 ML role)",
    #     candidate_id="C001",
    #     message="How does my profile match the Machine Learning Engineer role J002?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getSkillsGap"],
    #     expect_keywords=["J002"],
    # ),

    # # ── Application status — specific application ─────────────────────────────
    # Scenario(
    #     number=3,
    #     group="Application Status",
    #     title="Status of FINAL_INTERVIEW application (Alice, A001)",
    #     candidate_id="C001",
    #     application_id="A001",
    #     message="Where does my application stand?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getApplicationStatus"],
    #     expect_keywords=["interview", "final"],
    # ),
    # Scenario(
    #     number=4,
    #     group="Application Status",
    #     title="OFFER_EXTENDED — offer details surfaced (David, A004)",
    #     candidate_id="C004",
    #     application_id="A004",
    #     message="What is the latest on my application?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getApplicationStatus"],
    #     expect_keywords=["offer"],
    # ),
    # Scenario(
    #     number=5,
    #     group="Application Status",
    #     title="REJECTED — constructive tone expected (Alice, A006)",
    #     candidate_id="C001",
    #     application_id="A006",
    #     message="I applied for a Platform Engineer role — what happened?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getApplicationStatus"],
    #     expect_keywords=["not", "role"],
    # ),

    # # ── All applications for a candidate ─────────────────────────────────────
    # Scenario(
    #     number=6,
    #     group="All Applications",
    #     title="Full application history — no application_id (Alice, A001 + A006)",
    #     candidate_id="C001",
    #     message="Give me an overview of all my applications.",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getApplicationsByCandidate"],
    #     expect_keywords=["A001", "A006"],
    # ),
    # Scenario(
    #     number=7,
    #     group="All Applications",
    #     title="Journey narrative — no application_id (Frank, A007 TECHNICAL_INTERVIEW)",
    #     candidate_id="C006",
    #     message="Walk me through my application journey so far.",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getApplicationsByCandidate"],
    #     expect_keywords=["Frank", "interview"],
    # ),

    # # ── Assessment results ────────────────────────────────────────────────────
    # Scenario(
    #     number=8,
    #     group="Assessments",
    #     title="All assessments — David (3 assessments, top scorer AS005/6/7)",
    #     candidate_id="C004",
    #     application_id="A004",
    #     message="How did I do on my assessments?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getAssessmentResults"],
    #     expect_keywords=["coding", "system design"],
    # ),
    # Scenario(
    #     number=9,
    #     group="Assessments",
    #     title="Percentile comparison — Bob AS003 (94th percentile tech screening)",
    #     candidate_id="C002",
    #     application_id="A002",
    #     message="How do my assessment scores compare to other applicants?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["compareToPercentile"],
    #     expect_keywords=["percentile"],
    # ),

    # # ── Next steps & SLA ─────────────────────────────────────────────────────
    # Scenario(
    #     number=10,
    #     group="Next Steps",
    #     title="Next steps for PHONE_INTERVIEW stage (Bob, A002)",
    #     candidate_id="C002",
    #     application_id="A002",
    #     message="What should I prepare for next?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getNextSteps"],
    #     expect_keywords=["interview", "prepare"],
    # ),
    # Scenario(
    #     number=11,
    #     group="Next Steps",
    #     title="SLA / stage duration check (Frank, A007)",
    #     candidate_id="C006",
    #     application_id="A007",
    #     message="How long has my application been at this stage? Is that normal?",
    #     expect_agent="post_apply_assistant",
    #     expect_tools=["getStageDuration"],
    #     expect_keywords=["day"],
    # ),

    # # ── Stream endpoint ───────────────────────────────────────────────────────
    Scenario(
        number=12,
        group="Streaming",
        title="SSE stream — status + next steps (Carol, A003 SCREENING)",
        candidate_id="C003",
        application_id="A003",
        message="What is the status of my application and what should I do next?",
        stream=True,
        expect_agent="post_apply_assistant",
        expect_tools=["getApplicationStatus", "getNextSteps"],
        expect_keywords=["screening", "next"],
    ),

    # ── Edge cases ────────────────────────────────────────────────────────────
    Scenario(
        number=13,
        group="Edge Cases",
        title="HIRED candidate — journey summary (Emma, A005)",
        candidate_id="C005",
        application_id="A005",
        message="Can you summarise my entire application journey?",
        expect_agent="post_apply_assistant",
        expect_tools=["getCandidateJourney"],
        expect_keywords=["hired", "platform"],
    ),
    Scenario(
        number=14,
        group="Edge Cases",
        title="Interview feedback query — 3 rounds + recruiter notes (Alice, A001)",
        candidate_id="C001",
        application_id="A001",
        message="Do you have any feedback from my interviews so far?",
        expect_agent="post_apply_assistant",
        expect_tools=["getInterviewFeedback"],
        expect_keywords=["interview", "technical"],
    ),
]


# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class Result:
    scenario: Scenario
    passed: bool
    duration_s: float
    agent_used: str = ""
    tool_calls: list[str] = field(default_factory=list)
    response_preview: str = ""
    assertion_failures: list[str] = field(default_factory=list)
    error: str = ""


# ── Runners ───────────────────────────────────────────────────────────────────

def run_invoke(sc: Scenario, base_url: str) -> Result:
    payload = {
        "message": sc.message,
        "candidate_id": sc.candidate_id,
        "thread_id": f"scenario-{sc.number:02d}",
        "correlation_id": f"test-run-{sc.number:02d}",
    }
    if sc.application_id:
        payload["application_id"] = sc.application_id

    t0 = time.perf_counter()
    try:
        resp = httpx.post(
            f"{base_url}{INVOKE_PATH}",
            json=payload,
            timeout=TIMEOUT,
        )
        duration = time.perf_counter() - t0
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return Result(
            scenario=sc,
            passed=False,
            duration_s=time.perf_counter() - t0,
            error=str(exc),
        )

    agent_used = body.get("agent_used", "")
    tool_calls = body.get("tool_calls", [])
    response_text = body.get("response", "")

    failures = _check_assertions(sc, agent_used, tool_calls, response_text)

    return Result(
        scenario=sc,
        passed=len(failures) == 0,
        duration_s=duration,
        agent_used=agent_used,
        tool_calls=tool_calls,
        response_preview=response_text[:300],
        assertion_failures=failures,
    )


def run_stream(sc: Scenario, base_url: str) -> Result:
    payload = {
        "message": sc.message,
        "candidate_id": sc.candidate_id,
        "thread_id": f"scenario-{sc.number:02d}-stream",
        "correlation_id": f"test-run-{sc.number:02d}-stream",
    }
    if sc.application_id:
        payload["application_id"] = sc.application_id

    t0 = time.perf_counter()
    token_chunks: list[str] = []
    tool_calls_seen: list[str] = []
    active_agent = ""
    handoff_seen = False

    try:
        with httpx.stream(
            "POST",
            f"{base_url}{STREAM_PATH}",
            json=payload,
            timeout=TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                etype = event.get("event", "")
                edata = event.get("data", {})

                if etype == "token":
                    token_chunks.append(edata.get("content", ""))
                elif etype == "tool_call":
                    tool_calls_seen.append(edata.get("name", ""))
                elif etype == "handoff":
                    handoff_seen = True
                    active_agent = edata.get("to", "")
                elif etype == "done":
                    active_agent = edata.get("active_agent", active_agent)
                    tool_calls_seen = edata.get("tool_calls", tool_calls_seen)
                    break
    except Exception as exc:
        return Result(
            scenario=sc,
            passed=False,
            duration_s=time.perf_counter() - t0,
            error=str(exc),
        )

    duration = time.perf_counter() - t0
    full_response = "".join(token_chunks)
    failures = _check_assertions(sc, active_agent, tool_calls_seen, full_response)

    # Stream-specific: warn if no tokens received
    if not token_chunks:
        failures.append("no token events received in stream")

    return Result(
        scenario=sc,
        passed=len(failures) == 0,
        duration_s=duration,
        agent_used=active_agent,
        tool_calls=tool_calls_seen,
        response_preview=full_response[:300],
        assertion_failures=failures,
    )


def _check_assertions(
    sc: Scenario,
    agent_used: str,
    tool_calls: list[str],
    response_text: str,
) -> list[str]:
    failures = []

    if sc.expect_agent and agent_used != sc.expect_agent:
        failures.append(
            f"agent_used={agent_used!r}, expected {sc.expect_agent!r}"
        )

    for tool in sc.expect_tools:
        if tool not in tool_calls:
            failures.append(f"expected tool {tool!r} not found in {tool_calls}")

    response_lower = response_text.lower()
    for kw in sc.expect_keywords:
        if kw.lower() not in response_lower:
            failures.append(f"keyword {kw!r} not found in response")

    return failures


# ── Display ───────────────────────────────────────────────────────────────────

def print_header():
    print()
    print(bold("=" * 72))
    print(bold("  candidate-agent  ·  v2 API Scenario Tests"))
    print(bold("=" * 72))
    print()


def print_scenario_start(sc: Scenario):
    tag = "[STREAM]" if sc.stream else "[INVOKE]"
    print(f"{cyan(f'[{sc.number:02d}]')} {bold(sc.title)}")
    print(f"     {dim(sc.group)} {dim(tag)}")
    print(f"     candidate_id={yellow(sc.candidate_id)}", end="")
    if sc.application_id:
        print(f"  application_id={yellow(sc.application_id)}", end="")
    print()
    print(f"     {dim(repr(sc.message))}")


def print_result(r: Result):
    status = green("PASS") if r.passed else red("FAIL")
    print(f"     {status}  {dim(f'{r.duration_s:.1f}s')}  "
          f"agent={cyan(r.agent_used or '?')}  "
          f"tools={dim(str(r.tool_calls))}")

    if r.error:
        print(f"     {red('ERROR:')} {r.error}")

    if r.assertion_failures:
        for f in r.assertion_failures:
            print(f"     {yellow('⚠')}  {f}")

    if r.response_preview:
        wrapped = textwrap.fill(r.response_preview, width=66,
                                initial_indent="     │ ",
                                subsequent_indent="     │ ")
        print(wrapped)
        if len(r.response_preview) == 300:
            print("     │ …")

    print()


def print_summary(results: list[Result]):
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_time = sum(r.duration_s for r in results)

    print(bold("=" * 72))
    print(bold("  Summary"))
    print(bold("=" * 72))

    # Group by scenario group
    groups: dict[str, list[Result]] = {}
    for r in results:
        groups.setdefault(r.scenario.group, []).append(r)

    for group, group_results in groups.items():
        group_passed = sum(1 for r in group_results if r.passed)
        group_icon = green("✓") if group_passed == len(group_results) else red("✗")
        print(f"  {group_icon}  {group:<22} {group_passed}/{len(group_results)}")

    print()
    total_icon = green("✓ ALL PASSED") if failed == 0 else red(f"✗ {failed} FAILED")
    print(f"  {total_icon}  {passed}/{len(results)} scenarios  "
          f"{dim(f'({total_time:.1f}s total)')}")
    print()

    if failed:
        print(bold("  Failed scenarios:"))
        for r in results:
            if not r.passed:
                print(f"    {red(f'[{r.scenario.number:02d}]')} {r.scenario.title}")
                for f in r.assertion_failures:
                    print(f"         {yellow('⚠')}  {f}")
                if r.error:
                    print(f"         {red('error:')} {r.error}")
        print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="v2 API scenario tests")
    parser.add_argument("--base-url", default=BASE_URL,
                        help=f"Agent base URL (default: {BASE_URL})")
    parser.add_argument("--scenario", type=int, default=None,
                        help="Run a single scenario by number (1-14)")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario is not None:
        scenarios = [s for s in SCENARIOS if s.number == args.scenario]
        if not scenarios:
            print(red(f"No scenario with number {args.scenario}"))
            sys.exit(1)

    # Quick health check
    try:
        health = httpx.get(f"{args.base_url}/health", timeout=5).json()
        mcp_ok = health.get("mcp_connected", False)
    except Exception as exc:
        print(red(f"Cannot reach agent at {args.base_url}: {exc}"))
        sys.exit(1)

    print_header()
    print(f"  base_url  : {args.base_url}")
    print(f"  mcp       : {green('connected') if mcp_ok else red('DISCONNECTED')}")
    print(f"  scenarios : {len(scenarios)}")
    print()

    results: list[Result] = []
    for sc in scenarios:
        print_scenario_start(sc)
        if sc.stream:
            r = run_stream(sc, args.base_url)
        else:
            r = run_invoke(sc, args.base_url)
        print_result(r)
        results.append(r)

    print_summary(results)
    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
