import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("moa_run", ROOT / "scripts" / "moa_run.py")
moa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moa)


def json_line(value):
    return json.dumps(value, separators=(",", ":"))


class AdapterTests(unittest.TestCase):
    def test_claude_and_codex_safe_argv_and_resume(self):
        claude = moa.build_argv("claude", "prompt", model="x", native_session_id="s1")
        self.assertIn("--permission-mode", claude); self.assertIn("plan", claude)
        self.assertIn("--tools", claude); self.assertIn("", claude)
        self.assertIn("--resume", claude); self.assertIn("--strict-mcp-config", claude)
        self.assertIn("--disable-slash-commands", claude); self.assertNotIn("shell", " ".join(claude))
        self.assertNotIn("{}", claude)
        codex = moa.build_argv("codex", "prompt", model="x", native_session_id="t1")
        self.assertEqual(codex[:2], ["codex", "exec"])
        self.assertIn("read-only", codex); self.assertIn("--skip-git-repo-check", codex)
        self.assertIn("--ignore-user-config", codex); self.assertIn("--ignore-rules", codex)
        self.assertNotIn("--ignore-git-repo-check", codex); self.assertNotIn("--ignore-user-rules", codex)
        self.assertNotIn("--resume", codex)
        self.assertEqual(codex[-3:], ["resume", "t1", "prompt"])

    def test_provider_output_parsers_reject_failure_and_preserve_native_ids(self):
        public = '{"schema_version":1,"action":"PROPOSE","public_rationale":"x","claim_updates":[],"evidence_refs":[],"unknowns":[],"stance":null}'
        self.assertEqual(moa.parse_claude_output(json.dumps({"session_id": "claude-1", "result": public}))["native_session_id"], "claude-1")
        with self.assertRaises(ValueError): moa.parse_claude_output('{"session_id":"x","is_error":true,"result":"{}"}')
        codex = '\n'.join([json_line({"type":"thread.started","thread_id":"thread-1"}), json_line({"type":"item.completed","item":{"type":"agent_message","text":public}})])
        self.assertEqual(moa.parse_codex_output(codex)["native_session_id"], "thread-1")
        with self.assertRaises(ValueError): moa.parse_codex_output(json_line({"type":"turn.failed"}))

    def test_opencode_is_non_launching_and_fake_cli_dry_run_writes_nothing(self):
        with self.assertRaises(moa.IneligibleProvider): moa.build_argv("opencode", "prompt")
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "marker"; marker.write_text("unchanged")
            result = moa.launch_adapter("codex", "prompt", cwd=temp, dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertEqual(marker.read_text(), "unchanged")

    def test_explicit_only_metadata_and_no_dependency(self):
        metadata = (ROOT / "agents" / "openai.yaml").read_text()
        self.assertIn("allow_implicit_invocation: false", metadata)
        source = (ROOT / "scripts" / "moa_run.py").read_text()
        self.assertNotIn("import requests", source)
        self.assertNotIn("from pydantic", source)

    def test_fake_executable_captures_recursion_environment_without_repo_writes(self):
        public = '{"schema_version":1,"action":"PROPOSE","public_rationale":"x","claim_updates":[],"evidence_refs":[],"unknowns":[],"stance":null}'
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"PATH": temp + os.pathsep + os.environ["PATH"]}, clear=False):
            fake = Path(temp) / "codex"
            fake.write_text("#!/bin/sh\n[ \"$MOA_COUNCIL_PARTICIPANT\" = 1 ] && [ \"$MOA_COUNCIL_RUN_ID\" = run-1 ] || exit 9\nprintf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"fake-thread\"}'\nprintf '%s\\n' '{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"" + public.replace('"', '\\\"') + "\"}}'\n")
            fake.chmod(0o755)
            marker = Path(temp) / "marker"; marker.write_text("unchanged")
            result = moa.launch_adapter("codex", "prompt", cwd=temp, run_id="run-1")
            self.assertEqual(result["native_session_id"], "fake-thread")
            self.assertEqual(marker.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
