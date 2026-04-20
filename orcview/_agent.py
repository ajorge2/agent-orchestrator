import json
import os
import threading
import time
import uuid
from enum import Enum, auto
from pathlib import Path
from typing import Callable

# load .env from cwd or package directory if present
for _env in (Path.cwd() / ".env", Path(__file__).parent / ".env"):
    if _env.exists():
        for _line in _env.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
        break


COST_PER_INPUT_TOKEN  = 3.0  / 1_000_000
COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000


class CyclicAgentError(RuntimeError):
    """Raised when an agent call chain would create a cycle."""


class _State(Enum):
    START     = auto()
    REASONING = auto()
    ACTING    = auto()
    DONE      = auto()


class Agent:
    def __init__(
        self,
        label: str,
        tools: list,
        model: str = "claude-sonnet-4-6",
        max_cycles: int = 12,
        system: str = "",
    ):
        self.label         = label
        self.model         = model
        self.max_cycles    = max_cycles
        self.system        = system
        self._tools        = tools  # @tool functions or Agent instances
        # all tools enabled by default; updated at runtime via /config POST
        self.enabled_tools: set[str] = {self._tool_name(t) for t in tools}

    # ── Tool protocol — lets Agent be passed as a tool to another Agent ───────

    @property
    def _is_orcview_tool(self) -> bool:
        return True

    @property
    def _tool_schema(self) -> dict:
        name = self.label.lower().replace(" ", "_").replace("-", "_")
        return {
            "name": name,
            "description": f"Delegate a task to the {self.label} agent.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "The task to delegate."}
                },
                "required": ["task"],
            },
        }

    def _as_callable(self, emit: Callable, call_chain: frozenset, stop=None) -> Callable:
        def _delegate(task: str, **_) -> dict:
            sub_run_id = uuid.uuid4().hex[:8]

            def sub_emit(event: dict) -> None:
                emit({**event, "_sub_run_id": sub_run_id, "_agent_label": self.label})

            emit({"type": "sub_agent_start", "name": self._tool_schema["name"],
                  "agent_label": self.label, "sub_run_id": sub_run_id})

            answer = self.run(task, emit=sub_emit, _call_chain=call_chain, _stop=stop)

            emit({"type": "sub_agent_end", "name": self._tool_schema["name"],
                  "agent_label": self.label, "sub_run_id": sub_run_id})

            return {"answer": answer,
                    "__sub_run_id__": sub_run_id,
                    "__sub_agent_label__": self.label}
        return _delegate

    # ── Public API ─────────────────────────────────────────────────────────────

    def run(
        self,
        question: str,
        emit: Callable | None = None,
        _call_chain: frozenset | None = None,
        _version_id: str | None = None,
        _stop: threading.Event | None = None,
    ) -> str:
        """Run the agent FSM and return the final text answer.

        emit — optional callback that receives each event dict as it happens.
        _call_chain — internal; do not pass from user code.
        """
        # ── cycle detection ──────────────────────────────────────────────────
        if _call_chain is None:
            _call_chain = frozenset()
        if self.label in _call_chain:
            chain = " → ".join([*sorted(_call_chain), self.label])
            raise CyclicAgentError(f"cycle detected in agent call chain: {chain}")
        _call_chain = _call_chain | {self.label}

        if emit is None:
            emit = lambda _: None

        # wrap emit so ALL events (including sub-agent passthrough) are captured
        _outer_emit = emit
        all_events: list = []

        def emit(event: dict) -> None:
            all_events.append(event)
            _outer_emit(event)

        import anthropic
        client = anthropic.Anthropic()

        # snapshot config so mid-run changes don't affect this run
        model         = self.model
        max_cycles    = self.max_cycles
        system        = self.system
        tool_schemas  = self._build_schemas()
        tool_registry = self._build_registry(emit, _call_chain, _stop)

        run_id    = uuid.uuid4().hex[:8]
        run_start = time.monotonic()

        total_input  = 0
        total_output = 0
        total_cost   = 0.0
        cycles       = 0
        error: str | None = None

        messages: list       = [{"role": "user", "content": question}]
        state_history: list  = []
        last_response        = None


        def _emit(event: dict) -> None:
            emit(event)

        _emit({"type": "started",    "question": question, "run_id": run_id})
        _emit({"type": "run_config", "agent_label": self.label, "model": model})

        current = _State.START

        try:
            while current is not _State.DONE:
                state_history.append(current)

                if current is _State.START:
                    next_s, trigger = _State.REASONING, "question_received"

                elif current is _State.REASONING:
                    if _stop is not None and _stop.is_set():
                        raise RuntimeError("run cancelled")
                    if cycles >= max_cycles:
                        raise RuntimeError(
                            f"exceeded {max_cycles} reasoning cycles — possible loop"
                        )
                    cycles += 1

                    t0 = time.monotonic()
                    last_response = client.messages.create(
                        model=model,
                        max_tokens=2048,
                        system=system,
                        tools=tool_schemas,
                        messages=messages,
                    )
                    latency_ms = round((time.monotonic() - t0) * 1000)

                    usage = last_response.usage
                    cost  = (usage.input_tokens  * COST_PER_INPUT_TOKEN +
                             usage.output_tokens * COST_PER_OUTPUT_TOKEN)
                    total_input  += usage.input_tokens
                    total_output += usage.output_tokens
                    total_cost   += cost

                    _emit({
                        "type":          "llm_response",
                        "cycle":         cycles,
                        "input_tokens":  usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cost_usd":      round(cost, 6),
                        "latency_ms":    latency_ms,
                    })

                    for block in last_response.content:
                        if hasattr(block, "text") and block.text:
                            _emit({
                                "type":          "agent_text",
                                "text":          block.text,
                                "input_tokens":  usage.input_tokens,
                                "output_tokens": usage.output_tokens,
                                "cost_usd":      round(cost, 6),
                                "latency_ms":    latency_ms,
                            })

                    if last_response.stop_reason == "tool_use":
                        next_s, trigger = _State.ACTING, "tool_use_requested"
                    else:
                        next_s, trigger = _State.DONE, "answer_ready"

                elif current is _State.ACTING:
                    messages.append({"role": "assistant", "content": last_response.content})
                    tool_results = []

                    for block in last_response.content:
                        if block.type != "tool_use":
                            continue

                        _emit({"type": "tool_call", "name": block.name, "input": block.input})

                        t0 = time.monotonic()
                        fn = tool_registry.get(block.name)
                        if fn is None:
                            result = {"error": f"unknown tool '{block.name}'"}
                        else:
                            try:
                                result = fn(**block.input)
                            except CyclicAgentError:
                                raise
                            except Exception as exc:
                                result = {"error": f"{type(exc).__name__}: {exc}"}
                        exec_ms = round((time.monotonic() - t0) * 1000)

                        # strip internal routing keys before sending to LLM
                        sub_run_id        = None
                        sub_agent_label   = None
                        if isinstance(result, dict):
                            sub_run_id      = result.pop("__sub_run_id__", None)
                            sub_agent_label = result.pop("__sub_agent_label__", None)

                        result_str = json.dumps(result)
                        tool_result_evt = {
                            "type":       "tool_result",
                            "name":       block.name,
                            "result":     result_str,
                            "latency_ms": exec_ms,
                            "is_error":   "error" in result,
                        }
                        if sub_run_id:
                            tool_result_evt["sub_run_id"]      = sub_run_id
                            tool_result_evt["sub_agent_label"] = sub_agent_label
                        _emit(tool_result_evt)

                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     result_str,
                        })

                    messages.append({"role": "user", "content": tool_results})
                    next_s, trigger = _State.REASONING, "tool_results_ready"

                _emit({"type": "transition",
                       "from": current.name, "to": next_s.name, "trigger": trigger})
                current = next_s

            state_history.append(_State.DONE)

        except Exception as exc:
            error = str(exc)
            _emit({"type": "error", "message": error})

        total_latency_ms = round((time.monotonic() - run_start) * 1000)

        metrics = {
            "type":                "run_metrics",
            "run_id":              run_id,
            "agent_label":         self.label,
            "total_input_tokens":  total_input,
            "total_output_tokens": total_output,
            "total_cost_usd":      round(total_cost, 6),
            "total_latency_ms":    total_latency_ms,
            "cycles":              cycles,
            "state_path":          [s.name for s in state_history],
            "error":               error,
        }
        _emit(metrics)

        # persist before emitting "done" so history refresh sees the new run
        try:
            from . import _db
            _db.save_run(run_id, question, metrics, all_events, version_id=_version_id)
        except Exception:
            pass

        _emit({"type": "done", "path": [s.name for s in state_history]})

        if last_response:
            for block in last_response.content:
                if hasattr(block, "text") and block.text:
                    return block.text
        return error or ""

    def serve(self, port: int = 5050) -> None:
        """Start the OrcView web UI for this agent."""
        from . import _server
        _server.serve(self, port)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _tool_name(self, t) -> str:
        if isinstance(t, Agent):
            return t._tool_schema["name"]
        return t.__name__

    def _build_registry(self, emit: Callable, call_chain: frozenset, stop=None) -> dict:
        registry: dict[str, Callable] = {}
        for t in self._tools:
            name = self._tool_name(t)
            if name not in self.enabled_tools:
                continue
            if isinstance(t, Agent):
                registry[name] = t._as_callable(emit, call_chain, stop)
            elif callable(t):
                registry[t.__name__] = t
        return registry

    def _build_schemas(self) -> list[dict]:
        return [
            t._tool_schema for t in self._tools
            if hasattr(t, "_tool_schema") and self._tool_name(t) in self.enabled_tools
        ]
