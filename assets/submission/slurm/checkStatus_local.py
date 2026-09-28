#!/usr/bin/env python3
"""Return 0 while a Slurm job is active and 1 once it is terminal."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys

ACTIVE_STATES = {
    "PENDING",
    "CONFIGURING",
    "RUNNING",
    "COMPLETING",
    "REQUEUED",
    "REQUEUE_FED",
    "REQUEUE_HOLD",
    "RESV_DEL_HOLD",
    "RESIZING",
    "SIGNALING",
    "STOPPED",
    "SUSPENDED",
    "STAGE_OUT",
}

INVALID_JOB_ID = re.compile(r"\binvalid job id(?: specified)?\b", re.IGNORECASE)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def _is_invalid_job_id(message: str) -> bool:
    """Recognize only Slurm's explicit unknown/expired-job diagnosis."""
    return bool(INVALID_JOB_ID.search(message))


def check_status_local(job_id: str) -> bool:
    proc = _run(["squeue", "-h", "-j", str(job_id), "-o", "%T"])
    if proc.returncode != 0:
        message = (proc.stderr + proc.stdout).strip()
        if not _is_invalid_job_id(message):
            raise RuntimeError(f"squeue failed; scheduler state is unknown: {message}")

    states = {line.strip().upper() for line in proc.stdout.splitlines() if line.strip()}
    # If squeue still returns a row, Slurm still owns the job. This is safer than
    # treating a site-specific/transitional state as terminal.
    if states:
        return False

    if shutil.which("sacct"):
        acct = _run(
            ["sacct", "-n", "-X", "-j", str(job_id), "--format=State", "--parsable2"]
        )
        if acct.returncode != 0:
            message = (acct.stderr + acct.stdout).strip()
            raise RuntimeError(f"sacct failed; scheduler state is unknown: {message}")
        acct_states = {
            line.split("|", 1)[0].split("+", 1)[0].strip().upper()
            for line in acct.stdout.splitlines()
            if line.strip()
        }
        if acct_states & ACTIVE_STATES:
            return False
        if acct_states:
            print(
                f"Slurm terminal state(s) for {job_id}: {sorted(acct_states)}",
                file=sys.stderr,
            )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-j", dest="job_id", required=True)
    args = parser.parse_args()
    done = check_status_local(args.job_id)
    print("<CALLRESULT>")
    print(int(done))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
