#!/usr/bin/env python3
"""Site-neutral Slurm submit callback for USPEX 10.5/10.6."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

# EDIT THESE VALUES FOR THE TARGET CLUSTER.
PARTITION = "<PARTITION>"
ACCOUNT = ""  # e.g. "project123"
NODES = 1
NTASKS = 8
CPUS_PER_TASK = 1
GPUS = 0
WALLTIME = "06:00:00"
JOB_PREAMBLE = """<LOAD_MODULES_AND_ACTIVATE_ENV>"""

PLACEHOLDER = re.compile(r"<[A-Z0-9_]+>")


def _validate_configuration() -> None:
    rendered = "\n".join([PARTITION, ACCOUNT, WALLTIME, JOB_PREAMBLE])
    unresolved = sorted(set(PLACEHOLDER.findall(rendered)))
    if unresolved:
        raise RuntimeError(f"unresolved Slurm template placeholder(s): {unresolved}")
    if NODES < 1 or NTASKS < 1 or CPUS_PER_TASK < 1 or GPUS < 0:
        raise RuntimeError("invalid Slurm resource counts")


def submit_job_local(index: int, command_executable: str) -> str:
    _validate_configuration()
    if not command_executable.strip():
        raise RuntimeError("commandExecutable is empty")
    job_name = f"USPEX-{index}"
    directives = [
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={PARTITION}",
        f"#SBATCH --nodes={NODES}",
        f"#SBATCH --ntasks={NTASKS}",
        f"#SBATCH --cpus-per-task={CPUS_PER_TASK}",
        f"#SBATCH --time={WALLTIME}",
        "#SBATCH --output=slurm-%j.out",
    ]
    if ACCOUNT:
        directives.append(f"#SBATCH --account={ACCOUNT}")
    if GPUS:
        directives.append(f"#SBATCH --gpus={GPUS}")

    script = "\n".join(
        [
            "#!/usr/bin/env bash",
            *directives,
            "set -euo pipefail",
            JOB_PREAMBLE,
            'export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"',
            command_executable,
            "",
        ]
    )
    run_file = Path("myrun")
    run_file.write_text(script, encoding="utf-8")

    proc = subprocess.run(
        ["sbatch", str(run_file)], text=True, capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"sbatch failed: {proc.stderr.strip() or proc.stdout.strip()}")
    match = re.search(r"Submitted\s+batch\s+job\s+(\d+)", proc.stdout)
    if not match:
        raise RuntimeError(f"could not parse Slurm job ID from: {proc.stdout!r}")
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
