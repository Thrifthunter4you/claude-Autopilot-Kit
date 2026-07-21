# MCP servers

Two stdio MCP servers, both dependency-light (`pip install mcp`, plain
files, no embedder/database). Register them with the commands below, then
confirm with `claude mcp list`.

## memory — persistent file-based memory

```bash
# from the kit root:
claude mcp add memory -- python3 "$PWD/mcp/memory_mcp.py"
```

| Tool | What it does |
|---|---|
| `memory_save(name, description, type, body)` | write/update one memory file + rebuild the index |
| `memory_search(query, k)` | keyword-scored recall (name > description > body) |
| `memory_read(name)` | full body of one memory |
| `memory_list()` | the MEMORY.md index |

Memory tree defaults to `~/.taskloop/memory/` (override: `MEMORY_DIR`).
Files are plain markdown with frontmatter — hand-edit freely; the index is
rebuilt on every save. Conventions:
[`../memory/MEMORY_MODEL.md`](../memory/MEMORY_MODEL.md).

## context — budget-bounded context packs

```bash
# from the kit root:
claude mcp add context -e CONTEXT_REPO=/path/to/your/repo \
  -- python3 "$PWD/mcp/context_mcp.py"
```

| Tool | What it does |
|---|---|
| `build_pack(task, repo, budget)` | one text pack: MEMORY hits + CODE grep hits (file:line) + git HISTORY of matched files |
| `grep_context(query, repo, k)` | just the CODE leg |

Use it to seed subagents: build a pack for the task and prepend it to the
subagent's prompt — the agent goes straight to cited file:line locations
instead of cold-exploring the repo. The pack is a seed/index, never a sole
source: agents must Read the citations for exact values.

## State awareness (not an MCP — hooks + a mailbox)

- [`../hooks/session_start_context.py`](../hooks/session_start_context.py)
  briefs each new session: queue snapshot, next runnable task, recent
  mailbox messages.
- [`../state/session_mailbox.py`](../state/session_mailbox.py) is the
  cross-session channel: post `claim`
  before multi-file changes in a shared repo, `landing` after you ship;
  sessions read it at start (and you can add a PreToolUse hook to surface
  new messages mid-session).

## Upgrade path

Both servers are deliberately the simplest thing that works. If you outgrow
them, keep the tool contracts and swap the internals: an embedding index
behind `memory_search`, an AST index behind the CODE leg. Agents won't
notice.
