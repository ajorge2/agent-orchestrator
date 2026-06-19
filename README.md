# Agent Orchestrator

> Build an AI agent, watch it think in real time, and trace everything it does back to a decision someone made on purpose.

An AI agent is a program that reasons in a loop: it looks at a question, decides whether it needs a tool, uses the tool, looks at the result, and repeats until it has an answer. They're powerful — and they're black boxes. When one gives a wrong answer, loops forever, or quietly burns through your budget, the usual tools tell you *that* it failed, not *where* or *why*.

**Agent Orchestrator is a complete system for building agents you can actually see inside.** You write an agent in a few lines of Python, call one function, and get a live browser dashboard that shows every reasoning step, every tool call, every sub-agent it hands work to — all the way down — with cost, latency, and token counts attached to each one. When something goes wrong, you replay the exact run and watch where it broke.

It's three things in one repository:

1. **A way to build observable agents** — a small SDK where any Python function becomes a tool and any agent becomes a sub-agent of another, with full visibility built in.
2. **A way to operate them safely** — structured retries, loop protection, cost accounting, cancellation, and a sandbox, so an agent can't run away from you.
3. **A way to direct the AI engineers that build them** — a documented method that treats a fleet of AI coding agents like an engineering org, with clear decision ownership and escalation rules.

The interesting part isn't any one of these. It's that they connect: the agents you build are observable *because* of a deliberate architecture, and that architecture exists *because* of a deliberate process. Every line of the system traces back to a choice you can name.

---

## What you can actually do

Point it at a codebase and ask a question in plain English:

```bash
python3 -m examples.codebase_explorer      # → http://localhost:5050
```

Then, in the browser, you can:

- **Watch the agent think.** A state diagram animates as the agent moves between *reasoning* and *acting*. A live timeline fills in with each thing it does — what it said, which tool it called, what came back.
- **Follow it into its sub-agents.** This example runs a manager agent that delegates to two specialists (one that explores files, one that analyzes code structure). Click any delegation and you drop into that sub-agent's *own* dashboard — its own state diagram, its own timeline — and you can keep going, as deep as the agents nest. A breadcrumb (`Codebase Orchestrator › File Explorer`) shows where you are.
- **See what every step cost.** Each reasoning step and tool call carries its latency, token usage, and dollar cost. The total for the run is tracked down to a millionth of a dollar.
- **Replay any past run.** Every run is saved. Open one from the history panel and the entire session replays — including everything its sub-agents did.
- **Compare versions.** Change the system prompt, the model, or which tools are available, and the system snapshots that as a new version. You can compare how the agent behaved before and after a change instead of guessing.

The companion **data-analyst agent** does the same for a spreadsheet: ask it a question about a sales dataset in plain English and it explores the data, runs the statistics, and — when a chart is the better answer — renders an interactive bar, line, or histogram chart on its own initiative.

---

## The system, in three layers

| Layer | What it is | Where it lives |
|---|---|---|
| **The product** | OrcView — a reusable SDK that turns any agent into a live, inspectable, recordable system | `orcview/`, `examples/` |
| **The proving ground** | A production-grade data-analyst agent where the reliability patterns were hardened | `data-analyst-agent/` |
| **The method** | A framework for directing a team of AI engineers, with decision ownership and escalation | `agents/`, `contracts/`, `.architecture/` |

These were built in that order, bottom-up: the agent loop was forged and stress-tested on a real task (analyzing data), then generalized into a clean SDK (OrcView), and the whole effort was run using the method in the third layer. The rest of this README is about the decisions behind each — *what* was chosen and *why*, not the code.

---

## By the numbers

| | |
|---|---|
| Reasoning loop | **4 states**, every transition observable |
| Event types streamed per run | **10** (reasoning, tool calls, results, metrics, …) |
| Agent nesting | **unbounded depth**, carried on **1 connection** |
| Cycle detection | **O(1)** per delegation |
| Cost resolution | tracked to **$0.000001** |
| Typical question | **~3 reasoning cycles · ~$0.01 · under 10 s** |
| Drill into a sub-agent | **client-side replay, <50 ms** — no server round-trip |
| Designed time-to-root-cause for a failed run | **under 2 minutes** |
| Reliability | **3 retries**, exponential backoff; hard loop guard at **12 cycles** |
| Analysis toolkit | **7 tools · 3 chart types** |
| Front end | **2 dashboards · 0 build steps · 0 frameworks** |
| Architecture method | **4 zoom levels**, living diagrams |

