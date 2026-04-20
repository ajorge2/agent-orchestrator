import anthropic
import csv
import json
import os
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"

COST_PER_INPUT_TOKEN  = 3.0  / 1_000_000   # $3/MTok
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000   # $15/MTok
MAX_CYCLES = 12

CONFIG_PATH = Path(__file__).parent / "agent_config.json"

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {
        "model": MODEL,
        "max_cycles": MAX_CYCLES,
        "system_prompt": "",
        "enabled_tools": [],
    }

# ── FSM types ─────────────────────────────────────────────────────────────

class State(Enum):
    START     = auto()
    REASONING = auto()
    ACTING    = auto()
    DONE      = auto()


@dataclass
class Transition:
    from_state: State
    to_state: State
    trigger: str
    condition: Callable


@dataclass
class StepMetric:
    state: str
    cycle: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


@dataclass
class AgentContext:
    question: str
    rows: list[dict]
    model: str                  = MODEL
    max_cycles: int             = MAX_CYCLES
    system: str                 = ""
    enabled_tools: list         = field(default_factory=list)
    messages: list[dict]        = field(default_factory=list)
    last_response: Any          = None
    tool_results: list[dict]    = field(default_factory=list)
    state_history: list[State]  = field(default_factory=list)
    run_id: str                 = field(default_factory=lambda: uuid.uuid4().hex[:8])
    cycles: int                 = 0
    step_metrics: list[StepMetric] = field(default_factory=list)
    total_input_tokens: int     = 0
    total_output_tokens: int    = 0
    total_cost_usd: float       = 0.0
    run_start: float            = field(default_factory=time.monotonic)


TRANSITIONS: list[Transition] = [
    Transition(State.START,     State.REASONING, "question_received",
               lambda ctx: bool(ctx.question)),
    Transition(State.REASONING, State.ACTING,    "tool_use_requested",
               lambda ctx: ctx.last_response and ctx.last_response.stop_reason == "tool_use"),
    Transition(State.REASONING, State.DONE,      "answer_ready",
               lambda ctx: ctx.last_response and ctx.last_response.stop_reason == "end_turn"),
    Transition(State.ACTING,    State.REASONING, "tool_results_ready",
               lambda ctx: bool(ctx.tool_results)),
]


def next_state(current: State, ctx: AgentContext) -> tuple[State, str]:
    for t in TRANSITIONS:
        if t.from_state == current and t.condition(ctx):
            return t.to_state, t.trigger
    raise RuntimeError(f"No valid transition from {current}")


# ── tools ─────────────────────────────────────────────────────────────────

def summarize_dataset(rows: list[dict], **_) -> dict:
    return {"row_count": len(rows), "columns": list(rows[0].keys()) if rows else []}


def get_column_stats(rows: list[dict], column: str, **_) -> dict:
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
        freq: dict[str, int] = defaultdict(int)
        for v in values:
            freq[v] += 1
        top = sorted(freq.items(), key=lambda x: -x[1])[:5]
        return {"column": column, "type": "categorical",
                "unique_values": len(freq), "top_values": top}


def group_and_aggregate(rows: list[dict], group_by: str,
                         agg_column: str, agg_fn: str, **_) -> list[dict]:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        try:
            groups[row[group_by]].append(float(row[agg_column]))
        except (ValueError, KeyError):
            pass
    fns = {"sum": sum, "mean": statistics.mean,
           "min": min, "max": max, "count": len}
    fn = fns.get(agg_fn, sum)
    result = [{"group": k, agg_fn: round(fn(v), 2)} for k, v in groups.items()]
    return sorted(result, key=lambda x: -x[agg_fn])


def filter_rows(rows: list[dict], column: str,
                operator: str, value: str, **_) -> dict:
    ops: dict[str, Callable] = {
        "==": lambda a, b: a == b,   "!=": lambda a, b: a != b,
        ">":  lambda a, b: float(a) > float(b),
        "<":  lambda a, b: float(a) < float(b),
        ">=": lambda a, b: float(a) >= float(b),
        "<=": lambda a, b: float(a) <= float(b),
    }
    op = ops.get(operator)
    if not op:
        return {"error": f"Unknown operator: {operator}"}
    matched = [r for r in rows if op(r.get(column, ""), value)]
    return {"matched_rows": len(matched), "sample": matched[:5]}


