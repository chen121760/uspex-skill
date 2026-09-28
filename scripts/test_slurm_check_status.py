#!/usr/bin/env python3
"""Unit tests for the site-neutral Slurm status callback."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "submission"
    / "slurm"
    / "checkStatus_local.py"
)
SPEC = importlib.util.spec_from_file_location("slurm_check_status", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def completed(
    argv: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class SlurmStatusTests(unittest.TestCase):
    def test_any_squeue_row_is_still_active(self) -> None:
        with patch.object(MODULE, "_run", return_value=completed([], stdout="PENDING\n")):
            self.assertFalse(MODULE.check_status_local("3511385"))

    def test_exact_invalid_job_id_can_be_terminal(self) -> None:
        responses = [
            completed(
                [],
                returncode=1,
                stderr="slurm_load_jobs error: Invalid job id specified\n",
            ),
            completed([], stdout="COMPLETED|\n"),
        ]
        with patch.object(MODULE, "_run", side_effect=responses), patch.object(
            MODULE.shutil, "which", return_value="/usr/bin/sacct"
        ):
            self.assertTrue(MODULE.check_status_local("3491780"))

    def test_controller_failure_is_not_done(self) -> None:
        proc = completed(
            [],
            returncode=1,
            stderr="slurm_load_jobs error: Unable to contact slurm controller\n",
        )
        with patch.object(MODULE, "_run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "state is unknown"):
                MODULE.check_status_local("123")

    def test_empty_squeue_but_active_sacct_is_not_done(self) -> None:
        responses = [completed([], stdout=""), completed([], stdout="RUNNING|\n")]
        with patch.object(MODULE, "_run", side_effect=responses), patch.object(
            MODULE.shutil, "which", return_value="/usr/bin/sacct"
        ):
            self.assertFalse(MODULE.check_status_local("123"))

    def test_sacct_failure_is_not_silently_terminal(self) -> None:
        responses = [
            completed([], stdout=""),
            completed([], returncode=1, stderr="slurmdbd unavailable\n"),
        ]
        with patch.object(MODULE, "_run", side_effect=responses), patch.object(
            MODULE.shutil, "which", return_value="/usr/bin/sacct"
        ):
            with self.assertRaisesRegex(RuntimeError, "sacct failed"):
                MODULE.check_status_local("123")

    def test_empty_squeue_without_sacct_is_terminal(self) -> None:
        with patch.object(MODULE, "_run", return_value=completed([], stdout="")), patch.object(
            MODULE.shutil, "which", return_value=None
        ):
            self.assertTrue(MODULE.check_status_local("123"))


if __name__ == "__main__":
    unittest.main()
