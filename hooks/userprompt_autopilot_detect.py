#!/usr/bin/env python3
"""UserPromptSubmit hook: auto-detect a NEW build initiative and steer it into
the task loop — so you never hand-run intake/drain scripts.

The standing policy this encodes: auto-queue CLEAR build asks, confirm FUZZY
ones.  The hook is the always-on detector; it classifies the prompt and
injects an <autopilot-detect> directive; the AGENT does the actuation
(extract a one-line goal, pick the repo, run bin/task-go, watch progress).
Detection lives in a hook because a hook fires on EVERY prompt and survives
context compaction — that is what makes "the session knows automatically".

  clear  -> agent auto-queues + kicks the loop, no questions (skips trivial
            one-liners / in-session continuations, which it handles inline)
  fuzzy  -> agent asks ONE concise "hand this to the task loop?" question
  none   -> inject nothing (chatter, acks, questions, slash/bash, automation)

Contract: fail-open and FAST.  Never blocks the prompt.  Any error -> exit 0
silent.  Pure-python, no I/O, no network — just regex classification.

Wire it (see hooks/install_hooks.py):
  "UserPromptSubmit": python3 <kit>/hooks/userprompt_autopilot_detect.py
"""
import json
import re
import sys

MIN_PROMPT_CHARS = 15

# --- Never fire inside harness-injected blocks or automation contexts. ---
# These are NOT genuine user initiatives.  Crucially this includes the task
# loop's own WORKER prompt: a background `claude -p` worker receives a prompt
# too, and without this guard the detector would recursively queue itself.
_NOISE_MARKERS = (
    "<task-notification>", "<tool-use-id>", "<command-name>", "<command-message>",
    "<local-command", "<system-reminder", "</command", "</task", "<function_results",
    # task-loop worker + automation markers (recursion guard):
    "autonomous task-loop worker",
    "invoke the `autopilot` skill", "loop_result:", "<autopilot-detect",
)
# Session bootstrap / handoff prompts carry build language as CONTEXT, not as
# a fresh ask — don't auto-spawn a run off a pasted handoff.
_BOOTSTRAP_RE = re.compile(
    r"(?im)^\s*(?:project|task|context|handoff|objective|goal|mission|charter|"
    r"recall_hint|originsessionid)\s*:"
    r"|you\s+are\s+(?:continuing|picking\s+up|an?\s|the\s|working\s+on)"
    r"|canonical\s+handoff|pick\s+up\s+where|new\s+session\s+with\s+handoff"
    r"|paste\s+the\s+block\s+below"
)

# --- Acknowledgements / meta / in-session continuations -> none. ---
# "do it" / "go" / "yes" are approvals of a PRIOR proposal -> handle inline,
# never a fresh queued run.
_ACK_RE = re.compile(
    r"^(?:y|n|ok|okay|yes|no|go|go\s+ahead|do\s+it|sure|yep|yeah|nope|stop|wait|"
    r"hold\s+on|pause|cancel|nvm|never\s+mind|thanks?|thank\s+you|ty|thx|"
    r"sounds?\s+good|perfect|great|nice|lgtm|continue|next|proceed|done)\b"
    r"[\s.!]*$",
    re.IGNORECASE,
)
# Meta / operational asks about EXISTING things -> discussion, not an initiative.
_META_RE = re.compile(
    r"(?i)^\s*(?:commit|push|pull|run\s+(?:the\s+)?tests?|test\s+it|show\s+me|show|"
    r"explain|describe|summari[sz]e|tell\s+me|recall|remind\s+me|status|"
    r"what\s+(?:is|are|does|do|happened|was)|why\s|how\s+(?:does|do|did|is|are)|"
    r"where\s+(?:is|are|does)|who\s|when\s|which\s|is\s+(?:it|there|this)|"
    r"can\s+you\s+(?:show|tell|explain|find|check|see|look))\b"
)

# --- Hedged / exploratory -> fuzzy (confirm). ---
_HEDGE_RE = re.compile(
    r"(?i)\b(?:thinking\s+about|think\s+(?:we|i)|what\s+if|what\s+about|maybe|"
    r"perhaps|i\s+wonder|wondering|could\s+we|should\s+we|would\s+it|might\s+want|"
    r"eventually|someday|some\s+day|considering|toying\s+with|brainstorm|"
    r"explore\s+(?:the\s+)?idea|not\s+sure\s+if|kind\s+of\s+want|sort\s+of\s+want|"
    r"we\s+might|i\s+might|idea\s*:|one\s+idea|half-baked|rough\s+idea)\b"
)

