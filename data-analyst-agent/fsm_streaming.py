import json
import time
from pathlib import Path
from typing import Callable

from fsm_core import (
    AgentContext, State, Transition, TRANSITIONS,
    execute_tool_safe, call_llm_with_retry, load_csv, load_config, next_state,
    COST_PER_INPUT_TOKEN, COST_PER_OUTPUT_TOKEN, SYSTEM,
)


# ── state handlers ────────────────────────────────────────────────────────

def handle_start(ctx: AgentContext, emit: Callable[[dict], None]) -> None:
    ctx.messages = [{"role": "user", "content": ctx.question}]


def handle_reasoning(ctx: AgentContext, emit: Callable[[dict], None]) -> None:
    ctx.last_response = call_llm_with_retry(ctx, emit)
    metric = ctx.step_metrics[-1] if ctx.step_metrics else None
    for block in ctx.last_response.content:
        if hasattr(block, "text") and block.text:
            evt: dict = {"type": "agent_text", "text": block.text}
            if metric:
                evt["input_tokens"]  = metric.input_tokens
                evt["output_tokens"] = metric.output_tokens
                evt["cost_usd"]      = metric.cost_usd
                evt["latency_ms"]    = metric.latency_ms
            emit(evt)


def handle_acting(ctx: AgentContext, emit: Callable[[dict], None]) -> None:
    ctx.messages.append({"role": "assistant", "content": ctx.last_response.content})
    ctx.tool_results = []

    for block in ctx.last_response.content:
        if block.type != "tool_use":
            continue

        emit({"type": "tool_call", "name": block.name, "input": block.input})

        t0 = time.monotonic()
        result = execute_tool_safe(block.name, ctx.rows, block.input)
        latency_ms = round((time.monotonic() - t0) * 1000)

        result_str = json.dumps(result)
        emit({"type": "tool_result", "name": block.name,
              "result": result_str, "latency_ms": latency_ms,
              "is_error": "error" in result})

        ctx.tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result_str,
        })

    ctx.messages.append({"role": "user", "content": ctx.tool_results})


STATE_HANDLERS: dict[State, Callable] = {
    State.START:     handle_start,
    State.REASONING: handle_reasoning,
    State.ACTING:    handle_acting,
    State.DONE:      lambda ctx, emit: None,
}


# ── public runner ─────────────────────────────────────────────────────────

def run_with_events(question: str, emit: Callable[[dict], None],
                    csv_path: str | Path | None = None) -> AgentContext:
    if csv_path is None:
        csv_path = Path(__file__).parent / "sales_data.csv"

    rows   = load_csv(csv_path)
    config = load_config()
    ctx    = AgentContext(
        question=question, rows=rows,
        model=config["model"],
        max_cycles=config["max_cycles"],
        system=config.get("system_prompt", SYSTEM),
        enabled_tools=config.get("enabled_tools", []),
    )
    current = State.START

    emit({"type": "started", "question": question, "run_id": ctx.run_id})
    emit({"type": "run_config", "agent_label": config.get("label", "Agent"),
          "model": config["model"]})

    error: str | None = None
    try:
        while current != State.DONE:
            ctx.state_history.append(current)
            STATE_HANDLERS[current](ctx, emit)
            next_s, trigger = next_state(current, ctx)
            emit({"type": "transition",
                  "from": current.name, "to": next_s.name, "trigger": trigger})
            ctx.tool_results = []
            current = next_s

        ctx.state_history.append(State.DONE)

    except Exception as e:
        error = str(e)
        emit({"type": "error", "message": error})

    total_latency_ms = round((time.monotonic() - ctx.run_start) * 1000)

    emit({
        "type":                "run_metrics",
        "run_id":              ctx.run_id,
        "total_input_tokens":  ctx.total_input_tokens,
        "total_output_tokens": ctx.total_output_tokens,
        "total_cost_usd":      round(ctx.total_cost_usd, 6),
        "total_latency_ms":    total_latency_ms,
        "cycles":              ctx.cycles,
        "state_path":          [s.name for s in ctx.state_history],
        "error":               error,
    })

    emit({"type": "done", "path": [s.name for s in ctx.state_history]})
    return ctx
