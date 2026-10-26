"""taskloop — a file-based autonomous task queue for Claude Code.

Tasks are markdown files ("handoffs") with a YAML `loop:` frontmatter block.
Sessions and hooks file tasks into the queue; the drainer claims eligible
tasks and runs each one in a fresh `claude -p` worker process, chunked so no
single context window ever holds the whole run.
"""

__version__ = "0.5.0"
