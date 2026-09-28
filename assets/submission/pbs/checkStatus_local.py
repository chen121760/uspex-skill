#!/usr/bin/env python3
"""Return 0 while a PBS/Torque job is active and 1 once it is terminal."""

from __future__ import annotations

import argparse
import re
import subprocess

ACTIVE_STATES = {"Q", "R", "H", "W", "T", "S", "E", "B", "M"}
TERMINAL_STATES = {"C", "F", "X"}


def check_status_local(job_id: str) -> bool:
    proc = subprocess.run(
        ["qstat", "-f", str(job_id)], text=True, capture_output=True, check=False
    )
    message = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode != 0:
        lower = message.lower()
        if "unknown job" in lower or "unknown job id" in lower or "not found" in lower:
            return True
        raise RuntimeError(f"qstat failed: {message}")

    match = re.search(r"job_state\s*=\s*([A-Za-z])", proc.stdout)
    if not match:
        raise RuntimeError(f"could not parse PBS job_state from: {proc.stdout!r}")
    state = match.group(1).upper()
    if state in ACTIVE_STATES:
        return False
    if state in TERMINAL_STATES:
        return True
    raise RuntimeError(f"unrecognized PBS job state {state!r}")


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
