---
name: moa-council
description: Explicit-only recorded deliberation for consequential shape, design, decide, and review work. Use only when the user invokes $moa-council (Codex) or /moa-council (Claude Code) by name.
disable-model-invocation: true
---

# MoA Council

Invoke only as `$moa-council <shape|design|decide|review> <task>` (Codex) or `/moa-council <shape|design|decide|review> <task>` (Claude Code). Similar prose is never activation.

Use two to four distinct read-only participant sessions. Start with blind proposals; then map claims, run only targeted critiques and claimant revisions (at most two rounds), collect final stances, request one journal-bound synthesis, and finalize through `scripts/moa_run.py`. The host chooses lenses and semantic conclusions; the runner validates public envelopes, records legal events, and computes the truthful disposition.

Keep prompts and records public and allowlisted. Do not request hidden reasoning, secrets, implementation, external actions, or tool use. Declare `prompt_only` unless verified filesystem isolation is available. Claude and Codex are advisory read-only lanes; OpenCode remains disabled until a matching certificate exists. A participant or nested context must not start a council.

Every run ends with exactly `brief.md`, append-only `transcript.md`, and write-once `decision.md`. Do not replace dissent with consensus. See [protocol](references/protocol.md) and [modes](references/modes.md).
