#!/usr/bin/env bash
# Install the kit: state dirs, bin symlinks, Claude Code hooks, agents, skill.
# Idempotent — safe to re-run after a git pull.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_HOME="${TASKLOOP_HOME:-$HOME/.taskloop}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"
PROJECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="${2:?--project needs a path}"; shift 2 ;;
    --project=*) PROJECT="${1#--project=}"; shift ;;
    -h|--help)
      echo "usage: ./install.sh [--project <repo>]"
      echo "  --project  also enable the hooks for that repository"
      echo "             (<repo>/.claude/settings.local.json); re-run per project"
      exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

echo "== claude-workflow-kit install =="
echo "kit:        $KIT"
echo "state home: $STATE_HOME"

# 1. State directories
mkdir -p "$STATE_HOME"/{queue/{active,done,superseded},runs,logs,memory}

# 2. Bin symlinks
mkdir -p "$BIN_DIR"
for b in task-add task-list task-go drain-once run-manifest toolbox token-audit spill noise-floor hook-cost; do
  ln -sf "$KIT/bin/$b" "$BIN_DIR/$b"
done
echo "symlinked bins into $BIN_DIR (ensure it is on PATH)"

# 3. Hooks — a PER-PROJECT opt-in (<project>/.claude/settings.local.json).
#    ./install.sh --project ~/dev/myapp   enables them for that repository;
#    without --project nothing is armed and a hint is printed. Re-run per
#    project. (`python3 hooks/install_hooks.py --global` exists for people who
#    really want every session on the machine hooked, and says what it does.)
if [ -n "$PROJECT" ]; then
  python3 "$KIT/hooks/install_hooks.py" --project "$PROJECT"
else
  echo "hooks: not installed — enable per project with:"
  echo "         ./install.sh --project <repo>   (or python3 $KIT/hooks/install_hooks.py --project <repo>)"
fi
# Upgrading from a kit that wrote ~/.claude/settings.json (<= v0.2)? Offer the
# migration instead of silently editing a machine-wide file.
if [ -f "$CLAUDE_DIR/settings.json" ] && grep -q "$KIT/hooks/" "$CLAUDE_DIR/settings.json" 2>/dev/null; then
  echo "hooks: $CLAUDE_DIR/settings.json still carries machine-wide kit hooks from an"
  echo "       earlier version — remove ONLY those with:"
  echo "         python3 $KIT/hooks/install_hooks.py --remove-global"
fi

# 4. Agents + skill (symlink so git pull updates them).
# Non-clobber: an existing REAL file/dir with the same name is yours — the
# installer warns and skips it; only absent paths and prior symlinks are
# (re)linked.
mkdir -p "$CLAUDE_DIR/agents" "$CLAUDE_DIR/skills"
for a in "$KIT"/agents/*.md; do
  base="$(basename "$a")"
  case "$base" in
    AGENT_DESIGN.md|agent_def_template.md) continue ;;
  esac
  dest="$CLAUDE_DIR/agents/$base"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "SKIP $dest (exists and is not a symlink — yours to keep)"
  else
    ln -sf "$a" "$dest"
  fi
done
skill_dest="$CLAUDE_DIR/skills/autopilot"
if [ -e "$skill_dest" ] && [ ! -L "$skill_dest" ]; then
  echo "SKIP $skill_dest (exists and is not a symlink — yours to keep)"
else
  ln -sfn "$KIT/skills/autopilot" "$skill_dest"
fi
echo "linked agents + autopilot skill into $CLAUDE_DIR"

# 5. Config starter
if [ ! -f "$STATE_HOME/config.yaml" ]; then
  cp "$KIT/config.example.yaml" "$STATE_HOME/config.yaml"
  echo "wrote starter config: $STATE_HOME/config.yaml  <-- set default_repo"
fi

echo
echo "Done. Next steps:"
echo "  1. edit $STATE_HOME/config.yaml (default_repo at minimum)"
echo "  2. enable hooks where you want them: ./install.sh --project <repo>"
echo "  3. try: task-go \"add a smoke test\" --repo <your repo>"
echo "  4. optional MCP servers: see mcp/README.md"
echo "  5. optional timers: see systemd/README.md"
