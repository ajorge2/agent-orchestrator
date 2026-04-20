# OrcView

A real-time observability platform for LLM agents. OrcView gives you a live view into how an agent thinks — the state machine it runs, every tool call it makes, every reasoning step it takes — and lets you drill into sub-agents in arbitrarily deep hierarchies.

---

## What it does

You write an agent in Python. You call `agent.serve()`. You get a browser UI that shows:

- The agent's **ReAct FSM** (Reasoning → Acting → Reasoning → … → Done) animating in real time
- A **timeline** of every event: LLM responses, tool calls, tool results, agent text
- An **inspect panel** for clicking into any state and seeing what happened there
- A **detail drawer** for any event, showing the full payload plus latency/token/cost metrics
- A **history panel** with versioned run history and charts (cost over time, latency distribution, cycle counts)
- Full **sub-agent drill-down**: click into any sub-agent's result and see its own OrcView — its own state graph, its own timeline, its own inspect panel — all the way down the hierarchy

---

## Architecture

### The ReAct FSM

Every agent runs the same finite state machine:

```
START → REASONING ⇄ ACTING → DONE
```

- **REASONING**: calls the LLM with the full message history and available tool schemas. If the model returns `stop_reason == "tool_use"`, transition to ACTING. Otherwise, transition to DONE.
- **ACTING**: executes all tool calls from the last response in sequence, collects results, appends them to the message history, and transitions back to REASONING.
- **DONE**: the model returned a final text response with no tool calls.

This loop is explicit in code as an enum + while loop rather than a framework abstraction. The state machine is fully observable — every transition is emitted as an event.

### The SDK (`orcview/`)

Three public exports: `Agent`, `tool`, `CyclicAgentError`.

**`@tool` decorator** — converts a Python function into an Anthropic-compatible tool schema by introspecting its type hints and signature at decoration time. `Literal["a", "b"]` becomes a JSON Schema enum. `Optional[X]` unwraps correctly. Parameters with defaults are marked non-required. The docstring becomes the tool description. Zero boilerplate for the user.

**`Agent` class** — holds the FSM loop, tool registry, and observability emit pipeline. Key design decisions:

- *Config snapshot at run start*: `model`, `max_cycles`, `system`, `tool_schemas`, and `tool_registry` are all snapshotted into locals at the top of `run()`. Mid-run config changes (via the UI) don't affect an in-progress execution.

- *Unified emit wrapping*: rather than separately tracking sub-agent events, the `emit` callback is wrapped at the start of every `run()` call so that all events — including those emitted by sub-agents via `sub_emit` — are captured in `all_events` before being forwarded. This means the top-level run's saved event log includes the full sub-agent trace, enabling drill-down from history.

- *Sub-agent as tool*: an `Agent` instance implements the same protocol as a `@tool` function — it has `_tool_schema` and `_as_callable()`. Passing an `Agent` as a tool to another `Agent` makes it callable exactly like any function tool. The parent LLM only ever sees the sub-agent's final `{"answer": "..."}` — internal reasoning, tool calls, and state transitions are fully opaque to it.

- *Cycle detection*: a `frozenset` called `_call_chain` is threaded through every `run()` call. Before executing, each agent checks if its label is already in the chain. If so, it raises `CyclicAgentError`. The set is immutable so parallel sub-agent calls don't interfere with each other's cycle tracking.

- *Event tagging*: sub-agent events are tagged with `_sub_run_id` and `_agent_label` as they pass through the parent's emit. The LLM never sees these fields — they're stripped before tool results are added to the message history. The UI uses them to route events to the correct agent's view.

- *Persistence ordering*: `save_run()` is called before the `done` event is emitted. This ensures that when the frontend receives `done` and immediately fetches the updated run history, the new run is already in the database.

**`Agent.serve()`** — starts a Flask server with SSE streaming. Runs the agent in a background thread, streams events to the browser via Server-Sent Events. A `threading.Event` named `cancelled` lets the browser disconnect (via `GeneratorExit` on the generator) signal the worker to stop emitting without killing the thread mid-LLM-call.

### Event stream

