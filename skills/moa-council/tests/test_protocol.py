import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "moa_run.py"
spec = importlib.util.spec_from_file_location("moa_run", RUNNER)
moa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moa)


def roster(count=2, provider="codex"):
    return [
        {"participant_id": f"{provider}-{i:02d}", "provider": provider,
         "model": "test", "role": f"lens-{i}", "quorum_eligible": True}
        for i in range(1, count + 1)
    ]


class ProtocolTests(unittest.TestCase):
    def test_modes_and_participant_bounds_identity(self):
        for mode in ("shape", "design", "decide", "review"):
            self.assertEqual(moa.validate_run_spec({"mode": mode, "task": "x", "participants": roster()} )["mode"], mode)
        with self.assertRaises(ValueError):
            moa.validate_run_spec({"mode": "premortem", "task": "x", "participants": roster()})
        with self.assertRaises(ValueError):
            moa.validate_run_spec({"mode": "shape", "task": "x", "participants": roster(1)})
        with self.assertRaises(ValueError):
            moa.validate_run_spec({"mode": "shape", "task": "x", "participants": roster(5)})
        duplicate = roster(); duplicate[1]["participant_id"] = duplicate[0]["participant_id"]
        with self.assertRaises(ValueError):
            moa.validate_run_spec({"mode": "shape", "task": "x", "participants": duplicate})

    def test_public_envelope_allowlist_and_digest(self):
        response = {"schema_version": 1, "action": "PROPOSE", "public_rationale": "public",
                    "claim_updates": [], "evidence_refs": [], "unknowns": [], "stance": "ACCEPT"}
        self.assertEqual(moa.validate_public_response(response)["action"], "PROPOSE")
        with self.assertRaises(ValueError):
            moa.validate_public_response({**response, "thinking": "secret"})
        self.assertEqual(moa.canonical_digest({"b": [1, 2], "a": "x"}),
                         moa.canonical_digest({"a": "x", "b": [1, 2]}))
        with self.assertRaises(ValueError):
            moa.canonical_digest({"number": 1.0})

    def test_claim_transition_and_references(self):
        self.assertTrue(moa.legal_claim_transition("OPEN", "ACCEPTED"))
        self.assertTrue(moa.legal_claim_transition("OPEN", "CONTESTED"))
        self.assertFalse(moa.legal_claim_transition("ACCEPTED", "OPEN"))
        with tempfile.TemporaryDirectory() as temp:
            run = moa.init_run({"run_root": temp, "mode": "review", "task": "x", "participants": roster()})
            with self.assertRaises(ValueError):
                moa.append_event(run, {"type": "CLAIM_REVISED", "claim_id": "C001", "supporting_turn_ids": ["T999"], "public_rationale": "x"})
            moa.append_event(run, {"type": "CLAIM_REGISTERED", "claim_id": "C001", "material": True, "supporting_turn_ids": [], "public_rationale": "x"})
            moa.append_event(run, {"type": "CLAIM_RESOLVED", "claim_id": "C001", "state": "ACCEPTED", "supporting_turn_ids": [], "public_rationale": "x"})

    def test_two_critique_round_limit_and_disposition_precedence(self):
        self.assertTrue(moa.critique_round_allowed(2))
        self.assertFalse(moa.critique_round_allowed(3))
        base = {"integrity": True, "eligible": 2, "claims": ["ACCEPTED"], "objections": [],
                "stances": ["ACCEPT", "ACCEPT"], "actionable": False, "missing_evidence": False}
        self.assertEqual(moa.compute_disposition(base), "CONSENSUS")
        self.assertEqual(moa.compute_disposition({**base, "stances": ["ACCEPT", "ACCEPT_WITH_CAVEAT"], "nonblocking_caveats": ["NONBLOCKING:C001"]}), "QUALIFIED_CONSENSUS")
        self.assertEqual(moa.compute_disposition({**base, "missing_evidence": True, "stances": ["OBJECT_MATERIAL", "ABSTAIN_MISSING_EVIDENCE"]}), "DEFERRED")
        self.assertEqual(moa.compute_disposition({**base, "integrity": False}), "ABORTED_INTEGRITY")
        self.assertEqual(moa.compute_disposition({**base, "eligible": 1}), "ABORTED_NO_QUORUM")


if __name__ == "__main__":
    unittest.main()
