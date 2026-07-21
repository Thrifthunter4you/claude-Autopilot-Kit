#!/usr/bin/env python3
"""Memory MCP server — persistent file-based memory for Claude sessions.

One memory = one markdown file with YAML frontmatter, in a flat directory:

    memory/
      MEMORY.md                      the index (one line per memory)
      <slug>.md                      individual memories
      handoffs/{active,done,superseded}/   session-to-session baton files

Tools exposed:
    memory_save(name, description, type, body)   write/update a memory + index
    memory_search(query, k)                      keyword-scored recall
    memory_read(name)                            full body of one memory
    memory_list()                                the index

Design notes:
  - No embeddings, no database — plain files + deterministic keyword scoring
    (token overlap weighted by field: name > description > body).  This keeps
    the server dependency-light and the memory tree fully greppable/editable
    by hand.  If you outgrow it, swap the search function for an embedding
    index; the file format doesn't change.
  - The MEMORY.md index is what a session loads at start; keep it small.
    Memory content lives in the individual files, never in the index.

Run (stdio):  python3 mcp/memory_mcp.py
Register:     claude mcp add memory -- python3 <kit>/mcp/memory_mcp.py
Requires:     pip install mcp
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(os.environ.get(
    "MEMORY_DIR", Path(os.environ.get("TASKLOOP_HOME",
                                      Path.home() / ".taskloop")) / "memory"))
INDEX = ROOT / "MEMORY.md"
TYPES = {"user", "feedback", "project", "reference"}

mcp = FastMCP("memory")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:64].rstrip("-") or "memory"


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _parse(path: Path):
    """Return (frontmatter_dict, body) — tolerant of missing frontmatter."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    front = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            front[k.strip()] = v.strip()
    return front, m.group(2)


def _rebuild_index() -> None:
    lines = ["# MEMORY.md — index (one line per memory; content lives in the "
             "files)\n"]
    for p in sorted(ROOT.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        front, _ = _parse(p)
        desc = front.get("description", "")
        lines.append(f"- [{p.stem}]({p.name}) — {desc}")
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


@mcp.tool()
def memory_save(name: str, description: str, type: str = "project",
                body: str = "") -> str:
    """Save (or update) one memory: a single fact/preference/pointer.

    name: short kebab-case slug.  description: one-line summary used for
    recall relevance.  type: user | feedback | project | reference.
    body: the fact itself; for feedback/project include why + how to apply.
    """
    if type not in TYPES:
        return f"type must be one of {sorted(TYPES)}"
    ROOT.mkdir(parents=True, exist_ok=True)
    slug = _slugify(name)
    path = ROOT / f"{slug}.md"
    existed = path.exists()
    path.write_text(
        f"---\nname: {slug}\ndescription: {description}\n"
        f"metadata:\n  type: {type}\n---\n\n{body.strip()}\n",
        encoding="utf-8")
    _rebuild_index()
    return f"{'updated' if existed else 'created'} {path}"


@mcp.tool()
def memory_search(query: str, k: int = 5) -> str:
    """Recall the k most relevant memories for a query (keyword-scored).

    Returns name, description, and a body excerpt per hit — call
    memory_read(name) for a full body.
    """
    q = _tokens(query)
    if not q:
        return "(empty query)"
    scored = []
    for p in sorted(ROOT.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        front, body = _parse(p)
        score = (3 * len(q & _tokens(front.get("name", p.stem)))
                 + 2 * len(q & _tokens(front.get("description", "")))
                 + len(q & _tokens(body)))
        if score > 0:
            scored.append((score, p.stem, front.get("description", ""),
                           body.strip()[:400]))
    if not scored:
        return "(no matching memories)"
    scored.sort(reverse=True)
    out = []
    for score, name, desc, excerpt in scored[:k]:
        out.append(f"## {name} (score {score})\n{desc}\n\n{excerpt}\n")
    return "\n".join(out)


@mcp.tool()
def memory_read(name: str) -> str:
    """Read one memory's full content by name (slug)."""
    path = ROOT / f"{_slugify(name)}.md"
    if not path.exists():
        return f"(no memory named '{name}')"
    return path.read_text(encoding="utf-8")


@mcp.tool()
def memory_list() -> str:
    """The memory index: every memory's name + one-line description."""
    if not INDEX.exists():
        _rebuild_index() if ROOT.exists() else None
    if not INDEX.exists():
        return "(no memories yet)"
    return INDEX.read_text(encoding="utf-8")


if __name__ == "__main__":
    mcp.run()
