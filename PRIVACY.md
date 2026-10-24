# OrcView privacy boundaries

The MCP entry point reads only explicitly scoped local traces/CLI logs and does not save requests, results, or copied traces, call model providers, or read authentication files. Content is omitted unless explicitly requested. The public MCP endpoint exposes synthetic traces only. Your AI application and its model provider control their own retention.

The hosted web demo calls Anthropic using submitted questions and selected bundled example source. It cannot explore arbitrary server files. Configuration and at most 20 runs are isolated behind an unguessable HttpOnly SameSite browser cookie and held in process memory. Sessions expire after one hour of inactivity; cleanup occurs on a subsequent request or process exit. Questions are submitted in a POST body, not an EventSource URL. Hosting metadata/provider policies still apply.

The SDK emits events in memory. SQLite run recording is disabled unless the local operator explicitly constructs `Agent(..., record_runs=True)`. Opted-in recordings contain questions, configuration, tool inputs/results, and metrics in `~/.orcview/runs.db`; retention/deletion is the operator's responsibility. Child agents must opt in separately.
