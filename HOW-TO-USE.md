# How to Use This Template

Copy this folder, rename it to your project name, then follow these steps.

## Step 1 — Define the Project

Fill in `PROJECT.md`. Every agent reads this before doing anything.
The more specific you are here, the less you'll need to repeat yourself.

## Step 2 — Run the Kickoff

Activate `agents/kickoff.md`. It will interview you about what's missing from PROJECT.md
and propose a team of agents and an MVP boundary. Approve or adjust before moving on.

## Step 3 — Set Up the Architecture

Activate `agents/architect.md`. Start with the system diagram (`.architecture/system.json`).
Get a proposed diagram approved before any code is written.

## Step 4 — Activate Expert Agents

For each domain in the build:
- Use `agents/expert-template.md` to draft a new expert
- Or use one of the pre-built experts in `agents/experts/`
- Fill in the tooling landscape table — this is the most important part
- Get EM approval before activating

Pre-built experts:
- `agents/experts/frontend-expert.md` — UI/UX, dashboards, single-file or component-based
- `agents/experts/backend-expert.md` — Python APIs, async fetching, caching, LLM integration

## Step 5 — Build

Agents work within their domain. They escalate decisions they don't own.
Architect reflects diagrams as things get built.
You stay in the decision seat — not the implementation seat.

## Folder Structure After Setup

```
[project-name]/
  PROJECT.md                  ← project definition (fill in first)
  HOW-TO-USE.md
  agents/
    kickoff.md
    architect.md
    expert-template.md
    experts/
      frontend-expert.md
      backend-expert.md
      [your-new-expert].md    ← generated from expert-template.md
  .architecture/
    system.json               ← start here
    components/               ← add as needed
    flows/                    ← add as needed
    data/                     ← add as needed
  contracts/
    README.md
    [interface-name].md       ← add when two components share a boundary
  [your-code]/                ← the actual project lives here
```

## What's Not in This Template

- `learner/profile.md` — lives at the vault root, injected into teaching agents by the Learning Orchestrator
- `tools/spec.md` and `tools/viewer.html` — live at the vault root, used by all projects
- The Learning Orchestrator and Stakeholder Readiness Coach — vault-level agents, not project-specific