> Cost and latency figures are typical for a multi-step question against a current Claude model; the architectural figures are exact.

---

## Design decisions that matter

### 1. The agent is a state machine you can see — not a framework you can't

Most agent libraries hide the reasoning loop behind abstractions. We made the opposite choice: the loop is an explicit, four-state machine written out in plain code.

```
START → REASONING ⇄ ACTING → DONE
```

- **Reasoning** asks the model what to do next. If it asks for a tool, go to *Acting*. If it gives a final answer, go to *Done*.
- **Acting** runs the requested tools, collects the results, and goes back to *Reasoning*.

**Why this matters:** because the loop is explicit, every transition is a real event the system can emit. There's no hidden control flow to reverse-engineer when something misbehaves. The animated diagram in the UI isn't a decoration bolted on afterward — it's a faithful rendering of the exact states the agent actually moved through. The thing you watch *is* the thing that ran.

We rejected the alternative (a general agent framework with planners, memory modules, and routing layers) on purpose. For a system whose entire point is *visibility*, every layer of abstraction is a place a bug can hide.

### 2. A sub-agent is just a tool

An agent needs tools. An agent can also *be* a tool. We made those the same thing: any agent satisfies the same interface a tool does, so you compose a multi-agent system simply by handing one agent to another as a tool.

```python
researcher = Agent(label="Researcher", tools=[search, read_page])
writer     = Agent(label="Writer",     tools=[researcher, outline, draft])
```

The `Writer` can now delegate to the `Researcher` exactly as if it were calling a function. No router, no message bus, no orchestration config.

**Why this matters:** the manager agent never sees the researcher's internal reasoning — only its final answer. Each agent stays a clean, self-contained unit with a narrow job, which is precisely what keeps a multi-agent system debuggable. And because composition is just "pass an agent as a tool," there's no ceiling on depth: a three-tier hierarchy and a ten-tier one use the identical mechanism. The codebase explorer (manager → two specialists) is built from nothing more than this.

### 3. One stream carries the whole hierarchy

A nested agent system raises an awkward question: a deeply-buried sub-sub-agent does something interesting — how does that reach your screen?

The naive answer is a separate channel per agent, then stitching them together. We chose a single stream instead. When an agent delegates, the events from the sub-agent are tagged with a sub-run ID and passed up through the same pipe. The browser reads one connection and routes each event to the right view by its tag.

**Why this matters:** one connection scales to any number of nested agents with no extra plumbing, and the parent's saved recording automatically contains its children's full traces — which is the entire reason you can replay a months-old run and still drill into what a sub-agent did inside it. The tags are internal: they're stripped out before anything is shown to the model, so the agent's own reasoning is never polluted by observability metadata.

### 4. Safety rails, because agents misbehave in specific ways

Agents fail in a handful of predictable ways. Each gets a deliberate guard:

- **Infinite loops.** An agent can get stuck calling tools forever. Every run has a hard cycle ceiling (12 by default); hit it and the run stops with a clear error instead of draining your account.
- **Agents calling each other in circles.** If agent A delegates to B which delegates back to A, you have a cycle that never ends. Each delegation carries an immutable record of the chain that led to it; before running, an agent checks whether it's already in that chain and refuses if so. Because the record is immutable, two sub-agents running in parallel can never corrupt each other's view of the chain — a subtle correctness property that a shared mutable list would quietly break.
- **Runaway file access.** The codebase explorer can read any file the agent asks for — which is dangerous. Every path is resolved and checked against the project root *before the disk is touched*; anything that escapes (`../../etc/passwd`) is rejected. The agent is free to explore, inside a fence it cannot cross.
- **Config changing mid-flight.** Someone edits the system prompt in the UI while a run is in progress. Rather than risk a half-old, half-new execution, the configuration is photographed at the instant a run begins; that snapshot governs the whole run. Edits take effect on the next one.

