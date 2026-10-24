# Public MCP release

Updated 2026-10-23 by Codex.

Public pages live below `/mcp/`. Browser samples call actual MCP tools and render their structured responses. They do not pretend to generate model answers. Visitors may connect the endpoints to their existing AI host; no new API credentials are required.

Public endpoints always use bundled synthetic data. They do not expose local-file roots, the original web application's uploaded projects, CLI sessions, or user databases. No request/response content, tool receipts, source trees or trace content is persisted by these routes. Hosting infrastructure can retain operational metadata. Existing web-app routes retain their separate API/storage behavior.

Local installation uses versioned `.tgz` / `.whl` archives served by these same repos. The release packages contain only application code and bundled synthetic examples; no credentials, private datasets or session logs. The tools return evidence to the user's AI host, whose own retention and provider policies apply.

The CodeArchitect Node process serves `/mcp/codearchitect/`, `/mcp/sqlharness/`, their assets/downloads, and `/mcp/codearchitect/server`. Its `/mcp/sqlharness/server` proxy forwards only MCP requests to the fixed OrcView Python service. It never forwards Authorization or Cookie headers. No caller-provided upstream URL is accepted.

The OrcView Python process serves `/mcp/orcview/` and wraps its existing Flask app in an ASGI gateway. `/mcp/orcview/server` uses synthetic traces. `/mcp/sqlharness/server` uses the same Python SQLHarness authority and SQLGlot validation as the local package, with a temporary synthetic shop database. An ASGI lifespan owns both MCP applications and sample cleanup. The original Flask routes remain available.

HTTP requests are size-bounded and host/origin checked. Tool results are bounded. Concurrent public MCP calls are capped. The database connection is read-only, administrative SQL such as ATTACH is rejected, and SQLite scalar allocations are bounded. These are prototype controls, not a claim of general process isolation or production security certification.

Local OrcView reads existing project-scoped CLI logs without copying them. Missing CLI internals and billed costs are not invented. The published MCP-only package does not enable the original agent SDK's recording behavior. Runtime recording controls in the legacy SDK are outside this release's privacy claims.

Tests cover MCP discovery/calls, samples, path traversal and hidden-file rejection, content defaults, blocked SQL writes, public downloads, invalid origins, oversized requests, and unchanged legacy Flask routes. The original Node application tests also run before publication.
