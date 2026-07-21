#!/usr/bin/env bash
# Install the kit: state dirs, bin symlinks, Claude Code hooks, agents, skill.
# Idempotent — safe to re-run after a git pull.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_HOME="${TASKLOOP_HOME:-$HOME/.taskloop}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CLAUDE_DIR="${CLAUDE_DIR:-$HOME/.claude}"

echo "== claude-workflow-kit install =="
echo "kit:        $KIT"
echo "state home: $STATE_HOME"

# 1. State directories
mkdir -p "$STATE_HOME"/{queue/{active,done,superseded},runs,logs,memory}

# 2. Bin symlinks
mkdir -p "$BIN_DIR"
for b in task-add task-list task-go drain-once run-manifest; do
  ln -sf "$KIT/bin/$b" "$BIN_DIR/$b"
done
echo "symlinked bins into $BIN_DIR (ensure it is on PATH)"

# 3. Hooks (idempotent; backs up settings.json first)
python3 "$KIT/hooks/install_hooks.py"

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
echo "  2. try: task-go \"add a smoke test\" --repo <your repo>"
echo "  3. optional MCP servers: see mcp/README.md"
echo "  4. optional timers: see systemd/README.md"