### 5. Cancellation that doesn't corrupt anything

When you close the browser tab mid-run, the work behind it should stop — but not by killing a thread in the middle of an API call, which can leave state half-written.

We use a cooperative signal instead. Disconnecting trips a flag; the worker checks that flag at the safe boundary between reasoning steps and exits cleanly. Nothing is interrupted mid-operation, and a half-finished run never gets recorded as if it were real.

### 6. Failures get handled where they happen — and the agent gets coached, not just caught

The data-analyst agent is where the reliability patterns were hardened against a real workload, and it draws a sharp line between two kinds of failure:

- **Transient failures** (the model is rate-limited) are retried automatically — up to three times, waiting longer between each attempt. Permanent failures are surfaced immediately rather than retried into the same wall.
- **The agent's own mistakes** (asking for a column that doesn't exist, a malformed filter) don't crash anything. The tool catches the error and hands back a *useful* message — "Column not found: 'reigon'. Call summarize_dataset to see available columns." The agent reads that and corrects itself on the next step.

**Why this matters:** the second pattern is the difference between a brittle demo and something that works. A tool that just throws turns a typo into a dead run. A tool that explains what went wrong turns the model into a self-correcting system. Error messages here are treated as a coaching channel, not just a failure report.

### 7. Record first, then announce

Every run is saved to a local database so the history panel can replay it later. There's a small ordering trap: the browser, on seeing a run finish, immediately asks for the updated history. If the save hasn't happened yet, the run it's looking for isn't there.

So the save always completes *before* the "done" signal is sent. By the time the UI knows a run finished, the record is already on disk. A one-line ordering decision that removes an entire class of race condition.

### 8. Versions, so you can tell whether a change actually helped

Prompt engineering is mostly guessing unless you can compare. Every meaningful change — the model, the system prompt, the enabled tools — opens a new version, and every run is filed under the version that produced it.

**Why this matters:** "the new prompt is better" stops being a feeling. You can look at the runs under each version and compare cost, latency, and behavior directly. The system turns iteration into something you can measure instead of something you remember.

### 9. A dashboard with no build step

Both dashboards are a single HTML file each — no framework, no bundler, no build pipeline, just hand-written code and one small charting library loaded from a CDN. Roughly 5,700 lines of front end across the two, and you open them by pointing a browser at the server.

**Why this matters:** the front end's job is to render a fast-moving event stream without stuttering. A heavy framework re-rendering on every event is exactly the wrong tool. Plain, direct code that updates only what changed keeps the live view smooth, and a contributor can read the entire UI top to bottom without learning a toolchain first. The deliberate constraint — no build step — is what keeps it that way.

### 10. The agent decides when a picture beats a paragraph

The data-analyst agent has three charting tools alongside its statistical ones. Nothing tells it when to use them — the *model* decides, from the question, that "show me the trend" wants a line chart and "compare the regions" wants bars. When it picks a chart tool, the result renders as a real interactive chart instead of a wall of numbers.

**Why this matters:** the right output format is part of a good answer, and handing that judgment to the agent — rather than hard-coding it — is what makes the system feel like it understands the question, not just parses it.

---

## How it was built: directing a team of AI engineers

The third layer is the one that's easy to miss and the most unusual. The agents in `agents/` aren't agents the *software* runs — they're agents that *built the software*, organized like a real engineering team.

The premise: when you direct a fleet of AI coding agents, the failure mode isn't bad code — it's an agent confidently making a decision that wasn't theirs to make, and you discovering it three steps later. So the whole method is built around **owning the decisions that matter and delegating the rest.**

