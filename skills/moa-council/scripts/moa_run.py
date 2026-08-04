#!/usr/bin/env python3
"""Stdlib-only mechanics for the explicitly invoked moa-council skill."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - the staging target is Linux
    fcntl = None

MODES = {"shape", "design", "decide", "review"}
MODERATOR_EVENTS = {"BRIEF_AMENDMENT", "ELIGIBILITY_CHANGED", "CLAIM_REGISTERED", "MATERIALITY_SET", "CHALLENGE_RECORDED", "CLAIM_REVISED", "MATERIAL_OBJECTION_RECORDED", "MATERIAL_OBJECTION_CLOSED", "CLAIM_RESOLVED"}
PUBLIC_FIELDS = {"schema_version", "action", "public_rationale", "claim_updates", "evidence_refs", "unknowns", "stance"}
SYNTHESIS_FIELDS = {"schema_version", "action", "public_rationale", "claim_updates", "evidence_refs", "unknowns", "requested_disposition", "recommendation", "claim_resolutions", "caveats", "dissent", "deferred_evidence", "authority_boundary", "reconsideration_triggers", "synthesizer"}
SYNTHESIS_BODY_FIELDS = SYNTHESIS_FIELDS - {"synthesizer"}
CLAIM_TERMINAL = {"ACCEPTED", "REJECTED", "WITHDRAWN", "CONTESTED", "DEFERRED_MISSING_EVIDENCE"}
HIDDEN_KEY = re.compile(r"(?:think|reason|chain.?of.?thought|credential|secret|password|api.?key|token)", re.I)
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class IneligibleProvider(RuntimeError):
    pass


class OperationalFailure(RuntimeError):
    pass


def _no_float(value):
    if isinstance(value, float): raise ValueError("floating-point fields are not permitted")
    if isinstance(value, dict):
        for key, item in value.items():
            if HIDDEN_KEY.search(str(key)): raise ValueError("hidden reasoning or credential field is forbidden")
            _no_float(item)
    elif isinstance(value, list):
        for item in value: _no_float(item)


def canonical_digest(value):
    _no_float(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _guard_recursion():
    try: depth = int(os.environ.get("MOA_COUNCIL_DEPTH", "0"))
    except ValueError as exc: raise RuntimeError("invalid MOA_COUNCIL_DEPTH") from exc
    if depth < 0: raise RuntimeError("invalid MOA_COUNCIL_DEPTH")
    if os.environ.get("MOA_COUNCIL_PARTICIPANT") or depth > 0:
        raise RuntimeError("moa-council participant/nested execution is prohibited")


def _default_root():
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state"))) / "moa-council" / "runs"


def validate_run_spec(payload):
    if payload.get("mode") not in MODES: raise ValueError("unsupported mode")
    if not isinstance(payload.get("task"), str) or not payload["task"].strip(): raise ValueError("task is required")
    allowlist = payload.get("context_allowlist", [])
    if not isinstance(allowlist, list) or not all(isinstance(item, str) and item for item in allowlist): raise ValueError("context_allowlist must be a list of nonempty strings")
    people = payload.get("participants")
    if not isinstance(people, list) or not 2 <= len(people) <= 4: raise ValueError("requires two to four participants")
    ids = [p.get("participant_id") for p in people]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids): raise ValueError("participant identities must be distinct")
    for person in people:
        if person.get("provider") not in {"claude", "codex", "opencode"}: raise ValueError("unsupported provider")
        if not isinstance(person.get("role"), str) or not person["role"]: raise ValueError("participant role is required")
        if not isinstance(person.get("quorum_eligible"), bool): raise ValueError("quorum_eligible must be boolean")
    if payload.get("run_id") and not RUN_ID.fullmatch(payload["run_id"]): raise ValueError("unsafe run ID")
    return payload


def _validate_collections(value, fields):
    _no_float(value)
    for field in fields:
        if not isinstance(value[field], list): raise ValueError(f"{field} must be a list")


def validate_public_response(response):
    if not isinstance(response, dict) or set(response) != PUBLIC_FIELDS: raise ValueError("public response allowlist violation")
    _validate_collections(response, {"claim_updates", "evidence_refs", "unknowns"})
    if response["schema_version"] != 1 or not isinstance(response["public_rationale"], str) or not response["public_rationale"]: raise ValueError("invalid public response")
    if response["action"] not in {"PROPOSE", "CHALLENGE", "MAINTAIN", "REVISE", "WITHDRAW", "DEFER", "POLL"}: raise ValueError("invalid action")
    if response["stance"] not in {"ACCEPT", "ACCEPT_WITH_CAVEAT", "OBJECT_MATERIAL", "ABSTAIN_MISSING_EVIDENCE", None}: raise ValueError("invalid stance")
    if not all(isinstance(item, str) and item for item in response["evidence_refs"]): raise ValueError("evidence_refs must be nonempty strings")
    return response


def validate_synthesis_body(candidate):
    if not isinstance(candidate, dict) or set(candidate) != SYNTHESIS_BODY_FIELDS: raise ValueError("candidate allowlist violation")
    _validate_collections(candidate, {"claim_updates", "evidence_refs", "unknowns", "claim_resolutions", "caveats", "dissent", "deferred_evidence", "reconsideration_triggers"})
    if candidate["schema_version"] != 1 or candidate["action"] != "SYNTHESIZE": raise ValueError("invalid synthesis")
    if candidate["requested_disposition"] not in {"CONSENSUS", "QUALIFIED_CONSENSUS", "DISSENT", "DEFERRED", "DECIDED_BY_SYNTHESIZER"}: raise ValueError("invalid requested disposition")
    if not all(isinstance(item, str) for field in ("caveats", "dissent", "deferred_evidence") for item in candidate[field]): raise ValueError("candidate caveats, dissent, and deferred evidence must be string IDs")
    if not all(isinstance(item, str) and item for item in candidate["evidence_refs"]): raise ValueError("evidence_refs must be nonempty strings")
    return candidate


def validate_candidate(candidate):
    if not isinstance(candidate, dict) or set(candidate) != SYNTHESIS_FIELDS: raise ValueError("candidate allowlist violation")
    validate_synthesis_body({key: value for key, value in candidate.items() if key != "synthesizer"})
    identity = candidate["synthesizer"]
    if not isinstance(identity, dict) or set(identity) != {"provider", "model", "native_session_id"} or not all(isinstance(identity[key], str) and identity[key] for key in identity): raise ValueError("synthesizer identity is required")
    return candidate


def legal_claim_transition(before, after):
    return before == "OPEN" and after in CLAIM_TERMINAL


def critique_round_allowed(round_number):
    return isinstance(round_number, int) and 1 <= round_number <= 2


def compute_disposition(state):
    if not state.get("integrity", True): return "ABORTED_INTEGRITY"
    eligible_ids = state.get("eligible_ids") or [str(index) for index in range(state.get("eligible", 0))]
    polls = state.get("polls") or {str(index): stance for index, stance in enumerate(state.get("stances", []))}
    all_claims = state.get("claims", {})
    materiality = state.get("material_claims")
    claims_value = ({claim_id: value for claim_id, value in all_claims.items() if materiality.get(claim_id)}
                    if isinstance(all_claims, dict) and isinstance(materiality, dict) else all_claims)
    objections_value = state.get("objections", {})
    claims = claims_value.values() if isinstance(claims_value, dict) else claims_value
    objections = objections_value.values() if isinstance(objections_value, dict) else objections_value
    if len(eligible_ids) < 2: return "ABORTED_NO_QUORUM"
    if set(polls) != set(eligible_ids): return "ABORTED_INTEGRITY"
    stances = polls.values()
    clean_claims = all(value in {"ACCEPTED", "REJECTED", "WITHDRAWN"} for value in claims)
    clean_objections = all(value in {"SATISFIED", "WITHDRAWN"} for value in objections)
    if clean_claims and clean_objections and all(value == "ACCEPT" for value in stances): return "CONSENSUS"
    if clean_claims and clean_objections and all(value in {"ACCEPT", "ACCEPT_WITH_CAVEAT"} for value in stances) and "ACCEPT_WITH_CAVEAT" in stances and state.get("nonblocking_caveats"): return "QUALIFIED_CONSENSUS"
    claim_items = claims_value.items() if isinstance(claims_value, dict) else enumerate(claims_value)
    objection_items = objections_value.items() if isinstance(objections_value, dict) else enumerate(objections_value)
    blockers = [key for key, value in claim_items if value in {"OPEN", "CONTESTED", "DEFERRED_MISSING_EVIDENCE"}]
    blockers += [key for key, value in objection_items if value not in {"SATISFIED", "WITHDRAWN"}]
    blockers += [participant for participant, value in polls.items() if value in {"OBJECT_MATERIAL", "ABSTAIN_MISSING_EVIDENCE"}]
    if state.get("actionable") and blockers and set(state.get("candidate_blockers", [])) >= set(blockers): return "DECIDED_BY_SYNTHESIZER"
    if state.get("deferred_reasons") or state.get("missing_evidence") or any(value == "ABSTAIN_MISSING_EVIDENCE" for value in stances) or any(value == "DEFERRED_MISSING_EVIDENCE" for value in claims): return "DEFERRED"
    if blockers: return "DISSENT"
    return "ABORTED_INTEGRITY"


def _paths(run):
    path = Path(run)
    return path / "brief.md", path / "transcript.md", path / "decision.md"


@contextlib.contextmanager
def _lock(run):
    path = Path(run)
    lock_dir = path.parent / ".moa-council-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{path.name}.lock").open("a+") as handle:
        if fcntl: fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try: yield
        finally:
            if fcntl: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_events(run):
    _, transcript, _ = _paths(run)
    return [json.loads(line[2:]) for line in transcript.read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def _append(run, event):
    _, transcript, _ = _paths(run)
    before = transcript.read_bytes()
    with transcript.open("ab") as handle:
        handle.write(("- " + json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
    if not transcript.read_bytes().startswith(before): raise RuntimeError("transcript prefix integrity failed")


def init_run(payload):
    _guard_recursion()
    validate_run_spec(payload)
    root = Path(payload.get("run_root") or _default_root())
    run_id = payload.get("run_id") or "moa-" + uuid.uuid4().hex[:12]
    run = root / run_id
    if run.exists(): raise FileExistsError(run)
    run.mkdir(parents=True)
    brief, transcript, _ = _paths(run)
    safe = {key: payload.get(key) for key in ("mode", "task", "success_criteria", "non_goals", "context_allowlist", "exclusions", "cwd_identity", "cli_versions", "synthesizer_policy")}
    safe.update({"run_id": run_id, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "participants": payload["participants"], "native_session_state": "pending", "preliminary_eligibility": {p["participant_id"]: bool(p.get("quorum_eligible")) and p["provider"] != "opencode" for p in payload["participants"]}, "quorum_rule": "two eligible participant sessions", "max_critique_rounds": 2, "per_participant_call_cap": 4, "confidentiality": payload.get("confidentiality", "prompt_only"), "confidentiality_warning": "prompt_only does not claim excluded files are technically inaccessible"})
    brief.write_text("# MoA Council Brief\n\n```json\n" + json.dumps(safe, sort_keys=True, indent=2) + "\n```\n", encoding="utf-8")
    transcript.write_text("# MoA Council Transcript\n", encoding="utf-8")
    _append(run, {"type": "RUN_INITIALIZED", "participants": payload["participants"], "context_allowlist": payload.get("context_allowlist", []), "public_rationale": "initialized", "supporting_turn_ids": []})
    return str(run)


def _state(run):
    events = _load_events(run)
    initialized = next(event for event in events if event["type"] == "RUN_INITIALIZED")
    roster = initialized["participants"]
    eligible = {person["participant_id"]: bool(person.get("quorum_eligible")) and person["provider"] != "opencode" for person in roster}
    claims, material_claims, objections, polls, native_sessions = {}, {}, {}, {}, {}
    integrity = True
    for event in events:
        kind = event.get("type")
        if kind == "ELIGIBILITY_CHANGED" and next(person["provider"] for person in roster if person["participant_id"] == event["participant_id"]) != "opencode": eligible[event["participant_id"]] = bool(event["quorum_eligible"])
        elif kind == "CLAIM_REGISTERED":
            claims[event["claim_id"]] = "OPEN"; material_claims[event["claim_id"]] = bool(event.get("material"))
        elif kind == "MATERIALITY_SET": material_claims[event["claim_id"]] = bool(event["material"])
        elif kind == "CLAIM_RESOLVED": claims[event["claim_id"]] = event["state"]
        elif kind == "MATERIAL_OBJECTION_RECORDED": objections[event["objection_id"]] = "OPEN"
        elif kind == "MATERIAL_OBJECTION_CLOSED": objections[event["objection_id"]] = event["state"]
        elif kind == "TURN_COMPLETED" and event.get("phase") == "poll": polls[event["participant_id"]] = event["response"]["stance"]
        if kind == "TURN_COMPLETED" and event.get("participant_id"): native_sessions[event["participant_id"]] = event.get("native_session_id", "unknown")
        elif kind == "TURN_FAILED" and event.get("integrity_failure"): integrity = False
    return {"events": events, "eligible_ids": [pid for pid, allowed in eligible.items() if allowed], "eligibility": eligible, "claims": claims, "material_claims": material_claims, "objections": objections, "polls": polls, "native_sessions": native_sessions, "integrity": integrity, "context_allowlist": initialized.get("context_allowlist", [])}


def _validate_evidence_refs(state, response):
    if not set(response["evidence_refs"]).issubset(set(state["context_allowlist"])): raise ValueError("evidence reference is outside the context allowlist")


def append_event(run, event):
    if event.get("type") not in MODERATOR_EVENTS: raise ValueError("event is runner-owned or illegal")
    if not isinstance(event.get("public_rationale"), str): raise ValueError("public rationale required")
    with _lock(run):
        events, state = _load_events(run), _state(run)
        if state["polls"]: raise ValueError("moderator state is frozen after final polling starts")
        turns = {item.get("turn_id") for item in events if item.get("turn_id")}
        if not set(event.get("supporting_turn_ids", [])).issubset(turns): raise ValueError("unknown supporting turn")
        if event["type"] == "CLAIM_REGISTERED":
            if not isinstance(event.get("material"), bool): raise ValueError("material must be boolean")
            event = dict(event); event["claim_id"] = f"C{len([e for e in events if e.get('type') == 'CLAIM_REGISTERED']) + 1:03d}"
            event.setdefault("state", "OPEN")
        elif event["type"] in {"MATERIALITY_SET", "CHALLENGE_RECORDED", "CLAIM_REVISED", "MATERIAL_OBJECTION_RECORDED", "MATERIAL_OBJECTION_CLOSED", "CLAIM_RESOLVED"} and event.get("claim_id") not in state["claims"]: raise ValueError("unknown claim")
        if event["type"] == "MATERIALITY_SET" and not isinstance(event.get("material"), bool): raise ValueError("material must be boolean")
        if event["type"] == "CLAIM_RESOLVED" and not legal_claim_transition(state["claims"][event["claim_id"]], event.get("state")): raise ValueError("illegal claim transition")
        if event["type"] == "MATERIAL_OBJECTION_RECORDED":
            if not state["material_claims"].get(event["claim_id"]): raise ValueError("material objection requires a material claim")
            if not event.get("participant_id") or not event.get("turn_id") in turns: raise ValueError("objection needs participant and turn")
            event = dict(event); event["objection_id"] = f"O{len(state['objections']) + 1:03d}"
        if event["type"] == "MATERIAL_OBJECTION_CLOSED":
            if event.get("objection_id") not in state["objections"] or event.get("state") not in {"SATISFIED", "WITHDRAWN", "DEFERRED"}: raise ValueError("invalid objection close")
        if event["type"] == "ELIGIBILITY_CHANGED":
            if event.get("participant_id") not in state["eligibility"]: raise ValueError("unknown participant")
            if not isinstance(event.get("quorum_eligible"), bool): raise ValueError("quorum_eligible must be boolean")
            roster = next(item["participants"] for item in events if item["type"] == "RUN_INITIALIZED")
            if next(person["provider"] for person in roster if person["participant_id"] == event["participant_id"]) == "opencode" and event.get("quorum_eligible"): raise ValueError("OpenCode cannot become eligible without a certificate feature")
        _append(run, event)
        return event


def record_operational_failure(run, participant_id, rationale="provider operational failure", integrity_failure=True):
    """Runner-owned failure record; never accepts raw stderr or provider chatter."""
    with _lock(run):
        _append(run, {"type": "TURN_FAILED", "participant_id": participant_id, "public_rationale": rationale, "supporting_turn_ids": [], "integrity_failure": bool(integrity_failure)})


def _turn_id(events):
    return f"T{sum(1 for event in events if event.get('turn_id')) + 1:03d}"


def _require_phase(state, payload):
    phase, events = payload["phase"], state["events"]
    reply, targets = payload.get("reply_to", []), payload.get("targeted_claim_ids", [])
    if state["polls"] and phase in {"critique", "revision"}: raise ValueError("final polling has started")
    eligible = set(state["eligible_ids"])
    proposals = {event.get("participant_id") for event in events if event.get("type") == "TURN_COMPLETED" and event.get("phase") == "proposal"}
    critique_targets = {claim_id for event in events if event.get("type") == "TURN_COMPLETED" and event.get("phase") == "critique" for claim_id in event.get("targeted_claim_ids", [])}
    revision_targets = {claim_id for event in events if event.get("type") == "TURN_COMPLETED" and event.get("phase") == "revision" for claim_id in event.get("targeted_claim_ids", [])}
    if phase == "proposal":
        if reply or targets or any(e.get("type") == "TURN_COMPLETED" and e.get("phase") == "proposal" and e.get("participant_id") == payload.get("participant_id") for e in events): raise ValueError("one blind proposal per participant cannot reference prior turns or claims")
    elif phase == "critique":
        if proposals != eligible: raise ValueError("critique requires blind proposal from every eligible participant")
        if not critique_round_allowed(payload.get("critique_round")) or not any(e.get("phase") == "proposal" for e in events) or not reply or not targets: raise ValueError("critique requires prior proposal, targeted turns/claims, and round 1-2")
    elif phase == "revision":
        if not any(e.get("phase") == "critique" and set(targets).intersection(e.get("targeted_claim_ids", [])) for e in events): raise ValueError("revision requires targeted critique")
    elif phase == "poll":
        if proposals != eligible: raise ValueError("poll requires proposal from every eligible participant")
        if critique_targets - revision_targets: raise ValueError("poll requires revision coverage for every targeted critique")
        if payload.get("participant_id") in state["polls"] or any(state["material_claims"].get(claim_id) and value == "OPEN" for claim_id, value in state["claims"].items()): raise ValueError("one final poll requires resolved material claims")
    elif phase == "synthesis":
        if proposals != eligible or set(state["polls"]) != eligible: raise ValueError("synthesis requires proposal and poll from every eligible participant")
        if critique_targets - revision_targets: raise ValueError("synthesis requires revision coverage for every targeted critique")
    else: raise ValueError("illegal turn phase")


def record_synthesis(run, candidate, identity=None):
    if "synthesizer" not in candidate:
        if identity is None: raise ValueError("synthesizer identity is required")
        candidate = {**candidate, "synthesizer": identity}
    validate_candidate(candidate)
    with _lock(run):
        state = _state(run)
        _require_phase(state, {"phase": "synthesis"})
        _validate_evidence_refs(state, candidate)
        if any(event.get("phase") == "synthesis" for event in state["events"]): raise ValueError("only one synthesis call")
        turn_id, digest = _turn_id(state["events"]), canonical_digest(candidate)
        _append(run, {"type": "TURN_COMPLETED", "phase": "synthesis", "turn_id": turn_id, "candidate": candidate, "synthesizer": candidate["synthesizer"], "digest": digest, "public_rationale": candidate["public_rationale"], "supporting_turn_ids": []})
        return {"turn_id": turn_id, "digest": digest}


def parse_claude_output(stdout, validator=validate_public_response):
    try: outer = json.loads(stdout)
    except json.JSONDecodeError as exc: raise ValueError("malformed Claude JSON") from exc
    if outer.get("is_error") or not isinstance(outer.get("session_id"), str) or not outer["session_id"]: raise ValueError("Claude operational failure or missing session ID")
    try: response = json.loads(outer["result"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc: raise ValueError("Claude result is not a public envelope") from exc
    return {"native_session_id": outer["session_id"], "response": validator(response)}


def parse_codex_output(stdout, validator=validate_public_response):
    thread_id, final_text = None, None
    for raw in stdout.splitlines():
        try: event = json.loads(raw)
        except json.JSONDecodeError as exc: raise ValueError("malformed Codex JSONL") from exc
        if event.get("type") in {"turn.failed", "error"}: raise ValueError("Codex operational failure")
        if event.get("type") == "thread.started": thread_id = event.get("thread_id") or event.get("thread", {}).get("id")
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") in {"agent_message", "agent_message_item"}: final_text = item.get("text") or item.get("content")
    if not isinstance(thread_id, str) or not thread_id or not isinstance(final_text, str): raise ValueError("Codex missing thread ID or final agent message")
    try: response = json.loads(final_text)
    except json.JSONDecodeError as exc: raise ValueError("Codex final message is not a public envelope") from exc
    return {"native_session_id": thread_id, "response": validator(response)}


def build_argv(provider, prompt, model=None, native_session_id=None, mcp_config_path=None):
    if provider == "opencode": raise IneligibleProvider("OpenCode is disabled pending a provider certificate")
    if provider == "claude":
        argv = ["claude", "--print", "--permission-mode", "plan", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--mcp-config", mcp_config_path or "<empty-mcp-config.json>", "--disable-slash-commands", "--disallowedTools", "Edit,Write,Bash"]
        if model: argv += ["--model", model]
        if native_session_id: argv += ["--resume", native_session_id]
        return argv + [prompt]
    if provider == "codex":
        argv = ["codex", "exec", "--sandbox", "read-only", "--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
        if model: argv += ["--model", model]
        return argv + (["resume", native_session_id, prompt] if native_session_id else [prompt])
    raise ValueError("unsupported provider")


def launch_adapter(provider, prompt, cwd, model=None, native_session_id=None, dry_run=False, run_id=None, synthesis=False):
    _guard_recursion()
    argv = build_argv(provider, prompt, model, native_session_id)
    if dry_run: return {"dry_run": True, "argv": argv}
    env = os.environ.copy(); env["MOA_COUNCIL_PARTICIPANT"] = "1"; env["MOA_COUNCIL_RUN_ID"] = run_id or "unknown"; env["MOA_COUNCIL_DEPTH"] = str(int(env.get("MOA_COUNCIL_DEPTH", "0")) + 1)
    with tempfile.TemporaryDirectory(prefix="moa-council-mcp-") as temp:
        if provider == "claude":
            config = Path(temp) / "empty-mcp.json"
            config.write_text('{"mcpServers":{}}', encoding="utf-8")
            argv = build_argv(provider, prompt, model, native_session_id, str(config))
        try: result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=120)
        except subprocess.TimeoutExpired as exc: raise OperationalFailure("provider timeout") from exc
        if result.returncode: raise OperationalFailure("provider nonzero exit")
        validator = validate_synthesis_body if synthesis else validate_public_response
        return parse_claude_output(result.stdout, validator) if provider == "claude" else parse_codex_output(result.stdout, validator)


def _synthesis_turn(payload, dry_run):
    run = payload["run"]
    if "response" in payload:
        identity = payload.get("synthesizer")
        return record_synthesis(run, payload["response"], identity)
    provider = payload.get("provider")
    if provider not in {"claude", "codex"}: raise IneligibleProvider("synthesizer provider is not eligible")
    if dry_run:
        return {"dry_run": True, "argv": build_argv(provider, payload["prompt"], payload.get("model"), payload.get("native_session_id"))}
    with _lock(run):
        _require_phase(_state(run), {"phase": "synthesis"})
    try:
        parsed = launch_adapter(provider, payload["prompt"], payload.get("cwd", "."), payload.get("model"), payload.get("native_session_id"), False, Path(run).name, synthesis=True)
    except (OperationalFailure, ValueError) as exc:
        raise OperationalFailure("synthesizer operational failure") from exc
    identity = {"provider": provider, "model": payload.get("model") or "unknown", "native_session_id": parsed["native_session_id"]}
    return record_synthesis(run, parsed["response"], identity)


def turn_run(payload, dry_run=False):
    _guard_recursion()
    run, phase = payload["run"], payload.get("phase")
    if phase == "synthesis": return _synthesis_turn(payload, dry_run)
    expected_actions = {"proposal": {"PROPOSE"}, "critique": {"CHALLENGE"}, "revision": {"MAINTAIN", "REVISE", "WITHDRAW", "DEFER"}, "poll": {"POLL"}}
    def snapshot():
        state = _state(run)
        roster = next(event["participants"] for event in state["events"] if event["type"] == "RUN_INITIALIZED")
        participant = next((person for person in roster if person["participant_id"] == payload.get("participant_id")), None)
        if not participant or not state["eligibility"].get(participant["participant_id"]): raise IneligibleProvider("participant is not eligible")
        if participant["provider"] == "opencode": raise IneligibleProvider("OpenCode is disabled")
        if sum(1 for event in state["events"] if event.get("participant_id") == participant["participant_id"] and event.get("type") in {"TURN_COMPLETED", "TURN_FAILED"}) >= 4: raise ValueError("per-participant call cap reached")
        turns = {event.get("turn_id") for event in state["events"] if event.get("turn_id")}
        if not set(payload.get("reply_to", [])).issubset(turns) or not set(payload.get("targeted_claim_ids", [])).issubset(state["claims"]): raise ValueError("turn references are unknown")
        _require_phase(state, payload)
        return state, participant
    with _lock(run):
        state, participant = snapshot()
        response = payload.get("response")
        if response is not None:
            parsed = {"native_session_id": payload.get("native_session_id", "unknown"), "response": validate_public_response(response)}
            if parsed["response"]["action"] not in expected_actions[phase]: raise ValueError("response action does not match phase")
            if (phase == "poll") != (parsed["response"]["stance"] is not None): raise ValueError("only polls require a non-null stance")
        else:
            failures = [e for e in state["events"] if e.get("type") == "TURN_FAILED" and e.get("participant_id") == participant["participant_id"] and e.get("native_session_id") == payload.get("native_session_id")]
            completed_same_session = any(e.get("type") == "TURN_COMPLETED" and e.get("participant_id") == participant["participant_id"] and e.get("native_session_id") == payload.get("native_session_id") for e in state["events"])
            if payload.get("retry") and (len(failures) != 1 or completed_same_session): raise ValueError("retry requires exactly one recorded same-session failure")
    # Provider work is intentionally outside the cooperative run lock.
    if response is None:
        try:
            parsed = launch_adapter(participant["provider"], payload["prompt"], payload.get("cwd", "."), participant.get("model"), payload.get("native_session_id"), dry_run, Path(run).name)
        except (OperationalFailure, ValueError) as exc:
            with _lock(run):
                _append(run, {"type": "TURN_FAILED", "phase": phase, "participant_id": participant["participant_id"], "native_session_id": payload.get("native_session_id", "unknown"), "public_rationale": "provider operational failure", "supporting_turn_ids": payload.get("reply_to", [])})
            raise OperationalFailure("provider operational failure") from exc
        if dry_run: return parsed
        if parsed["response"]["action"] not in expected_actions[phase]: raise ValueError("response action does not match phase")
        if (phase == "poll") != (parsed["response"]["stance"] is not None): raise ValueError("only polls require a non-null stance")
    with _lock(run):
        state, participant = snapshot()  # run may have changed while the provider ran
        _validate_evidence_refs(state, parsed["response"])
        turn_id = _turn_id(state["events"])
        event = {"type": "TURN_COMPLETED", "phase": phase, "turn_id": turn_id, "participant_id": participant["participant_id"], "provider": participant["provider"], "role": participant["role"], "native_session_id": parsed["native_session_id"], "reply_to": payload.get("reply_to", []), "targeted_claim_ids": payload.get("targeted_claim_ids", []), "response": parsed["response"], "public_rationale": parsed["response"]["public_rationale"], "supporting_turn_ids": payload.get("reply_to", [])}
        _append(run, event)
        return {"turn_id": turn_id, "native_session_id": parsed["native_session_id"]}


def _render_decision(disposition, state, candidate, abort):
    marker = "not applicable—aborted before synthesis"
    if abort:
        return "# MoA Council Decision\n\n" + "\n".join([f"- Disposition: **{disposition}**", f"- Recommendation: {marker}", f"- Drivers/selected alternative: {marker}", f"- Synthesizer identity: {marker}", f"- Caveats/dissent/deferred evidence: {marker}", f"- Unresolved claims: {json.dumps(state['claims'], sort_keys=True)}", f"- Abort evidence: quorum={len(state['eligible_ids'])}; integrity={state['integrity']}", f"- Final stances: {json.dumps(state['polls'], sort_keys=True)}", f"- Eligibility: {json.dumps(state['eligibility'], sort_keys=True)}", ""])
    return "# MoA Council Decision\n\n" + "\n".join([f"- Disposition: **{disposition}**", f"- Recommendation: {candidate['recommendation']}", f"- Drivers/selected alternative: {json.dumps(candidate.get('claim_resolutions', []), ensure_ascii=False)}", f"- Claim-resolution trail: {json.dumps(candidate['claim_resolutions'], ensure_ascii=False)}", f"- Caveats: {json.dumps(candidate['caveats'], ensure_ascii=False)}", f"- Dissent: {json.dumps(candidate['dissent'], ensure_ascii=False)}", f"- Deferred evidence/reason codes: {json.dumps(candidate['deferred_evidence'], ensure_ascii=False)}", f"- Final stances: {json.dumps(state['polls'], sort_keys=True)}", f"- Quorum/integrity: {len(state['eligible_ids'])}/{state['integrity']}", f"- Participant native-session/eligibility summary: {json.dumps({'sessions': state['native_sessions'], 'eligibility': state['eligibility']}, sort_keys=True)}", f"- Synthesizer identity: {json.dumps(candidate['synthesizer'], sort_keys=True)}", f"- Limitations/authority: {candidate['authority_boundary']}", f"- Next action/reconsideration triggers: {json.dumps(candidate['reconsideration_triggers'], ensure_ascii=False)}", ""])


def finalize_run(run, payload):
    with _lock(run):
        _, _, decision = _paths(run)
        if decision.exists(): raise FileExistsError("decision.md is write-once")
        state, abort = _state(run), payload.get("abort_reason")
        candidate = None
        if abort:
            allowed = (abort == "ABORTED_NO_QUORUM" and len(state["eligible_ids"]) < 2) or (abort == "ABORTED_INTEGRITY" and not state["integrity"])
            if not allowed: raise ValueError("abort predicate is not journal-proven")
            disposition = abort
        else:
            matching = [event for event in state["events"] if event.get("phase") == "synthesis" and event.get("turn_id") == payload.get("synthesis_turn_id")]
            if len(matching) != 1 or matching[0]["digest"] != payload.get("digest"): raise ValueError("synthesis digest mismatch")
            candidate = matching[0]["candidate"]
            if any(state["material_claims"].get(claim_id) and value == "OPEN" for claim_id, value in state["claims"].items()): raise ValueError("normal finalization rejects open material claims")
            state["actionable"] = bool(candidate["recommendation"])
            state["candidate_blockers"] = candidate["dissent"]
            state["nonblocking_caveats"] = bool(candidate["caveats"]) and all(item.startswith("NONBLOCKING:") for item in candidate["caveats"])
            reasons = {item for item in candidate["deferred_evidence"] if item in {"MISSING_EVIDENCE", "MISSING_AUTHORITY", "REQUIRED_EXPERIMENT"}}
            if candidate["deferred_evidence"] and len(reasons) != len(candidate["deferred_evidence"]): raise ValueError("invalid deferred reason code")
            state["deferred_reasons"] = reasons
            disposition = compute_disposition(state)
            if disposition.startswith("ABORTED"): raise ValueError("normal finalization requires complete polling and a classifiable journal")
        _append(run, {"type": "RUN_FINALIZED", "disposition": disposition, "public_rationale": "finalized", "supporting_turn_ids": []})
        decision.write_text(_render_decision(disposition, state, candidate, bool(abort)), encoding="utf-8")
        return {"run_id": Path(run).name, "disposition": disposition}


def main():
    parser = argparse.ArgumentParser(description="Recorded deliberative MoA mechanics (explicit host invocation only).")
    parser.add_argument("operation", nargs="?", choices=("init", "turn", "event", "finalize")); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.operation: parser.print_help(); return 0
    payload = json.load(sys.stdin)
    if args.operation == "init": output = {"run": init_run(payload)}
    elif args.operation == "event": output = append_event(payload["run"], payload["event"])
    elif args.operation == "finalize": output = finalize_run(payload["run"], payload)
    else: output = turn_run(payload, dry_run=args.dry_run)
    print(json.dumps(output, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
