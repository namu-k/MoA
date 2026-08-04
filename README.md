# MoA Council

`moa-council` is an explicit-only Agent Skill for recorded deliberation among multiple independent agent sessions. It is designed for consequential work where several defensible answers may exist and where dissent, evidence gaps, and authority boundaries must remain visible.

The skill separates semantic judgment from deterministic mechanics:

- The host selects the task, evidence allowlist, participant lenses, and final authority boundary.
- Two to four read-only participant sessions submit public proposals and targeted critiques.
- `scripts/moa_run.py` validates public envelopes, phase order, quorum, references, journal integrity, and the final disposition.
- Each completed run leaves an auditable brief, append-only transcript, and write-once decision.

## Purpose

Use MoA Council when disagreement is useful and the cost of an unexamined decision is high:

- shape a testable problem and expose assumptions;
- review an architecture or interface boundary;
- compare options with different trade-offs;
- check conformance to a governing plan, specification, or test contract.

Do not use it for deterministic formatting, routine implementation, or facts settled by one authoritative source or test.

## Package layout

```text
skills/moa-council/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── modes.md
│   └── protocol.md
├── scripts/moa_run.py
└── tests/
    ├── test_adapters.py
    ├── test_protocol.py
    └── test_runner.py
```

Runtime code uses only the Python standard library. Running the test suite requires `pytest`. Live participant turns require the selected provider CLI to be installed and authenticated; offline deterministic turns can be recorded by supplying validated public responses directly.

## Install with an agent

Give the following instruction to an agent that can inspect files and run shell commands:

```text
Install the `moa-council` skill from https://github.com/namu-k/MoA.

1. Read the repository and host guidance before changing files.
2. Detect the skill directory supported by this runtime. Prefer a project-local
   skill directory over a user-global directory.
3. Clone the repository into a temporary or stable source checkout and inspect
   every file under `skills/moa-council/` before installation.
4. Install only `skills/moa-council/`. Do not overwrite or delete an existing
   target. If one exists, compare it with the source and report the difference.
5. Run the packaged tests and CLI help check from the installed target.
6. Report the source commit, installation path, and validation results.

Do not install globally unless I explicitly request a global installation.
```

Common project-local targets include:

| Runtime | Common target |
| --- | --- |
| Agent Skills compatible | `.agents/skills/moa-council/` |
| Codex project | `.codex/skills/moa-council/` or the project-defined skill root |
| Claude Code project | `.claude/skills/moa-council/` |

The repository's `AGENTS.md`, runtime documentation, or installed-skill catalog remains authoritative when it specifies a different location.

### Agent validation commands

After resolving the actual target path, the installing agent should run:

```bash
python3 -m pytest -q <target>/moa-council/tests
python3 <target>/moa-council/scripts/moa_run.py --help
```

If the runtime provides an Agent Skills validator, run that validator against `<target>/moa-council/` as an additional check.

## Manual project-local installation

The following example uses the generic `.agents/skills/` location. Stop if the target already exists.

```bash
git clone https://github.com/namu-k/MoA.git
if [ -e .agents/skills/moa-council ]; then
  printf '%s\n' 'target exists; stop without overwriting'
  exit 1
fi
mkdir -p .agents/skills
cp -R MoA/skills/moa-council .agents/skills/moa-council
python3 -m pytest -q .agents/skills/moa-council/tests
python3 .agents/skills/moa-council/scripts/moa_run.py --help
```

Use a different target only when the active runtime documents that location.

## Usage

Invocation is explicit. Similar prose must not activate the skill.

```text
$moa-council shape "Turn a broad objective into a testable problem and evidence plan"
$moa-council design "Review the boundary between two components"
$moa-council decide "Compare reversible options and preserve material dissent"
$moa-council review "Check an artifact against its governing specification"
```

The four modes are:

| Mode | Focus |
| --- | --- |
| `shape` | Problem, assumptions, non-goals, and evidence gaps |
| `design` | UX, API, data, and implementation contract boundaries |
| `decide` | Competing options, trade-offs, reversibility, and evidence |
| `review` | Conformance and regression risk |

For the JSON operations accepted by the deterministic runner, read [`references/protocol.md`](skills/moa-council/references/protocol.md). For mode selection, read [`references/modes.md`](skills/moa-council/references/modes.md).

## Deliberation contract

1. Freeze the task, public evidence allowlist, participant roster, and authority boundary.
2. Collect one blind proposal from each of two to four distinct read-only sessions.
3. Register material claims and objections in the journal.
4. Run only targeted critiques and claimant revisions, with at most two critique rounds.
5. Resolve material claims and collect one final stance per eligible participant.
6. Record one synthesis turn and finalize it by its journaled SHA-256 digest.

The runner computes the truthful disposition from the journal. Open material claims, unresolved objections, abstentions, or missing evidence cannot be silently converted into consensus.

## Outputs

Each run produces exactly:

- `brief.md` — frozen public task and run metadata;
- `transcript.md` — append-only public journal;
- `decision.md` — write-once final disposition and limitations.

The default run root is `${XDG_STATE_HOME}/moa-council/runs/`, falling back to `~/.local/state/moa-council/runs/`. A host may provide an explicit `run_root` in the initialization payload.

## Safety and boundaries

- Record public rationale only. Never request or store hidden reasoning.
- Keep credentials, secrets, and unrestricted private context out of prompts and records.
- Treat `prompt_only` as a disclosure posture, not proof of filesystem isolation.
- Keep participants advisory and read-only. They do not implement changes or take external actions.
- Preserve dissent and deferred evidence instead of manufacturing consensus.
- A participant or nested execution context must not start another council.
- OpenCode remains ineligible until the runner supports a matching provider certificate.

MoA Council improves the structure and auditability of deliberation; it does not transfer decision authority away from the responsible human or host process.

## Development

Run the complete regression suite:

```bash
python3 -m pytest -q skills/moa-council/tests
```

The suite covers protocol validation, provider adapter parsing, journal integrity, phase order, quorum, recursion guards, synthesis identity, and final disposition behavior.