def plot_bar_chart(rows: list[dict], group_by: str,
                   agg_column: str, agg_fn: str, **_) -> dict:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        try:
            groups[row[group_by]].append(float(row[agg_column]))
        except (ValueError, KeyError):
            pass
    fns = {"sum": sum, "mean": statistics.mean, "min": min, "max": max, "count": len}
    fn = fns.get(agg_fn, sum)
    result = sorted([(k, round(fn(v), 2)) for k, v in groups.items()], key=lambda x: -x[1])
    return {
        "chart_type": "bar",
        "title": f"{agg_fn.capitalize()} of {agg_column} by {group_by}",
        "x_label": group_by,
        "y_label": f"{agg_fn}({agg_column})",
        "labels": [r[0] for r in result],
        "values": [r[1] for r in result],
    }


def plot_histogram(rows: list[dict], column: str, bins: int = 10, **_) -> dict:
    try:
        vals = [float(row[column]) for row in rows if row.get(column)]
    except (ValueError, KeyError) as e:
        return {"error": f"Cannot build histogram: {e}"}
    if not vals:
        return {"error": "No numeric values found"}
    min_v, max_v = min(vals), max(vals)
    bin_size = (max_v - min_v) / bins if max_v > min_v else 1
    counts = [0] * bins
    labels = []
    for i in range(bins):
        lo = min_v + i * bin_size
        hi = lo + bin_size
        labels.append(f"{lo:.0f}–{hi:.0f}")
        counts[i] = sum(1 for v in vals if lo <= v < hi)
    counts[-1] += sum(1 for v in vals if v == max_v)
    return {
        "chart_type": "bar",
        "title": f"Distribution of {column}",
        "x_label": column,
        "y_label": "frequency",
        "labels": labels,
        "values": counts,
    }


def plot_line_chart(rows: list[dict], date_column: str,
                    value_column: str, agg_fn: str = "sum", **_) -> dict:
    groups: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        try:
            key = str(row[date_column])[:7]   # YYYY-MM
            groups[key].append(float(row[value_column]))
        except (ValueError, KeyError):
            pass
    fns = {"sum": sum, "mean": statistics.mean, "min": min, "max": max, "count": len}
    fn = fns.get(agg_fn, sum)
    sorted_groups = sorted(groups.items())
    return {
        "chart_type": "line",
        "title": f"{agg_fn.capitalize()} of {value_column} over time",
        "x_label": date_column,
        "y_label": f"{agg_fn}({value_column})",
        "labels": [k for k, _ in sorted_groups],
        "values": [round(fn(v), 2) for _, v in sorted_groups],
    }


TOOL_REGISTRY: dict[str, Callable] = {
    "summarize_dataset":   summarize_dataset,
    "get_column_stats":    get_column_stats,
    "group_and_aggregate": group_and_aggregate,
    "filter_rows":         filter_rows,
    "plot_bar_chart":      plot_bar_chart,
    "plot_histogram":      plot_histogram,
    "plot_line_chart":     plot_line_chart,
}

TOOL_SCHEMAS = [
    {"name": "summarize_dataset",
     "description": "Get row count and column names.",
     "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_column_stats",
     "description": "Stats for a single column (numeric or categorical).",
     "input_schema": {"type": "object",
                      "properties": {"column": {"type": "string"}},
                      "required": ["column"]}},
    {"name": "group_and_aggregate",
     "description": "Group rows by a column and aggregate another.",
     "input_schema": {"type": "object",
                      "properties": {
                          "group_by":   {"type": "string"},
                          "agg_column": {"type": "string"},
                          "agg_fn":     {"type": "string",
                                         "enum": ["sum", "mean", "min", "max", "count"]}},
                      "required": ["group_by", "agg_column", "agg_fn"]}},
    {"name": "filter_rows",
     "description": "Filter rows by a condition.",
     "input_schema": {"type": "object",
                      "properties": {
                          "column":   {"type": "string"},
                          "operator": {"type": "string",
                                       "enum": ["==", "!=", ">", "<", ">=", "<="]},
                          "value":    {"type": "string"}},
                      "required": ["column", "operator", "value"]}},
    {"name": "plot_bar_chart",
     "description": "Create a bar chart by grouping rows and aggregating a numeric column. Use when the user asks for a chart, graph, or visual comparison by category.",
     "input_schema": {"type": "object",
                      "properties": {
                          "group_by":   {"type": "string"},
                          "agg_column": {"type": "string"},
                          "agg_fn":     {"type": "string",
                                         "enum": ["sum", "mean", "min", "max", "count"]}},
                      "required": ["group_by", "agg_column", "agg_fn"]}},
    {"name": "plot_histogram",
     "description": "Create a histogram showing the frequency distribution of a numeric column.",
     "input_schema": {"type": "object",
                      "properties": {
                          "column": {"type": "string"},
                          "bins":   {"type": "integer"}},
                      "required": ["column"]}},
    {"name": "plot_line_chart",
     "description": "Create a line chart showing a numeric column aggregated by month. Use when the user asks for trends or time-series analysis.",
     "input_schema": {"type": "object",
                      "properties": {
                          "date_column":  {"type": "string"},
                          "value_column": {"type": "string"},
                          "agg_fn":       {"type": "string",
                                           "enum": ["sum", "mean", "min", "max", "count"]}},
                      "required": ["date_column", "value_column", "agg_fn"]}},
]

