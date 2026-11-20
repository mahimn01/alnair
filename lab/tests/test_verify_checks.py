"""Acceptance checks in lab verify (lab/bin/lab.py): which checks run, and what a refused check reports.

The 2026-11-18 Code Mode A/B recorded `env PYTHONDONTWRITEBYTECODE=1 python3 code/check_limits.py` as a check.
verify() raised "Unsupported check program: env" before writing anything, so the lab verify tool returned
{"evidence": null} with no reason, and an older passing evidence file would have stayed in place.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lab_verify_checks", ROOT / "bin/lab.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)

ENV_CHECK = ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-c",
             "import os, sys; sys.exit(0 if os.environ.get('PYTHONDONTWRITEBYTECODE') == '1' else 3)"]


class VerifyChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(lab, "AUTH", Path(self.temp.name) / "auth.json"),
                        patch.object(lab, "ACCOUNT_REGISTRY", Path(self.temp.name) / "accounts.json"),
                        patch.object(lab, "core_and_trading", return_value=("core", "policy"))]
        for p in self.patches:
            p.start()
        self.project = Path(self.temp.name) / "project"
        shutil.copytree(ROOT / "demo-project", self.project)
        (self.project / "totals.py").write_text("def total_amount(amounts):\n    return sum(amounts)\n")
        self.run_dir = Path(self.temp.name) / "run"
        lab.prepare(self.project, "balanced", "auto", self.run_dir)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def checks(self, *argvs):
        lab.dump(self.run_dir / "acceptance.json", {"criteria": [], "checks": [{"argv": a, "timeoutSeconds": 30} for a in argvs]})

    def evidence(self):
        return json.loads((self.run_dir / "verified-checks.json").read_text())

    def test_env_with_assignments_runs_an_allowed_program(self):
        self.checks(ENV_CHECK)
        report = lab.verify(self.run_dir)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["checks"][0]["exitCode"], 0)
        self.assertEqual(report["rejected"], [])

    def test_a_check_that_times_out_or_cannot_start_fails_with_its_reason(self):
        # verify raised UnboundLocalError on both: `except ... as error` deleted the refusal message it reports.
        lab.dump(self.run_dir / "acceptance.json", {"criteria": [], "checks": [
            {"argv": ["python3", "-c", "import time; time.sleep(5)"], "timeoutSeconds": 1}]})
        report = lab.verify(self.run_dir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"][0]["exitCode"], 124)
        self.assertIn("Check timed out.", report["checks"][0]["tail"])
        self.checks(["cargo", "test"])
        real_run = lab.subprocess.run

        def run(argv, *args, **kwargs):  # cargo is missing; git, for the fingerprint, still runs
            if argv[:1] == ["cargo"]:
                raise FileNotFoundError(2, "No such file or directory", "cargo")
            return real_run(argv, *args, **kwargs)
        with patch.object(lab.subprocess, "run", run):
            report = lab.verify(self.run_dir)
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"][0]["exitCode"], 127)

    def test_on_windows_env_assignments_become_the_environment_and_python3_a_real_python(self):
        found = {"python3": r"C:\Users\u\AppData\Local\Microsoft\WindowsApps\python3.EXE",
                 "python": r"C:\Python312\python.EXE", "npm": r"C:\nodejs\npm.CMD"}
        with patch.object(lab.shutil, "which", lambda name: found.get(name)):
            command, env = lab.windows_check(ENV_CHECK)
            self.assertEqual(command[:2], [r"C:\Python312\python.EXE", "-c"])  # not the Microsoft Store stub
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertEqual(lab.windows_check(["npm", "test"]), ([r"C:\nodejs\npm.CMD", "test"], None))
        with patch.object(lab.shutil, "which", lambda name: None):
            self.assertEqual(lab.windows_check(["python3", "-V"]), ([lab.sys.executable, "-V"], None))
        with patch.object(lab.native, "WINDOWS", True):  # the whole check, as Windows runs it, on this machine
            self.checks(ENV_CHECK)
            self.assertTrue(lab.verify(self.run_dir)["passed"])

    def test_env_tricks_and_disallowed_programs_are_refused_with_the_reason(self):
        cases = {
            ("env", "-S", "python3 -c 0"): "env option -S",
            ("env", "-i", "sh", "-c", "true"): "env option -i",
            ("env", "-i", "python3", "-c", "0"): "env option -i",
            ("env", "A=1", "-u", "B", "python3"): "env option -u",
            ("env", "--", "python3", "-c", "0"): "env option --",
            ("env", "PYTHONDONTWRITEBYTECODE=1"): "no program",
            ("env",): "no program",
            ("env", "A=1", "sh", "-c", "true"): "sh",
            ("env", "A=1", "env", "python3", "-c", "0"): "env",
            ("env", "PATH=/tmp/x", "python3", "-c", "0"): "PATH",
            ("env", "DYLD_INSERT_LIBRARIES=/tmp/x.dylib", "python3", "-c", "0"): "DYLD_INSERT_LIBRARIES",
            ("sh", "-c", "true"): "sh",
        }
        for argv, reason in cases.items():
            with self.subTest(argv=argv):
                self.checks(["python3", "-c", "0"], list(argv))
                report = lab.verify(self.run_dir)
                self.assertFalse(report["passed"])
                self.assertEqual(report["checks"], [], "no check runs while one is refused")
                self.assertEqual([r["index"] for r in report["rejected"]], [1])
                self.assertIn(reason, report["rejected"][0]["reason"])
                self.assertEqual(self.evidence()["rejected"], report["rejected"])
                self.assertIn(reason, (self.run_dir / "verification.md").read_text())

    def test_the_plugin_test_runner_may_be_a_check_but_no_other_claude_command(self):
        """Third review: the guard tests could not be recorded evidence, because claude was refused outright."""
        for argv in (["claude", "plugin", "test", "lab/plugin"], ["/Users/u/.local/bin/claude", "plugin", "validate", "lab/plugin"],
                     ["env", "A=1", "claude", "plugin", "test", "lab/plugin"]):
            with self.subTest(argv=argv):
                self.assertIsNone(lab.check_problem(argv))
        for argv in (["claude", "-p", "hi"], ["claude", "plugin", "install", "x"], ["claude", "plugin", "test", "--print", "x"],
                     ["claude", "plugin", "test", "lab/plugin", "-p"], ["claude"], ["claude", "plugin", "test"]):
            with self.subTest(argv=argv):
                self.assertIn("claude may run only as claude plugin test", lab.check_problem(argv))

    def test_a_refused_check_replaces_older_passing_evidence(self):
        self.checks(["python3", "-c", "0"])
        self.assertTrue(lab.verify(self.run_dir)["passed"])
        self.assertTrue(self.evidence()["passed"])
        self.checks(["env", "-S", "python3 -c 0"])
        report = lab.verify(self.run_dir)
        self.assertFalse(report["passed"])
        self.assertFalse(self.evidence()["passed"], "older passing evidence must not survive a refused verify")

    def test_no_checks_is_reported_as_unverified(self):
        report = lab.verify(self.run_dir)
        self.assertFalse(report["passed"])
        self.assertIn("Record 1", report["error"])
        self.assertFalse(self.evidence()["passed"])

    def test_the_command_line_prints_the_refusal_and_exits_nonzero(self):
        self.checks(["env", "-S", "python3 -c 0"])
        out = io.StringIO()
        with patch.object(lab, "sync_models", return_value={}), patch.object(lab.sys, "argv", ["lab.py", "verify", "--run", str(self.run_dir)]), \
                contextlib.redirect_stdout(out):
            code = lab.main()
        self.assertEqual(code, 1)
        self.assertIn("env option -S", out.getvalue())

    def test_checks_lists_the_refusals_before_verify_runs_anything(self):
        self.checks(ENV_CHECK, ["make", "test"], ["bash", "-c", "true"])
        self.assertEqual([(r["index"], r["argv"][0]) for r in lab.check_refusals(self.run_dir)], [(2, "bash")])
        out = io.StringIO()
        with patch.object(lab, "sync_models", return_value={}), patch.object(lab.sys, "argv", ["lab.py", "checks", "--run", str(self.run_dir)]), \
                contextlib.redirect_stdout(out):
            self.assertEqual(lab.main(), 0)
        self.assertEqual([r["index"] for r in json.loads(out.getvalue())["rejected"]], [2])

    def test_evidence_names_the_acceptance_serial_it_ran(self):
        metrics = json.loads((self.run_dir / "metrics.json").read_text())
        lab.dump(self.run_dir / "metrics.json", {**metrics, "acceptanceSerial": 3})
        self.checks(["python3", "-c", "0"])
        self.assertEqual(lab.verify(self.run_dir)["acceptanceSerial"], 3)

    def test_resume_restores_missing_policy_files(self):
        (self.run_dir / "trading-policy.md").unlink()
        lab.upgrade_run(self.run_dir)
        self.assertEqual((self.run_dir / "trading-policy.md").read_text(), "policy")
        self.assertEqual((self.run_dir / "personal-core.md").read_text(), "core")


if __name__ == "__main__":
    unittest.main()
