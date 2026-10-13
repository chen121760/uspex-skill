#!/usr/bin/env python3
"""Offline tests for the site-neutral Slurm submit callback."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "submission"
    / "slurm"
    / "submitJob_local.py"
)
SPEC = importlib.util.spec_from_file_location("slurm_submit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(["sbatch", "myrun"], returncode, stdout, stderr)


class SlurmSubmitTests(unittest.TestCase):
    def configured(self):
        return (
            patch.object(MODULE, "PARTITION", "gpu"),
            patch.object(MODULE, "JOB_PREAMBLE", "module load engine"),
            patch.object(MODULE, "JOB_NAME_PREFIX", "LiAlZrCl-A"),
        )

    def test_unresolved_placeholders_fail_before_sbatch(self) -> None:
        with patch.object(MODULE.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                MODULE.submit_job_local(1, "python engine.py")
            run.assert_not_called()

    def test_success_preserves_command_once_and_parses_id(self) -> None:
        command = "python engine.py --name 'A B' > engine.log 2>&1"
        partition, preamble, prefix = self.configured()
        with tempfile.TemporaryDirectory() as tmp, partition, preamble, prefix, patch.object(
            MODULE.subprocess,
            "run",
            return_value=completed(stdout="Submitted batch job 3511385\n"),
        ):
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                self.assertEqual(MODULE.submit_job_local(12, command), "3511385")
                script = Path("myrun").read_text(encoding="utf-8")
            finally:
                os.chdir(old_cwd)
        self.assertEqual(script.count(command), 1)
        self.assertIn("#SBATCH --job-name=LiAlZrCl-A-12", script)
        self.assertNotRegex(script, r"<[A-Z0-9_]+>")

    def test_sbatch_failure_is_raised(self) -> None:
        partition, preamble, prefix = self.configured()
        with tempfile.TemporaryDirectory() as tmp, partition, preamble, prefix, patch.object(
            MODULE.subprocess,
            "run",
            return_value=completed(returncode=1, stderr="submission rejected\n"),
        ):
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaisesRegex(RuntimeError, "sbatch failed"):
                    MODULE.submit_job_local(1, "python engine.py")
            finally:
                os.chdir(old_cwd)

    def test_ambiguous_output_is_not_a_job_id(self) -> None:
        partition, preamble, prefix = self.configured()
        with tempfile.TemporaryDirectory() as tmp, partition, preamble, prefix, patch.object(
            MODULE.subprocess,
            "run",
            return_value=completed(stdout="queued somehow\n"),
        ):
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaisesRegex(RuntimeError, "could not parse"):
                    MODULE.submit_job_local(1, "python engine.py")
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()
