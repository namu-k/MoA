# Protocol

The runner accepts only `init`, `turn`, `event`, and `finalize` JSON operations. It owns IDs, journal append/prefix checks, reference and envelope validation, subprocess mechanics, and final disposition—not semantic ranking.

Participants submit public rationale, claims, evidence references, unknowns, and phase stance. Synthesis is stored once with a SHA-256 digest of UTF-8 sorted-key compact JSON; normal finalization must name that exact turn and digest. Abort finalization is permitted only for journal-proven no quorum or integrity failure.

Every `evidence_refs` entry is a nonempty string ID and must occur in the `context_allowlist` frozen by `init`; an empty allowlist permits only empty evidence references. The host remains responsible for making each allowed ID resolve to the exact public evidence named in the brief.

Disposition precedence is: integrity abort, no-quorum abort, consensus, qualified consensus, synthesizer decision, deferred, dissent, fail-closed integrity abort. A material objection, contested/deferred claim, material abstention, or open claim cannot be consensus.

Provider commands are argv lists, never a shell. The runner checks participant/depth guards before creating a run or launching a process, propagates those guards into child environments, and records only the public envelope. Locks and certificates are outside run directories.

## JSON operation examples

`init` receives a mode, task, two-to-four participant records, and optional host facts such as `cwd_identity`, `cli_versions`, and `run_root`:

```json
{"mode":"design","task":"Choose an API boundary","participants":[{"participant_id":"codex-01","provider":"codex","model":"unknown","role":"contract","quorum_eligible":true},{"participant_id":"claude-01","provider":"claude","model":"unknown","role":"edge cases","quorum_eligible":true}]}
```

`turn` names the run, participant, phase, and public response (for offline deterministic use):

```json
{"run":"/state/moa-council/runs/moa-x","phase":"proposal","participant_id":"codex-01","response":{"schema_version":1,"action":"PROPOSE","public_rationale":"Prefer versioning","claim_updates":[],"evidence_refs":[],"unknowns":[],"stance":null}}
```

`event` carries only moderator-owned semantic transitions; the runner allocates claim and objection IDs:

```json
{"run":"/state/moa-council/runs/moa-x","event":{"type":"CLAIM_REGISTERED","material":true,"public_rationale":"Contract versioning matters","supporting_turn_ids":["T001"]}}
```

`finalize` names the journaled synthesis turn and its digest, or a journal-proven abort reason:

```json
{"run":"/state/moa-council/runs/moa-x","synthesis_turn_id":"T005","digest":"sha256-hex"}
```

A live synthesis `turn` names the safe adapter lane. The provider returns the candidate body only; the runner attaches its observed provider/model/native-session identity before journaling:

```json
{"run":"/state/moa-council/runs/moa-x","phase":"synthesis","provider":"codex","model":"approved-model","cwd":"/isolated/evidence","prompt":"Return only the candidate-decision JSON body."}
```