Every action in the FSM emits a typed dict:

| Event | When |
|---|---|
| `started` | run begins |
| `run_config` | agent label and model |
| `transition` | FSM state change with trigger reason |
| `llm_response` | after each LLM call, with token counts, cost, latency |
| `agent_text` | reasoning text visible in the response |
| `tool_call` | before each tool executes, with full input |
| `tool_result` | after each tool, with result, latency, error flag |
| `run_metrics` | totals for the whole run |
| `done` | run complete |
| `error` | unhandled exception |

Sub-agent events are the same types, tagged with `_sub_run_id`. This single stream carries all observability data for arbitrarily nested agent hierarchies.

### Versioned history (`_db.py`)

Runs are stored in SQLite at `~/.orcview/runs.db`, shared across all agents. A `versions` table tracks configuration snapshots — a new version is created on server start (if the previous version has runs) or on config save. This lets you compare run behavior across different system prompts, models, or tool configurations.

### The frontend (`static/index.html`)

Single-file, zero build-step, no framework. Key design decisions:

- *SSE guard*: the `EventSource` `onmessage` handler only closes the stream and resets the UI on `done`/`error` events that do **not** have `_sub_run_id`. Sub-agent `done` events (which arrive on the same stream) would otherwise close the connection prematurely, before the orchestrator finishes.

- *Sub-agent event filtering*: `handleEvent()` checks `_sub_run_id` on every incoming event. Events from sub-agents skip all state-graph updates (transitions, node colouring, edge animation, node metrics) and timeline cards in the parent view. They are still accumulated in `allEvents` for drill-down. When the user drills into a sub-agent, the tag is stripped before replay, so `handleEvent` treats those events as top-level for that view.

- *Agent view stack*: drilling into a sub-agent pushes the current `allEvents` snapshot onto a stack. Popping restores it. The breadcrumb in the graph panel renders from the stack (`Root › Sub-agent › Sub-sub-agent`). A `drillInIsLive` flag tracks whether the drill happened during an active run — if so, the `done` event automatically pops back to the parent view and exits snapshot mode.

- *Snapshot preservation*: loading a history run saves the current `allEvents` into `preSnapshotEvents`. Exiting the snapshot replays the saved events rather than going blank.

- *Config isolation from agent tools*: the Configure panel prevents adding the current agent as one of its own sub-agent tools, and propagates that constraint down the hierarchy, making cycle creation impossible from the UI.

- *Filesystem sandbox*: the codebase explorer example locks all file tools to `Path.cwd()` at startup. Any path that resolves outside the project root via `Path.resolve()` is rejected before the filesystem is touched.

---

## Project structure

```
orcview/
  __init__.py        public API: Agent, tool, CyclicAgentError
  _tool.py           @tool decorator — schema generation from type hints
  _agent.py          Agent class — FSM loop, emit pipeline, cycle detection
  _server.py         Flask server — SSE streaming, versioning, config API
  _db.py             SQLite persistence — runs, versions, metrics
  static/index.html  browser UI — graph, timeline, inspect, history, drill-down

examples/
  codebase_explorer/ three-tier agent hierarchy (Orchestrator → File Explorer + Code Analyzer)
```

---

## Usage

```python
from orcview import Agent, tool

@tool
def search(query: str, max_results: int = 10) -> dict:
    """Search for documents matching a query."""
    ...

agent = Agent(
    label="Research Assistant",
    tools=[search],
    system="You are a research assistant. Use search to find information.",
)

agent.serve()  # → http://localhost:5050
```

Sub-agents are just `Agent` instances passed as tools:

```python
researcher = Agent(label="Researcher", tools=[search, read_page])
writer     = Agent(label="Writer",     tools=[researcher, outline, draft])

writer.serve()
```

The `Writer` agent can delegate to `Researcher`. OrcView shows both state machines, and you can drill from the writer's tool result card into the researcher's full execution trace.

---

## Running the example

```bash
cd agent-orchestrator
python3 -m examples.codebase_explorer
# → http://localhost:5050
```

Ask it anything about the codebase. All file access is sandboxed to the current directory.
