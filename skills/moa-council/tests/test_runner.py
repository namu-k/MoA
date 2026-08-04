import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("moa_run", ROOT / "scripts" / "moa_run.py")
moa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moa)


def spec_payload(root, eligible=True):
    return {"run_root": root, "mode": "decide", "task": "choose", "participants": [
        {"participant_id": "codex-01", "provider": "codex", "model": "test", "role": "a", "quorum_eligible": eligible},
        {"participant_id": "codex-02", "provider": "codex", "model": "test", "role": "b", "quorum_eligible": eligible},
    ]}


class RunnerTests(unittest.TestCase):
    @staticmethod
    def response(action="PROPOSE", stance=None):
        return {"schema_version": 1, "action": action, "public_rationale": "x", "claim_updates": [], "evidence_refs": [], "unknowns": [], "stance": stance}

    def test_recursion_guard_precedes_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            old = os.environ.get("MOA_COUNCIL_PARTICIPANT")
            os.environ["MOA_COUNCIL_PARTICIPANT"] = "1"
            try:
                with self.assertRaises(RuntimeError):
                    moa.init_run(spec_payload(temp))
                self.assertEqual(list(Path(temp).iterdir()), [])
            finally:
                if old is None: os.environ.pop("MOA_COUNCIL_PARTICIPANT", None)
                else: os.environ["MOA_COUNCIL_PARTICIPANT"] = old

    def test_exactly_three_artifacts_prefix_and_write_once_decision(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp, eligible=False))
            self.assertEqual(sorted(p.name for p in Path(run).iterdir()), ["brief.md", "transcript.md"])
            self.assertIn("\n- {", (Path(run) / "transcript.md").read_text())
            before = (Path(run) / "transcript.md").read_bytes()
            moa.append_event(run, {"type": "ELIGIBILITY_CHANGED", "participant_id": "codex-01", "quorum_eligible": False, "public_rationale": "ok", "supporting_turn_ids": []})
            self.assertTrue((Path(run) / "transcript.md").read_bytes().startswith(before))
            result = moa.finalize_run(run, {"abort_reason": "ABORTED_NO_QUORUM"})
            self.assertEqual(result["disposition"], "ABORTED_NO_QUORUM")
            self.assertEqual(sorted(p.name for p in Path(run).iterdir()), ["brief.md", "decision.md", "transcript.md"])
            with self.assertRaises(FileExistsError): moa.finalize_run(run, {"abort_reason": "ABORTED_NO_QUORUM"})

    def test_no_quorum_and_integrity_abort(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp, eligible=False))
            self.assertEqual(moa.finalize_run(run, {"abort_reason": "ABORTED_NO_QUORUM"})["disposition"], "ABORTED_NO_QUORUM")
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            moa.record_operational_failure(run, "codex-01", "integrity")
            self.assertEqual(moa.finalize_run(run, {"abort_reason": "ABORTED_INTEGRITY"})["disposition"], "ABORTED_INTEGRITY")

    def test_synthesis_digest_binding_and_normal_finalization_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            candidate = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [],
                         "evidence_refs": [], "unknowns": [], "requested_disposition": "CONSENSUS", "recommendation": "go",
                         "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [],
                         "authority_boundary": "advisory", "reconsideration_triggers": [], "synthesizer": {"provider": "codex", "model": "test", "native_session_id": "synth-old"}}
            with self.assertRaises(ValueError): moa.record_synthesis(run, candidate)
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": participant, "response": self.response()})
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "poll", "participant_id": participant, "response": self.response("POLL", "ACCEPT")})
            turn = moa.record_synthesis(run, candidate)
            with self.assertRaises(ValueError): moa.finalize_run(run, {"synthesis_turn_id": turn["turn_id"], "digest": "bad"})
            self.assertEqual(moa.finalize_run(run, {"synthesis_turn_id": turn["turn_id"], "digest": turn["digest"]})["disposition"], "CONSENSUS")

    def test_phase_order_claim_ids_and_no_forged_runner_events(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            with self.assertRaises(ValueError): moa.append_event(run, {"type": "TURN_COMPLETED", "public_rationale": "forge", "supporting_turn_ids": []})
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "critique", "participant_id": "codex-01", "critique_round": 3, "response": self.response("CHALLENGE")})
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "reply_to": ["T001"], "response": self.response()})
            proposal = moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response()})
            claim = moa.append_event(run, {"type": "CLAIM_REGISTERED", "material": True, "public_rationale": "claim", "supporting_turn_ids": [proposal["turn_id"]]})
            self.assertEqual(claim["claim_id"], "C001")
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-01", "response": self.response("POLL", "ACCEPT")})

    def test_missing_poll_and_path_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            bad = spec_payload(temp); bad["run_id"] = "../escape"
            with self.assertRaises(ValueError): moa.init_run(bad)
            run = moa.init_run(spec_payload(temp))
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": participant, "response": self.response()})
            candidate = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [], "evidence_refs": [], "unknowns": [], "requested_disposition": "CONSENSUS", "recommendation": "go", "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [], "authority_boundary": "advisory", "reconsideration_triggers": [], "synthesizer": {"provider": "codex", "model": "test", "native_session_id": "synth-old"}}
            with self.assertRaises(ValueError): moa.record_synthesis(run, candidate)

    def test_final_phase_contract_and_opencode_cannot_be_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = spec_payload(temp)
            payload["participants"][1]["provider"] = "opencode"
            run = moa.init_run(payload)
            with self.assertRaises(ValueError): moa.append_event(run, {"type": "ELIGIBILITY_CHANGED", "participant_id": "codex-02", "quorum_eligible": True, "public_rationale": "unsafe", "supporting_turn_ids": []})
            first = moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response()})
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response()})
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-01", "response": self.response("PROPOSE", "ACCEPT")})
            self.assertEqual(first["turn_id"], "T001")

    def test_default_root_brief_and_synthesizer_identity(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"XDG_STATE_HOME": temp}, clear=False):
            payload = spec_payload(None); payload.pop("run_root")
            payload.update({"cwd_identity": "repo@abc", "cli_versions": {"codex": "test"}, "synthesizer_policy": "fresh"})
            run = moa.init_run(payload)
            self.assertEqual(Path(run).parent, Path(temp) / "moa-council" / "runs")
            brief = (Path(run) / "brief.md").read_text()
            self.assertIn("created_utc", brief); self.assertIn("repo@abc", brief); self.assertIn("prompt_only", brief)
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": participant, "response": self.response()})
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "poll", "participant_id": participant, "response": self.response("POLL", "ACCEPT"), "native_session_id": participant + "-thread"})
            candidate = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [], "evidence_refs": [], "unknowns": [], "requested_disposition": "CONSENSUS", "recommendation": "go", "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [], "authority_boundary": "advisory", "reconsideration_triggers": [], "synthesizer": {"provider": "codex", "model": "test", "native_session_id": "synth-1"}}
            turn = moa.record_synthesis(run, candidate)
            moa.finalize_run(run, {"synthesis_turn_id": turn["turn_id"], "digest": turn["digest"]})
            decision = (Path(run) / "decision.md").read_text()
            self.assertIn("synth-1", decision); self.assertIn("codex-01-thread", decision)

    def test_malformed_depth_fails_closed(self):
        with patch.dict(os.environ, {"MOA_COUNCIL_DEPTH": "nope"}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "invalid MOA_COUNCIL_DEPTH"):
                moa._guard_recursion()

    def test_one_same_session_retry_requires_recorded_operational_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            request = {"run": run, "phase": "proposal", "participant_id": "codex-01", "prompt": "x", "native_session_id": "thread-1"}
            with patch.object(moa, "launch_adapter", side_effect=moa.OperationalFailure("timeout")):
                with self.assertRaises(moa.OperationalFailure): moa.turn_run(request)
            self.assertEqual(moa._load_events(run)[-1]["type"], "TURN_FAILED")
            with patch.object(moa, "launch_adapter", return_value={"native_session_id": "thread-1", "response": self.response()}):
                self.assertEqual(moa.turn_run({**request, "retry": True})["turn_id"], "T001")
            with self.assertRaises(ValueError): moa.turn_run({**request, "retry": True})

    def test_synthesis_live_adapter_attaches_truthful_identity_and_dry_run_does_not_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": participant, "response": self.response()})
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "poll", "participant_id": participant, "response": self.response("POLL", "ACCEPT")})
            body = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [], "evidence_refs": [], "unknowns": [], "requested_disposition": "CONSENSUS", "recommendation": "go", "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [], "authority_boundary": "advisory", "reconsideration_triggers": []}
            request = {"run": run, "phase": "synthesis", "provider": "codex", "model": "synth", "prompt": "synthesize", "cwd": temp}
            with patch.object(moa, "launch_adapter", return_value={"native_session_id": "thread-synth", "response": body}) as launch:
                turn = moa.turn_run(request)
            self.assertEqual(launch.call_args.kwargs["synthesis"], True)
            event = moa._load_events(run)[-1]
            self.assertEqual(event["candidate"]["synthesizer"], {"provider": "codex", "model": "synth", "native_session_id": "thread-synth"})
            self.assertEqual(turn["turn_id"], "T005")
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            with patch.object(moa, "launch_adapter") as launch:
                output = moa.turn_run({"run": run, "phase": "synthesis", "provider": "codex", "model": "synth", "prompt": "s", "cwd": temp}, dry_run=True)
            self.assertTrue(output["dry_run"]); launch.assert_not_called(); self.assertFalse(any(e.get("phase") == "synthesis" for e in moa._load_events(run)))

    def test_phase_stances_and_nonmaterial_claim_promotion(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response("PROPOSE", "ACCEPT")})
            proposal = moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response()})
            claim = moa.append_event(run, {"type": "CLAIM_REGISTERED", "material": False, "public_rationale": "minor", "supporting_turn_ids": [proposal["turn_id"]]})
            self.assertEqual(claim["claim_id"], "C001")
            with self.assertRaises(ValueError): moa.turn_run({"run": run, "phase": "critique", "participant_id": "codex-01", "critique_round": 1, "reply_to": [proposal["turn_id"]], "targeted_claim_ids": ["C001"], "response": self.response("CHALLENGE", "ACCEPT")})
            promoted = moa.append_event(run, {"type": "MATERIALITY_SET", "claim_id": "C001", "material": True, "public_rationale": "now material", "supporting_turn_ids": [proposal["turn_id"]]})
            self.assertTrue(promoted["material"])

    def test_blind_stage_revision_coverage_and_nonmaterial_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            first = moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": self.response()})
            claim = moa.append_event(run, {"type": "CLAIM_REGISTERED", "material": False, "public_rationale": "nonmaterial", "supporting_turn_ids": [first["turn_id"]]})
            critique_payload = {"run": run, "phase": "critique", "participant_id": "codex-01", "critique_round": 1,
                                "reply_to": [first["turn_id"]], "targeted_claim_ids": [claim["claim_id"]], "response": self.response("CHALLENGE")}
            with self.assertRaisesRegex(ValueError, "every eligible participant"):
                moa.turn_run(critique_payload)
            moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-02", "response": self.response()})
            critique = moa.turn_run(critique_payload)
            with self.assertRaisesRegex(ValueError, "revision"):
                moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-01", "response": self.response("POLL", "ACCEPT")})
            moa.turn_run({"run": run, "phase": "revision", "participant_id": "codex-01", "reply_to": [critique["turn_id"]],
                          "targeted_claim_ids": [claim["claim_id"]], "response": self.response("MAINTAIN")})
            moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-01", "response": self.response("POLL", "ACCEPT")})
            with self.assertRaisesRegex(ValueError, "final polling"):
                moa.turn_run({"run": run, "phase": "critique", "participant_id": "codex-02", "critique_round": 2,
                              "reply_to": [critique["turn_id"]], "targeted_claim_ids": [claim["claim_id"]], "response": self.response("CHALLENGE")})
            with self.assertRaisesRegex(ValueError, "frozen"):
                moa.append_event(run, {"type": "BRIEF_AMENDMENT", "public_rationale": "too late", "supporting_turn_ids": []})
            moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-02", "response": self.response("POLL", "ACCEPT")})
            candidate = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [],
                         "evidence_refs": [], "unknowns": [], "requested_disposition": "CONSENSUS", "recommendation": "go",
                         "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [],
                         "authority_boundary": "advisory", "reconsideration_triggers": [],
                         "synthesizer": {"provider": "codex", "model": "test", "native_session_id": "synth-1"}}
            synthesis = moa.record_synthesis(run, candidate)
            result = moa.finalize_run(run, {"synthesis_turn_id": synthesis["turn_id"], "digest": synthesis["digest"]})
            self.assertEqual(result["disposition"], "CONSENSUS")
            self.assertEqual(sorted(path.name for path in Path(run).iterdir()), ["brief.md", "decision.md", "transcript.md"])

    def test_boolean_contract_fields_are_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = spec_payload(temp)
            payload["participants"][0]["quorum_eligible"] = "false"
            with self.assertRaisesRegex(ValueError, "quorum_eligible"):
                moa.init_run(payload)
            self.assertEqual(list(Path(temp).iterdir()), [])
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            with self.assertRaisesRegex(ValueError, "material"):
                moa.append_event(run, {"type": "CLAIM_REGISTERED", "material": "false", "public_rationale": "bad", "supporting_turn_ids": []})

    def test_evidence_refs_are_bound_to_the_initialized_allowlist(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = spec_payload(temp)
            payload["context_allowlist"] = ["E001"]
            run = moa.init_run(payload)
            bad = self.response()
            bad["evidence_refs"] = ["E999"]
            with self.assertRaisesRegex(ValueError, "context allowlist"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": bad})
            good = self.response()
            good["evidence_refs"] = ["E001"]
            self.assertEqual(moa.turn_run({"run": run, "phase": "proposal", "participant_id": "codex-01", "response": good})["turn_id"], "T001")
        with tempfile.TemporaryDirectory() as temp:
            payload = spec_payload(temp)
            payload["context_allowlist"] = "E001"
            with self.assertRaisesRegex(ValueError, "context_allowlist"):
                moa.init_run(payload)

    def test_qualified_consensus_requires_an_explicit_nonblocking_caveat(self):
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run(spec_payload(temp))
            for participant in ("codex-01", "codex-02"):
                moa.turn_run({"run": run, "phase": "proposal", "participant_id": participant, "response": self.response()})
            moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-01", "response": self.response("POLL", "ACCEPT")})
            moa.turn_run({"run": run, "phase": "poll", "participant_id": "codex-02", "response": self.response("POLL", "ACCEPT_WITH_CAVEAT")})
            candidate = {"schema_version": 1, "action": "SYNTHESIZE", "public_rationale": "x", "claim_updates": [],
                         "evidence_refs": [], "unknowns": [], "requested_disposition": "QUALIFIED_CONSENSUS", "recommendation": "go",
                         "claim_resolutions": [], "caveats": [], "dissent": [], "deferred_evidence": [],
                         "authority_boundary": "advisory", "reconsideration_triggers": [],
                         "synthesizer": {"provider": "codex", "model": "test", "native_session_id": "synth-1"}}
            synthesis = moa.record_synthesis(run, candidate)
            with self.assertRaisesRegex(ValueError, "classifiable journal"):
                moa.finalize_run(run, {"synthesis_turn_id": synthesis["turn_id"], "digest": synthesis["digest"]})


if __name__ == "__main__":
    unittest.main()