SYSTEM = """You are a data analyst assistant with access to a sales dataset.
Use tools to explore the data and answer accurately.
When the user asks for a chart, graph, plot, or visualization, use plot_bar_chart, plot_histogram, or plot_line_chart — the result will be rendered as an interactive chart automatically. Do not describe the chart in text; just call the tool.
When you have a complete text answer (no chart needed), respond in plain language — no code, no raw JSON."""


# ── safe tool execution ───────────────────────────────────────────────────

def execute_tool_safe(name: str, rows: list[dict], inputs: dict) -> dict:
    if name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool '{name}'. Available: {list(TOOL_REGISTRY)}"}
    try:
        return TOOL_REGISTRY[name](rows, **inputs)
    except KeyError as e:
        return {"error": f"Column not found: {e}. Call summarize_dataset to see available columns."}
    except (ValueError, TypeError) as e:
        return {"error": f"Invalid input: {e}"}
    except Exception as e:
        return {"error": f"Tool failed ({type(e).__name__}): {e}"}


# ── LLM call with retry ───────────────────────────────────────────────────

def call_llm_with_retry(ctx: AgentContext, emit: Callable) -> Any:
    if ctx.cycles >= ctx.max_cycles:
        raise RuntimeError(f"Exceeded {ctx.max_cycles} reasoning cycles — possible infinite loop")
    ctx.cycles += 1

    active_schemas = [s for s in TOOL_SCHEMAS if s["name"] in ctx.enabled_tools] or TOOL_SCHEMAS

    for attempt in range(3):
        t0 = time.monotonic()
        try:
            response = client.messages.create(
                model=ctx.model, max_tokens=2048, system=ctx.system,
                tools=active_schemas, messages=ctx.messages,
            )
            latency_ms = round((time.monotonic() - t0) * 1000)
            usage = response.usage
            cost = (usage.input_tokens  * COST_PER_INPUT_TOKEN +
                    usage.output_tokens * COST_PER_OUTPUT_TOKEN)
            ctx.total_input_tokens  += usage.input_tokens
            ctx.total_output_tokens += usage.output_tokens
            ctx.total_cost_usd      += cost

            metric = StepMetric(
                state=ctx.state_history[-1].name if ctx.state_history else "REASONING",
                cycle=ctx.cycles,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=round(cost, 6),
                latency_ms=latency_ms,
            )
            ctx.step_metrics.append(metric)

            emit({
                "type":         "llm_response",
                "cycle":        ctx.cycles,
                "input_tokens": usage.input_tokens,
                "output_tokens":usage.output_tokens,
                "cost_usd":     round(cost, 6),
                "latency_ms":   latency_ms,
            })
            return response

        except anthropic.RateLimitError:
            if attempt == 2:
                raise
            wait = 2 ** attempt
            emit({"type": "warning", "message": f"Rate limited — retrying in {wait}s"})
            time.sleep(wait)

        except anthropic.APIStatusError as e:
            raise RuntimeError(f"API error {e.status_code}: {e.message}") from e

    raise RuntimeError("LLM call failed after 3 attempts")


# ── CSV loader ────────────────────────────────────────────────────────────

def load_csv(path: str | Path) -> list[dict]:
    with open(path) as f:
        return list(csv.DictReader(f))