- **PROJECT.md** is the single source of truth. Every agent reads it before doing anything, so intent is stated once instead of re-explained constantly.
- **The Kickoff agent** interviews you, scopes the smallest useful first version, and — most usefully — surfaces *the one decision you'd most regret delegating*. That decision becomes an escalation rule baked into every other agent.
- **The Architect agent** keeps a living map of the system at four zoom levels (whole system → components → data flow → data shapes). It works in two modes: *Design* (propose a structure, wait for your approval before any code is written) and *Reflect* (after something's built, check it against the plan and flag where reality drifted). It never writes code — it only makes the structure visible so decisions are made with eyes open.
- **Expert agents** (a backend engineer, a frontend engineer) do the building, but each one carries an explicit charter: a list of what it's allowed to decide on its own, and a list of what it must stop and escalate. New dependency? Change to a shared interface? A choice between two approaches with real trade-offs? Escalate. Internal file layout? Decide freely.
- **Contracts** pin down the interfaces between components so two agents working in parallel can't quietly disagree about how their pieces fit together.

**Why this matters — and the proof it works:** the design rules these expert agents carry aren't aspirational; they're visible in the shipped code. The backend charter says *stream anything that takes more than two seconds* and *use the platform's SDK directly rather than a heavy abstraction layer* — and the agents do exactly that. The frontend charter says *no JavaScript-driven animation, it kills performance on a streaming UI* and *plain code unless a build step already exists* — and the dashboards are built precisely that way. You can read a principle in `agents/experts/` and then find it honored in the running system. The method isn't a story told after the fact; it left fingerprints.

There's even a self-assessment step. `evaluate.py` runs the codebase past an AI reviewer playing a demanding production engineer, grading it on the things that actually separate a real system from a toy: does it solve a concrete failure mode, can you debug a broken run quickly, is the failure handling more than a generic catch-all. The "under two minutes to find where a run broke" target in this README comes straight from that rubric — it's a goal the system was built to meet, not a number invented for the page.

---

## Build your own agent

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

agent.serve()   # → http://localhost:5050
```

The `@tool` decorator does the tedious part for you: it reads your function's type hints and docstring and writes the tool definition the model needs. A `str` becomes a text field, an `int` becomes a number, a fixed set of choices becomes a dropdown of allowed values, and any argument with a default becomes optional. You write a normal Python function; the schema is inferred.

Adding a sub-agent is just adding a tool:

```python
researcher = Agent(label="Researcher", tools=[search, read_page])
writer     = Agent(label="Writer",     tools=[researcher, outline, draft])

writer.serve()
```

---

## Project structure

```
orcview/                 the SDK — build observable, composable agents
  _tool.py                 @tool: infer a tool definition from a function
  _agent.py                the agent: state machine, composition, safety rails
  _server.py               live streaming, versioning, config
  _db.py                   run + version history
  static/index.html        the dashboard — diagram, timeline, drill-down, history

examples/
  codebase_explorer/       a 3-tier agent team that answers questions about code

data-analyst-agent/        the proving ground: the agent loop, hardened
  agent.py                   v1 — the naive loop, kept to show the starting point
  fsm_core.py                v2 — the instrumented state machine + reliability
  fsm_streaming.py           the live event stream
  server.py                  web server + persistence
  evaluate.py                grades the system against a production bar
  static/index.html          dashboard with live chart rendering

agents/                    the method: AI engineers, organized like a team
  kickoff.md                 scope the work, surface the risky decision
  architect.md               keep the system map honest
  experts/                   domain engineers with explicit decision boundaries
contracts/                 agreed interfaces between components
.architecture/             living diagrams at four zoom levels
```

---

## Run it

**The codebase explorer** (the deployed demo) — ask questions about any code directory; all file access is fenced to the current folder:

```bash
python3 -m examples.codebase_explorer        # → http://localhost:5050
# or, no browser:
python3 -m examples.codebase_explorer --headless "What does this project do?"
```

**The data-analyst agent** — ask questions about a sales dataset and get answers and charts:

```bash
cd data-analyst-agent
python3 server.py                            # → http://localhost:5050
```

Both need an Anthropic API key in a `.env` file (`ANTHROPIC_API_KEY=...`) and the two dependencies in `requirements.txt` (`pip install -r requirements.txt`). The app is container-ready — it binds to the port the host provides — so the same command that runs it locally runs it in production.
