#!/usr/bin/env python3
"""Site-neutral PBS/Torque submit callback for USPEX 10.5/10.6."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# EDIT THESE VALUES FOR THE TARGET CLUSTER.
QUEUE = "<QUEUE>"
ACCOUNT = ""  # e.g. "project123"
RESOURCE_LINE = "nodes=1:ppn=8"  # or PBS Pro: select=1:ncpus=8:mpiprocs=8
WALLTIME = "06:00:00"
JOB_PREAMBLE = """<LOAD_MODULES_AND_ACTIVATE_ENV>"""


def submit_job_local(index: int, command_executable: str) -> str:
    job_name = f"USPEX-{index}"
    directives = [
        f"#PBS -N {job_name}",
        f"#PBS -q {QUEUE}",
        f"#PBS -l {RESOURCE_LINE}",
        f"#PBS -l walltime={WALLTIME}",
        "#PBS -j oe",
    ]
    if ACCOUNT:
        directives.append(f"#PBS -A {ACCOUNT}")

    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            *directives,
            "set -euo pipefail",
            'cd "${PBS_O_WORKDIR:?PBS_O_WORKDIR is not set}"',
            JOB_PREAMBLE,
            command_executable,
            "",
        ]
    )
    run_file = Path("myrun")
    run_file.write_text(script, encoding="utf-8")

    proc = subprocess.run(
        ["qsub", str(run_file)], text=True, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"qsub failed: {proc.stderr.strip() or proc.stdout.strip()}")
    match = re.search(r"(?<!\d)(\d+)(?:\.[A-Za-z0-9_.-]+)?", proc.stdout)
    if not match:
        raise RuntimeError(f"could not parse PBS job ID from: {proc.stdout!r}")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", dest="index", type=int, required=True)
    parser.add_argument("-c", dest="command_executable", required=True)
    args = parser.parse_args()
    job_id = submit_job_local(args.index, args.command_executable)
    print("<CALLRESULT>")
    print(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
