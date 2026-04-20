import anthropic
import csv
import json
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

# ── env ────────────────────────────────────────────────────────────────────

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"

# ── FSM primitives ─────────────────────────────────────────────────────────

class State(Enum):
    START     = auto()
    THINKING  = auto()
    EXECUTING = auto()
    DONE      = auto()

@dataclass
class Transition:
    from_state: State
    to_state: State
    trigger: str          # human-readable label for the edge
    condition: Callable   # returns True when this transition should fire

@dataclass
class AgentContext:
    question: str
    rows: list[dict]
    messages: list[dict] = field(default_factory=list)
    last_response: Any = None
    tool_results: list[dict] = field(default_factory=list)
    state_history: list[State] = field(default_factory=list)

# ── declared FSM ───────────────────────────────────────────────────────────

TRANSITIONS: list[Transition] = [
    Transition(
        from_state=State.START,
        to_state=State.THINKING,
        trigger="question_received",
        condition=lambda ctx: bool(ctx.question),
    ),
    Transition(
        from_state=State.THINKING,
        to_state=State.EXECUTING,
        trigger="tool_use_requested",
        condition=lambda ctx: ctx.last_response and ctx.last_response.stop_reason == "tool_use",
    ),
    Transition(
        from_state=State.THINKING,
        to_state=State.DONE,
        trigger="answer_ready",
        condition=lambda ctx: ctx.last_response and ctx.last_response.stop_reason == "end_turn",
    ),
    Transition(
        from_state=State.EXECUTING,
        to_state=State.THINKING,
        trigger="tool_results_ready",
        condition=lambda ctx: bool(ctx.tool_results),
    ),
]

def next_state(current: State, ctx: AgentContext) -> tuple[State, str]:
    for t in TRANSITIONS:
        if t.from_state == current and t.condition(ctx):
            return t.to_state, t.trigger
    raise RuntimeError(f"No valid transition from {current} — context may be inconsistent")

# ── tools ──────────────────────────────────────────────────────────────────

def summarize_dataset(rows, **_):
    return {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}

def get_column_stats(rows, column, **_):
    values = [row[column] for row in rows]
    try:
        nums = [float(v) for v in values]
        return {
            "column": column, "type": "numeric",
            "min": min(nums), "max": max(nums),
            "mean": round(statistics.mean(nums), 2),
            "median": round(statistics.median(nums), 2),
            "stdev": round(statistics.stdev(nums), 2) if len(nums) > 1 else 0,
        }
    except ValueError:
        freq = defaultdict(int)
        for v in values:
            freq[v] += 1
        top = sorted(freq.items(), key=lambda x: -x[1])[:5]
        return {"column": column, "type": "categorical", "unique_values": len(freq), "top_values": top}

def group_and_aggregate(rows, group_by, agg_column, agg_fn, **_):
    groups = defaultdict(list)
    for row in rows:
        try:
            groups[row[group_by]].append(float(row[agg_column]))
        except (ValueError, KeyError):
            pass
    fns = {"sum": sum, "mean": statistics.mean, "min": min, "max": max, "count": len}
    fn = fns.get(agg_fn, sum)
    result = [{"group": k, agg_fn: round(fn(v), 2)} for k, v in groups.items()]
    return sorted(result, key=lambda x: -x[agg_fn])

def filter_rows(rows, column, operator, value, **_):
    ops = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b,
           ">": lambda a, b: float(a) > float(b), "<": lambda a, b: float(a) < float(b),
           ">=": lambda a, b: float(a) >= float(b), "<=": lambda a, b: float(a) <= float(b)}
    op = ops.get(operator)
    if not op:
        return {"error": f"Unknown operator: {operator}"}
    matched = [r for r in rows if op(r.get(column, ""), value)]
    return {"matched_rows": len(matched), "sample": matched[:5]}

TOOL_REGISTRY = {
    "summarize_dataset": summarize_dataset,
    "get_column_stats": get_column_stats,
    "group_and_aggregate": group_and_aggregate,
    "filter_rows": filter_rows,
}

TOOL_SCHEMAS = [
    {"name": "summarize_dataset", "description": "Get row count and column names.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_column_stats", "description": "Stats for a single column (numeric or categorical).",
     "input_schema": {"type": "object", "properties": {"column": {"type": "string"}}, "required": ["column"]}},
    {"name": "group_and_aggregate", "description": "Group rows by a column and aggregate another.",
     "input_schema": {"type": "object",
                      "properties": {"group_by": {"type": "string"}, "agg_column": {"type": "string"},
                                     "agg_fn": {"type": "string", "enum": ["sum", "mean", "min", "max", "count"]}},
                      "required": ["group_by", "agg_column", "agg_fn"]}},
    {"name": "filter_rows", "description": "Filter rows by a condition.",
     "input_schema": {"type": "object",
                      "properties": {"column": {"type": "string"},
                                     "operator": {"type": "string", "enum": ["==", "!=", ">", "<", ">=", "<="]},
                                     "value": {"type": "string"}},
                      "required": ["column", "operator", "value"]}},
]

SYSTEM = """You are a data analyst assistant with access to a sales dataset.
Use tools to explore the data and answer accurately.
When you have a complete answer, respond in plain language."""

# ── state handlers ─────────────────────────────────────────────────────────

def handle_start(ctx: AgentContext):
    ctx.messages = [{"role": "user", "content": ctx.question}]

def handle_thinking(ctx: AgentContext):
    ctx.last_response = client.messages.create(
        model=MODEL, max_tokens=2048, system=SYSTEM,
        tools=TOOL_SCHEMAS, messages=ctx.messages,
    )
    for block in ctx.last_response.content:
        if hasattr(block, "text") and block.text:
            print(f"Agent: {block.text}")

def handle_executing(ctx: AgentContext):
    ctx.messages.append({"role": "assistant", "content": ctx.last_response.content})
    ctx.tool_results = []
    for block in ctx.last_response.content:
        if block.type == "tool_use":
            print(f"  [tool] {block.name}({json.dumps(block.input)})")
            result = TOOL_REGISTRY[block.name](ctx.rows, **block.input)
            result_str = json.dumps(result)
            print(f"  [result] {result_str[:200]}")
            ctx.tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })
    ctx.messages.append({"role": "user", "content": ctx.tool_results})

STATE_HANDLERS: dict[State, Callable] = {
    State.START:     handle_start,
    State.THINKING:  handle_thinking,
    State.EXECUTING: handle_executing,
    State.DONE:      lambda ctx: None,
}

# ── runner ─────────────────────────────────────────────────────────────────

def load_csv(path: str) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))

def run(question: str, csv_path: str = "sales_data.csv"):
    rows = load_csv(csv_path)
    ctx = AgentContext(question=question, rows=rows)
    current = State.START

    print(f"\nQuestion: {question}\n{'-'*50}")

    while current != State.DONE:
        ctx.state_history.append(current)
        STATE_HANDLERS[current](ctx)
        current, trigger = next_state(current, ctx)
        print(f"  --> [{trigger}] → {current.name}")
        ctx.tool_results = []  # reset after transition so condition doesn't re-fire

    ctx.state_history.append(State.DONE)
    print(f"\nState path: {' → '.join(s.name for s in ctx.state_history)}")

if __name__ == "__main__":
    import sys
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Give me a summary of this dataset."
    run(question)
