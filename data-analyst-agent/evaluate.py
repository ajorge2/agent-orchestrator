import anthropic
import os
from pathlib import Path

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()

FILES = [
    "fsm_agent.py",
    "fsm_streaming.py",
    "server.py",
    "generate_data.py",
    "static/index.html",
]

def load_codebase() -> str:
    base = Path(__file__).parent
    parts = []
    for name in FILES:
        path = base / name
        if path.exists():
            parts.append(f"### {name}\n```\n{path.read_text()}\n```")
    return "\n\n".join(parts)

EVALUATOR_PROMPT = """You are a production AI engineer at a top-tier startup (Anthropic, Replit, Vercel). Your job is to evaluate whether a multi-agent orchestration system is production-ready and hiring-impressive.

Evaluate the submitted code/design on these non-negotiable criteria:

1. **Real problem solved**: Does it handle a concrete, painful production scenario? (Not toy multi-agent chat.)
   - Examples: retry with exponential backoff + context window overflow recovery, tool-call deadlocks, token budget exhaustion mid-execution
   - Red flag: "agents can talk to each other" with no failure mode handling

2. **Observability that matters**: Can you *debug* a broken agent execution in <2 min?
   - Must capture: full LLM request/response, tool inputs/outputs, routing decisions, latency per step, token usage
   - Must be queryable: filter by agent, error type, cost, date range
   - Red flag: Logging to stdout; generic "trace viewer" with no search/aggregation

3. **Failure recovery that isn't trivial**:
   - Structured retry logic (not just `for i in range(3)`)
   - Context window management (not just "use GPT-4")
   - Graceful degradation (fallback agents, partial results)
   - Red flag: Catches generic `Exception`; no rollback mechanism

4. **Code quality that ships**:
   - Type hints everywhere (Pydantic schemas, typed async)
   - Clean separation: orchestration logic ≠ observability ≠ agent definitions
   - Tests for failure modes (simulated API timeouts, invalid tool outputs)
   - Red flag: Monolithic file; hard-coded values; no error schemas

5. **Signal to hiring manager**:
   - Can you explain in 90 seconds why this matters? (Not "I built a multi-agent system.")
   - Does the GitHub README show you understand *where* agents fail in production?
   - Red flag: Generic project description; no concrete use case

**Your evaluation task:**
- Rate each criterion: ✅ Strong | ⚠️ Partial | ❌ Missing
- For each ⚠️ or ❌, suggest the *smallest* addition that fixes it
- Identify the 1–2 highest-ROI improvements to make this un-ignorable"""

def main():
    codebase = load_codebase()

    print("Evaluating codebase...\n" + "─" * 60 + "\n")

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=EVALUATOR_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Here is the codebase to evaluate:\n\n{codebase}"
        }]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)

    print("\n")

if __name__ == "__main__":
    main()