# --- Concrete build intent.  Leading imperative verb = strongest signal. ---
_BUILD_VERBS = (
    "build", "implement", "create", "add", "write", "make", "develop", "scaffold",
    "refactor", "migrate", "port", "optimi", "integrate", "generate", "automate",
    "set up", "setup", "set-up", "wire up", "wire-up", "stand up", "hook up",
    "put together", "design", "draft", "fix", "rework", "rebuild", "redesign",
)
_LEADING_BUILD_RE = re.compile(
    r"(?i)^\s*(?:please\s+|let'?s\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+(?:you\s+)?to\s+|"
    r"i\s+(?:need|wanna|want)\s+|we\s+(?:need|should)\s+|now\s+|next\s+|also\s+|go\s+)?"
    r"(" + "|".join(v.replace(" ", r"\s+") for v in _BUILD_VERBS) + r")\b"
)
# Build vocabulary anywhere (weaker; supports fuzzy fallback).
_BUILD_SIGNAL_RE = re.compile(
    r"(?i)\b(?:build|implement|create|add|write|make|develop|refactor|migrate|"
    r"feature|system|tool|script|pipeline|service|endpoint|dashboard|integration|"
    r"module|component|automation|workflow|new\s+(?:feature|tool|system|service))\b"
)
_QUESTION_RE = re.compile(r"\?\s*$")


def classify(prompt: str) -> str:
    """Return 'clear' | 'fuzzy' | 'none'.  Conservative: when torn between
    clear and fuzzy -> fuzzy (safe: just asks); between fuzzy and none -> none."""
    t = (prompt or "").strip()
    if len(t) < MIN_PROMPT_CHARS:
        return "none"
    if t.startswith(("/", "!")):
        return "none"
    if any(m in t.lower() for m in _NOISE_MARKERS):
        return "none"
    if _BOOTSTRAP_RE.search(t):
        return "none"
    first_line = t.splitlines()[0].strip()
    if _ACK_RE.match(first_line):
        return "none"
    if _META_RE.match(t):
        return "none"

    hedged = bool(_HEDGE_RE.search(t))
    question = bool(_QUESTION_RE.search(t))
    leading_build = bool(_LEADING_BUILD_RE.match(t))
    build_signal = bool(_BUILD_SIGNAL_RE.search(t))

    # Hedged or phrased as a question -> never auto-run.  Confirm only if
    # there's real build intent; otherwise it's just discussion.
    if hedged or question:
        return "fuzzy" if (build_signal or leading_build) else "none"

    # Plain imperative build directive with substance -> auto-run candidate.
    if leading_build and len(t.split()) >= 3:
        return "clear"

    # Build vocabulary but not a clean leading directive -> confirm.
    if build_signal:
        return "fuzzy"

    return "none"


_CLEAR_MSG = (
    "<autopilot-detect verdict=\"clear\">\n"
    "This reads as a NEW build initiative. Per the operator's standing policy "
    "(auto-run clear asks), proceed WITHOUT asking — UNLESS it's a trivial "
    "one-liner or a continuation of work already underway in THIS session (do "
    "those inline; do NOT queue them).\n"
    "If it is a genuine multi-step initiative:\n"
    "  1. Extract a one-line goal; pick the repo (the kit's default_repo, or "
    "another if the prompt names one).\n"
    "  2. Run: task-go \"<goal>\" --repo <repo>\n"
    "  3. Attach a background progress watcher on the run manifest and relay "
    "progress.\n"
    "The task loop runs the work in FRESH background contexts (chunked, no "
    "human handoff), so this session stays light.\n"
    "</autopilot-detect>"
)
_FUZZY_MSG = (
    "<autopilot-detect verdict=\"fuzzy\">\n"
    "This MIGHT be a new build initiative but it's exploratory/hedged. Per the "
    "operator's standing policy (confirm fuzzy ones), ask ONE concise question: "
    "hand this to the task loop (auto-queue + chunked background run) or just "
    "discuss / do inline? Extract the candidate goal so the question is "
    "concrete. Do NOT queue until they say go.\n"
    "</autopilot-detect>"
)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    prompt = payload.get("prompt") or payload.get("user_prompt") or ""
    try:
        verdict = classify(prompt)
    except Exception:
        sys.exit(0)
    if verdict == "none":
        sys.exit(0)
    msg = _CLEAR_MSG if verdict == "clear" else _FUZZY_MSG
    out = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": msg,
        }
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
