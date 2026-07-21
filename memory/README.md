# Memory

The persistent memory tree: one markdown file per fact, indexed by
[MEMORY.md](MEMORY.md), readable and writable by hand or through the
[memory MCP server](../mcp/README.md).

[MEMORY_MODEL.md](MEMORY_MODEL.md) is the document that matters here —
what belongs in memory vs the queue vs git history, and the rules that
keep each store from rotting. `handoffs/{active,done,superseded}/` is a
ready-made home for session-to-session handoff notes (see the handoff
lifecycle in MEMORY_MODEL.md).
