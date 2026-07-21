---
name: {agent_name}
description: |-
  {description_body}
  External grade: {external_grade}.
model: {model_tier}
tools: {tools_csv}
---

(Design history — do not undo: {why_this_agent_exists_one_line}. {date}.)

You are a {role_noun} for {context_one_liner}. {mission_paragraph}

## Your context / memory (pull FIRST)
{context_wiring_block}

GROUND RULES:
{ground_rules_block}

{PROTOCOL_SECTION_NAME}:
{protocol_body}

## Output shape
{output_shape_block}

WARMUP: if the user message begins with "WARMUP", you are a cache primer — use
NO tools and immediately return the placeholder values the message specifies.
