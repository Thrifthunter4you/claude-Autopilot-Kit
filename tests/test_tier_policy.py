"""Drift lint: agents/tier_policy.yaml is the single source of truth for
model tiers — every agents/*.md frontmatter must agree with it, and every
policy row must point at a real agent file. A tier change that edits only
one side fails here, which is the point."""
import re
from pathlib import Path

import yaml

KIT = Path(__file__).resolve().parents[1]
AGENTS_DIR = KIT / "agents"
POLICY = yaml.safe_load((AGENTS_DIR / "tier_policy.yaml").read_text())

# Docs/templates in agents/ that are not agent definitions.
NON_AGENTS = {"AGENT_DESIGN.md", "agent_def_template.md", "README.md"}

_MODEL_RE = re.compile(r"^model:\s*(\S+)", re.M)


def frontmatter_model(path: Path) -> str:
    """The agent's `model:` line, or 'inherit' when deliberately absent."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---")
    front = parts[1] if len(parts) >= 3 else ""
    m = _MODEL_RE.search(front)
    return m.group(1) if m else "inherit"


def declared_agents() -> dict:
    return {p.stem: p for p in AGENTS_DIR.glob("*.md")
            if p.name not in NON_AGENTS}


def test_policy_is_well_formed():
    assert set(POLICY) == {"tiers", "agents"}
    for tier, spec in POLICY["tiers"].items():
        assert spec.get("model"), f"tier '{tier}' names no model"
    for name, row in POLICY["agents"].items():
        assert row.get("tier") in POLICY["tiers"], \
            f"agent '{name}' uses undeclared tier '{row.get('tier')}'"
        assert row.get("rationale"), \
            f"agent '{name}' has no rationale — a tier with no stated why " \
            "gets 'fixed' by the next confused reader"


def test_every_agent_file_matches_its_policy_tier():
    problems = []
    for name, path in declared_agents().items():
        row = POLICY["agents"].get(name)
        if row is None:
            problems.append(f"{path.name}: not in tier_policy.yaml — every "
                            "fleet agent must declare a tier")
            continue
        want = POLICY["tiers"][row["tier"]]["model"]
        got = frontmatter_model(path)
        if got != want:
            problems.append(
                f"{path.name}: model '{got}' but policy tier "
                f"'{row['tier']}' says '{want}' — change both together")
    assert not problems, "\n".join(problems)


def test_every_policy_row_has_an_agent_file():
    missing = [name for name in POLICY["agents"] if name not in
               declared_agents()]
    assert not missing, \
        f"policy rows with no agents/*.md file: {missing} — remove the row " \
        "or restore the agent"
