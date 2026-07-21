#!/usr/bin/env python3
"""Context MCP server — budget-bounded context packs for a task.

Given a task/question, assembles a single high-signal text pack from three
local, zero-API-cost legs:

    MEMORY   — keyword hits from the kit's memory tree (rules, decisions)
    CODE     — grep hits across the target repo (file:line + matching lines)
    HISTORY  — recent git log subjects touching matched files

The pack is a SEED, not a sole source: it cites file:line so the agent goes
straight to the right place and Reads the citation for exact values.  Hand it
to a subagent at spawn time and it skips the cold-explore phase entirely.

Tools exposed:
    build_pack(task, repo, budget)   the full three-leg pack
    grep_context(query, repo, k)     just the CODE leg (cheaper)

Design notes:
  - Pure grep + git — no index to build, works on any repo instantly.  If you
    outgrow it, replace the CODE leg with an AST/embedding index; the tool
    contract (a cited text pack) doesn't change.

Run (stdio):  python3 mcp/context_mcp.py
Register:     claude mcp add context -- python3 <kit>/mcp/context_mcp.py
Requires:     pip install mcp
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

MEMORY_DIR = Path(os.environ.get(
    "MEMORY_DIR", Path(os.environ.get("TASKLOOP_HOME",
                                      Path.home() / ".taskloop")) / "memory"))
DEFAULT_REPO = os.environ.get("CONTEXT_REPO", "")

mcp = FastMCP("context")

_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "for", "on",
              "with", "is", "are", "it", "this", "that", "be", "as", "at",
              "by", "from", "into", "how", "what", "why", "when", "where",
              "does", "do", "add", "fix", "make", "should", "can", "we"}


def _terms(text: str, cap: int = 6) -> list:
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    seen, out = set(), []
    for t in toks:
        tl = t.lower()
        if tl in _STOPWORDS or tl in seen:
            continue
        seen.add(tl)
        out.append(t)
    # Longer identifiers first — they're the most discriminating.
    out.sort(key=len, reverse=True)
    return out[:cap]


def _grep(repo: str, term: str, max_lines: int = 40) -> list:
    try:
        r = subprocess.run(
            ["git", "-C", repo, "grep", "-n", "-I", "-i", "--", term],
            capture_output=True, text=True, timeout=20)
        lines = r.stdout.splitlines()
    except Exception:
        return []
    return lines[:max_lines]


def _git_log(repo: str, paths: list, n: int = 8) -> list:
    cmd = ["git", "-C", repo, "log", f"-{n}", "--pretty=%h %ad %s",
           "--date=short"]
    if paths:
        cmd += ["--"] + paths[:10]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.stdout.splitlines()
    except Exception:
        return []


def _memory_hits(query: str, k: int = 4) -> list:
    q = set(re.findall(r"[a-z0-9]+", query.lower()))
    scored = []
    if not MEMORY_DIR.exists():
        return []
    for p in sorted(MEMORY_DIR.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        score = len(q & set(re.findall(r"[a-z0-9]+", text.lower())))
        if score > 2:
            scored.append((score, p.name, text.strip()[:600]))
    scored.sort(reverse=True)
    return scored[:k]


@mcp.tool()
def build_pack(task: str, repo: str = "", budget: int = 6000) -> str:
    """Build a budget-bounded context pack (memory + code + history legs)
    for a task.  `repo` defaults to the CONTEXT_REPO env var.  The pack cites
    file:line — Read the citations for exact values; never treat the pack as
    exhaustive."""
    repo = repo or DEFAULT_REPO
    parts = [f"# CONTEXT PACK — for: {task}",
             "# Seed/index, not a sole source: Read the cited file:line "
             "for exact values.\n"]

    hits = _memory_hits(task)
    if hits:
        parts.append("## MEMORY — rules & context (verbatim; verify if stale)")
        for _score, name, excerpt in hits:
            parts.append(f"[{name}]\n{excerpt}\n")

    matched_files: list = []
    if repo and Path(repo).exists():
        parts.append("## CODE — grep hits (cite these file:line)")
        for term in _terms(task):
            lines = _grep(repo, term, max_lines=12)
            if lines:
                parts.append(f"### term: {term}")
                parts.extend(lines)
                for ln in lines:
                    f = ln.split(":", 1)[0]
                    if f not in matched_files:
                        matched_files.append(f)
        log = _git_log(repo, matched_files)
        if log:
            parts.append("\n## HISTORY — recent commits touching matches")
            parts.extend(log)
    elif repo:
        parts.append(f"(repo '{repo}' not found)")

    pack = "\n".join(parts)
    if len(pack) > budget:
        pack = pack[:budget] + "\n[...pack truncated at budget...]"
    return pack


@mcp.tool()
def grep_context(query: str, repo: str = "", k: int = 30) -> str:
    """Just the CODE leg: grep the repo for the query's identifiers and
    return file:line hits."""
    repo = repo or DEFAULT_REPO
    if not repo or not Path(repo).exists():
        return f"(repo '{repo}' not found — pass repo or set CONTEXT_REPO)"
    out = []
    for term in _terms(query):
        lines = _grep(repo, term, max_lines=k)
        if lines:
            out.append(f"### {term}")
            out.extend(lines)
    return "\n".join(out) or "(no hits)"


if __name__ == "__main__":
    mcp.run()
