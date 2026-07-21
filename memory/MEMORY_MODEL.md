# The memory model — what lives where

Four stores, each with one job. The discipline of keeping them separate is
what keeps every one of them useful.

| Store | Job | Loaded when |
|---|---|---|
| **Memory** (`memory/*.md` + `MEMORY.md` index) | rules, mental model, preferences, reference — things that shape how the agent *thinks* | index at session start; bodies on demand |
| **Queue/handoffs** (`queue/active/…`) | session-to-session baton — things that shape what the agent *does next* | via `task-list` / hooks, never bulk-loaded |
| **Git history** | what happened | on demand (`git log`) |
| **Lessons** (optional `memory/lessons/`) | debugging gotchas & diagnostic patterns | searched before debugging, never auto-loaded |

Rules that keep it healthy:

1. **One memory = one file = one fact.** Frontmatter carries `name`,
   `description` (the recall key), and `type`
   (user | feedback | project | reference). The body carries the fact; for
   feedback/project memories include **why** and **how to apply**.

2. **The index is a table of contents, never a content store.** `MEMORY.md`
   gets one line per memory. Cap it (~200 lines); when it grows past that,
   archive settled entries. A session loads the index cheaply and pulls
   bodies only when relevant.

3. **Don't write what another store already records.** Deployment notes are
   git history. Code structure is the code. If it's derivable, don't
   memorize it — memories go stale and then lie.

4. **Queue items are not memories.** "Found X, should fix it" goes in the
   QUEUE as a runnable task (`task-add`), not in memory as a note. The
   stop-hook nudges exactly this.

5. **Memories are claims about the past.** When recalling one that names a
   file, flag, or function — verify it still exists before acting on it.

6. **Update in place; delete what's wrong.** Before saving, check for an
   existing memory that covers the topic and update it rather than filing a
   near-duplicate. A wrong memory is worse than no memory.

## Handoff lifecycle

`queue/active/` holds both runnable tasks and in-flight handoffs. When a
task completes, the drainer moves it to `queue/done/`. A handoff replaced by
a newer one goes to `queue/superseded/`. Periodically archive `done/` (e.g.
after 30 days) — the queue directories should stay small enough to scan.

## Using the memory MCP

`mcp/memory_mcp.py` exposes the same tree as tools
(`memory_save/search/read/list`), so agents can save and recall memories
without knowing the file layout. The files stay plain markdown — hand-edit
them freely; the server rebuilds the index on every save.
