#!/usr/bin/env python3
"""Decide whether a USPEX 10.5/10.6 run is finished, healthy, stalled, or broken.

This is an *evidence* tool, not a progress viewer. It reads the artefacts a run
leaves behind, reports what it can and cannot establish, and returns an exit code
that tells a caller what to do next:

    0  done        USPEX_IS_DONE is present; the search terminated.
    1  running     work is in flight (or queued) and nothing forbids waiting.
    2  intervene   stalled, or the driver/jobs failed; a human or agent must act.
    3  unknown     not enough evidence on disk (wrong directory, nothing started).

Two failure modes this tool exists to prevent, both observed in production:

* reporting "still running" for a run whose engine died silently, and
* reporting "finished" (or "0 failures") for a run that never produced usable
  structures.

Consequences for the design: a missing artefact is reported as ``null`` in
``evidence_missing`` rather than as a zero, sentinel energies are counted
separately from real ones, and ``state`` degrades to ``stalled``/``unknown``
instead of guessing. Scheduler and process state are read only when the tool is
run on the machine that owns them; both can be injected from files so the whole
decision path stays testable offline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

EXIT_DONE = 0
EXIT_RUNNING = 1
EXIT_INTERVENE = 2
EXIT_UNKNOWN = 3

SCHEMA_VERSION = 1

# USPEX documents these as "this structure failed / was never evaluated".
UNCONVERGED_ENERGY = 999999.0

# Terminal scheduler states that mean "this structure calculation will not finish".
FAILED_JOB_STATES = {
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "BOOT_FAIL",
    "DEADLINE",
    "PREEMPTED",
    "REVOKED",
}
ACTIVE_JOB_STATES = {
    "PENDING",
    "CONFIGURING",
    "RUNNING",
    "COMPLETING",
    "REQUEUED",
    "REQUEUE_FED",
    "REQUEUE_HOLD",
    "RESIZING",
    "SIGNALING",
    "STOPPED",
    "SUSPENDED",
}

MARKER_FILES = (
    "USPEX_IS_DONE",
    "still_running",
    "still_reading",
    "STOP_USPEX_LOOP",
    ".uspex-runner.lock",
)

# Files worth handing to an interactive analyser. Deliberately excludes
# results*/generation*, CalcFold*, *.mat, logs and archives.
BUNDLE_FILES = (
    "Individuals",
    "BESTIndividuals",
    "Parameters.txt",
    "origin",
    "gatheredPOSCARS",
    "BESTgatheredPOSCARS",
    "gatheredPOSCARS_unrelaxed",
    "Pareto_ranking",
    "ParetoRankingInGeneration",
    "MLProperties",
    "Properties",
    "compositionStatistics",
    "Warnings",
    "convex_hull",
    "extended_convex_hull",
    "enthalpies_complete.dat",
)
BUNDLE_CORE = ("Individuals", "Parameters.txt", "gatheredPOSCARS")

VERSION_RE = re.compile(r"version\s+(\d+\.\d+[0-9A-Za-z._-]*)\s*\(([^)]*)\)")
SUMMARY_RE = re.compile(r"SUMMARY of Generation\s+(\d+)", re.IGNORECASE)
GENERATION_HEADER_RE = re.compile(r"^-+\s*Generation\s+(\d+)\s*-+$", re.IGNORECASE)
INT_PATTERNS = {
    "configured_generations": re.compile(r"Number of Generations\s*:\s*(\d+)", re.IGNORECASE),
    "initial_population_size": re.compile(r"Initial Population Size\s*:\s*(\d+)", re.IGNORECASE),
    "population_size": re.compile(r"General Population Size\s*:\s*(\d+)", re.IGNORECASE),
    "num_parallel_calcs": re.compile(r"(\d+)\s+parallel calculations are performed", re.IGNORECASE),
    "relaxation_steps": re.compile(r"There are\s+(\d+)\s+local relaxation steps", re.IGNORECASE),
    "types_of_atoms": re.compile(r"There are\s+(\d+)\s+types of atoms", re.IGNORECASE),
}
FLOAT_PATTERNS = {
    "external_pressure_gpa": re.compile(r"External Pressure is\s*:\s*([-+0-9.eE]+)\s*GPa", re.IGNORECASE),
}
STR_PATTERNS = {
    "job_started_at": re.compile(r"Job Starts at\s{2,}(.+?)\s*$", re.IGNORECASE),
    "job_finished_at": re.compile(r"Job Finished at\s{2,}(.+?)\s*$", re.IGNORECASE),
    "lowest_target_fitness": re.compile(r"lowest target fitness is\s*([-+0-9.eE]+)\s*eV", re.IGNORECASE),
    "atom_types": re.compile(r"types of atoms in the system:\s*(.+?)\s*$", re.IGNORECASE),
}
MAX_GENERATION_RE = re.compile(r"Maximum Generation number achieved:\s*(\d+)", re.IGNORECASE)
TOTAL_RELAXATIONS_RE = re.compile(r"This calculation runs\s+(\d+)\s+structural relaxations", re.IGNORECASE)

DRIVER_START_RE = re.compile(r"\[\s*([^\]]+)\s*\]\s*driver start", re.IGNORECASE)
DRIVER_RUN_RE = re.compile(r"\[\s*([^\]]+)\s*\]\s*USPEX -r start", re.IGNORECASE)
DRIVER_EXIT_RE = re.compile(r"\[\s*([^\]]+)\s*\]\s*USPEX -r exit=(-?\d+)", re.IGNORECASE)
DRIVER_STOP_RE = re.compile(r"\[\s*([^\]]+)\s*\]\s*stopping after (\d+) consecutive failures", re.IGNORECASE)
DRIVER_FAIL_LOG_RE = re.compile(r"environment health check failed")
DRIVER_DONE_RE = re.compile(r"USPEX_IS_DONE detected", re.IGNORECASE)

JOBS_HISTORY_RE = re.compile(
    r"Generation\s+(\d+)\s+Step\s+(\d+)\s+of\s+Structure\s+(\d+)\s+at\s+CalcFold\s*(\d+)\s*:\s*JOBID\s+(\d+)"
    r"(?:\s+Submitted\s+(\S+))?",
    re.IGNORECASE,
)

# An Individuals data row starts with "Gen ID Origin"; the composition column is
# bracket-padded, so row counting must not depend on column alignment.
INDIVIDUALS_ROW_RE = re.compile(r"^\s*\d+\s+\d+\s+[A-Za-z]")
PARETO_ROW_RE = re.compile(r"^\s*\d+\s+\d+\s+[A-Za-z]")
PRIG_SECTION_RE = re.compile(r"^-+\s*generation\s+(\d+)\s*-+$", re.IGNORECASE)

FIXED_INDIVIDUAL_COLUMNS = {
    "gen",
    "id",
    "origin",
    "composition",
    "enthalpy",
    "volume",
    "density",
    "kpoints",
    "symm",
    "q_entr",
    "a_order",
    "s_order",
    "convexhull",
    "convex_hull",
}
# Trailing descriptive columns such as "Magmoment-Type" are not objectives.
FIXED_INDIVIDUAL_PREFIXES = ("magmoment", "mag_moment")


def normalize_path(value: str) -> str:
    """Compare paths from different platforms and mount views.

    ``Path.resolve()`` on a Windows host turns a POSIX path into a drive-prefixed
    one, which would break workdir comparison in tests and in any recorded
    scheduler dump. Normalising to a slash form without the drive prefix keeps
    ``/home/x/run`` and ``D:\\home\\x\\run`` comparable.
    """
    text = value.strip().replace("\\", "/").rstrip("/")
    match = re.match(r"^[A-Za-z]:/(.*)$", text)
    if match:
        return "/" + match.group(1)
    return text or "/"


def root_variants(run_dir: Path) -> set[str]:
    """The run directory as given and as resolved.

    Slurm reports the workdir it was given while the caller may hold a symlinked
    or short-name path; accepting both forms avoids losing every job of a run
    whose directory is reached through a link.
    """
    variants = {normalize_path(str(run_dir))}
    try:
        variants.add(normalize_path(str(run_dir.resolve())))
    except OSError:  # pragma: no cover - filesystem dependent
        pass
    return variants


@dataclass
class Warning_:
    level: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "message": self.message}


@dataclass
class Report:
    run_dir: str
    observed_at: str
    results_dir: str | None = None
    state: str = "unknown"
    phase: str = "unknown"
    exit_code: int = EXIT_UNKNOWN
    uspex_version: str | None = None
    markers: dict[str, bool] = field(default_factory=dict)
    progress: dict[str, object] = field(default_factory=dict)
    driver: dict[str, object] = field(default_factory=dict)
    structures: dict[str, object] = field(default_factory=dict)
    individuals: dict[str, object] = field(default_factory=dict)
    pareto: dict[str, object] = field(default_factory=dict)
    jobs: dict[str, object] = field(default_factory=dict)
    concurrent_writers: dict[str, object] = field(default_factory=dict)
    warnings: list[Warning_] = field(default_factory=list)
    evidence_missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(Warning_("WARN", message))

    def info(self, message: str) -> None:
        self.warnings.append(Warning_("INFO", message))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_dir": self.run_dir,
            "observed_at": self.observed_at,
            "results_dir": self.results_dir,
            "state": self.state,
            "phase": self.phase,
            "exit_code": self.exit_code,
            "uspex_version": self.uspex_version,
            "markers": self.markers,
            "progress": self.progress,
            "driver": self.driver,
            "structures": self.structures,
            "individuals": self.individuals,
            "pareto": self.pareto,
            "jobs": self.jobs,
            "concurrent_writers": self.concurrent_writers,
            "warnings": [item.as_dict() for item in self.warnings],
            "evidence_missing": self.evidence_missing,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# Pure parsers (fed text, so every branch is testable without a cluster)
# --------------------------------------------------------------------------- #


def parse_output_txt(text: str) -> dict[str, object]:
    """Extract the run contract and generation progress from one OUTPUT.txt."""
    result: dict[str, object] = {
        "uspex_version": None,
        "uspex_release_date": None,
        "completed_generations": [],
        "current_generation": None,
        "max_generation_achieved": None,
        "total_relaxations": None,
    }
    # Stable schema: every key exists, unmatched ones stay None, so a consumer
    # never has to distinguish "absent" from "not found in this file".
    for key in INT_PATTERNS:
        result[key] = None
    for key in FLOAT_PATTERNS:
        result[key] = None
    for key in STR_PATTERNS:
        result[key] = None
    completed: set[int] = set()
    current: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if result["uspex_version"] is None:
            match = VERSION_RE.search(line)
            if match:
                result["uspex_version"] = match.group(1)
                result["uspex_release_date"] = match.group(2).strip()
        match = SUMMARY_RE.search(stripped)
        if match:
            completed.add(int(match.group(1)))
            continue
        match = GENERATION_HEADER_RE.match(stripped)
        if match:
            current = int(match.group(1))
            continue
        for key, pattern in INT_PATTERNS.items():
            if result.get(key) is not None:
                continue
            match = pattern.search(stripped)
            if match:
                result[key] = int(match.group(1))
        for key, pattern in FLOAT_PATTERNS.items():
            if result.get(key) is not None:
                continue
            match = pattern.search(stripped)
            if match:
                try:
                    result[key] = float(match.group(1))
                except ValueError:
                    pass
        for key, pattern in STR_PATTERNS.items():
            if result.get(key) is not None:
                continue
            match = pattern.search(stripped)
            if match:
                result[key] = match.group(1).strip()
        if result["max_generation_achieved"] is None:
            match = MAX_GENERATION_RE.search(stripped)
            if match:
                result["max_generation_achieved"] = int(match.group(1))
        if result["total_relaxations"] is None:
            match = TOTAL_RELAXATIONS_RE.search(stripped)
            if match:
                result["total_relaxations"] = int(match.group(1))
    result["completed_generations"] = sorted(completed)
    result["current_generation"] = current
    return result


def parse_driver_log(text: str) -> dict[str, object]:
    """Summarise a run_uspex_loop.sh log without trusting its exit status alone."""
    info: dict[str, object] = {
        "starts": 0,
        "last_start": None,
        "last_exit_code": None,
        "consecutive_failures": 0,
        "stopping": False,
        "done_detected": False,
        "health_check_failed": False,
    }
    for line in text.splitlines():
        match = DRIVER_RUN_RE.search(line)
        if match:
            info["starts"] = int(info["starts"]) + 1
            info["last_start"] = match.group(1).strip()
            continue
        match = DRIVER_EXIT_RE.search(line)
        if match:
            code = int(match.group(2))
            info["last_exit_code"] = code
            info["consecutive_failures"] = 0 if code == 0 else int(info["consecutive_failures"]) + 1
            continue
        match = DRIVER_STOP_RE.search(line)
        if match:
            info["stopping"] = True
            info["consecutive_failures"] = int(match.group(2))
            continue
        if DRIVER_START_RE.search(line):
            info["last_start"] = DRIVER_START_RE.search(line).group(1).strip()  # type: ignore[union-attr]
            continue
        if DRIVER_DONE_RE.search(line):
            info["done_detected"] = True
        if DRIVER_FAIL_LOG_RE.search(line):
            info["health_check_failed"] = True
    return info


def parse_jobs_history(text: str) -> dict[str, object]:
    """Last submission recorded in CalcFoldTemp/Jobs.history."""
    entries = []
    for line in text.splitlines():
        match = JOBS_HISTORY_RE.search(line)
        if match:
            entries.append(
                {
                    "generation": int(match.group(1)),
                    "step": int(match.group(2)),
                    "structure": int(match.group(3)),
                    "fold": int(match.group(4)),
                    "job_id": match.group(5),
                    "submitted": (match.group(6) or "").strip() or None,
                }
            )
    last = entries[-1] if entries else None
    return {
        "submissions": len(entries),
        "generations": sorted({entry["generation"] for entry in entries}),
        "job_ids": [entry["job_id"] for entry in entries],
        "last": last,
    }


def _split_table_header(line: str) -> list[str]:
    return line.split()


def _is_fixed_individual_column(name: str) -> bool:
    folded = name.strip().casefold()
    if folded in FIXED_INDIVIDUAL_COLUMNS:
        return True
    return any(folded.startswith(prefix) for prefix in FIXED_INDIVIDUAL_PREFIXES)


def parse_individuals(text: str) -> dict[str, object]:
    """Count Individuals rows and identify the variable-objective column."""
    lines = text.splitlines()
    header: list[str] = []
    objective_columns: list[str] = []
    rows = 0
    generations: set[int] = set()
    for index, line in enumerate(lines):
        if index == 0 and line.strip():
            header = _split_table_header(line.strip())
            objective_columns = [name for name in header if not _is_fixed_individual_column(name)]
            continue
        if not line.strip():
            continue
        if not INDIVIDUALS_ROW_RE.match(line):
            continue
        rows += 1
        token = line.split()[0]
        try:
            generations.add(int(token))
        except ValueError:
            pass
    return {
        "rows": rows,
        "columns": header,
        "objective_columns": objective_columns,
        "generations": sorted(generations),
    }


def parse_pareto_ranking(text: str) -> dict[str, object]:
    fronts: set[int] = set()
    ids: set[int] = set()
    rows = 0
    for index, line in enumerate(text.splitlines()):
        if index < 2 or not line.strip():
            continue
        if not PARETO_ROW_RE.match(line):
            continue
        rows += 1
        tokens = line.split()
        try:
            fronts.add(int(tokens[0]))
            ids.add(int(tokens[1]))
        except (ValueError, IndexError):
            continue
    return {"rows": rows, "fronts": sorted(fronts), "ids": len(ids)}


def parse_pareto_ranking_in_generation(text: str) -> dict[str, object]:
    generations: list[int] = []
    front_sizes: dict[str, int] = {}
    current: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        match = PRIG_SECTION_RE.match(stripped)
        if match:
            current = int(match.group(1))
            generations.append(current)
            front_sizes[str(current)] = 0
            continue
        if current is None or not stripped:
            continue
        if stripped[:1].isdigit():
            front_sizes[str(current)] = front_sizes.get(str(current), 0) + 1
    return {"generations": generations, "rows_by_generation": front_sizes}


def parse_scheduler_dump(text: str) -> list[dict[str, str]]:
    """Parse ``squeue -h -o '%T|%j|%Z'`` (or the injected equivalent)."""
    jobs: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        jobs.append({"state": parts[0].strip().upper(), "name": parts[1].strip(), "workdir": parts[2].strip()})
    return jobs


def attribute_jobs(jobs: list[dict[str, str]], run_dir: Path) -> dict[str, object]:
    """Attribute scheduler jobs to this run by workdir, never by job name.

    Job names are site- and callback-defined (this project saw both ``USPEX-<id>``
    and ``U106LiD-<id>`` for the same account), so only the working directory
    proved reliable in practice.
    """
    root = normalize_path(str(run_dir))
    by_state: dict[str, int] = {}
    ours = 0
    foreign = 0
    foreign_without_workdir = 0
    for job in jobs:
        workdir = job.get("workdir", "")
        if not workdir:
            foreign_without_workdir += 1
            continue
        normalised = normalize_path(workdir)
        if normalised == root or normalised.startswith(root + "/"):
            ours += 1
            by_state[job["state"]] = by_state.get(job["state"], 0) + 1
        else:
            foreign += 1
    active = sum(count for state, count in by_state.items() if state in ACTIVE_JOB_STATES)
    failed = sum(count for state, count in by_state.items() if state in FAILED_JOB_STATES)
    return {
        "total": ours,
        "by_state": dict(sorted(by_state.items())),
        "active": active,
        "failed": failed,
        "other_jobs_on_account": foreign,
        "jobs_without_workdir": foreign_without_workdir,
    }


def parse_process_dump(text: str) -> list[dict[str, str]]:
    """Parse ``pid<TAB>cwd<TAB>cmdline`` lines produced by gather_processes()."""
    processes: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        processes.append({"pid": parts[0].strip(), "cwd": parts[1].strip(), "cmdline": parts[2].strip()})
    return processes


def classify_writers(processes: list[dict[str, str]], run_dir: Path) -> dict[str, object]:
    root = normalize_path(str(run_dir))
    engines: list[dict[str, str]] = []
    drivers: list[dict[str, str]] = []
    for process in processes:
        cmdline = process["cmdline"]
        in_run_dir = normalize_path(process["cwd"]) == root
        if "run_uspex_loop" in cmdline and (in_run_dir or root in normalize_path(cmdline)):
            drivers.append(process)
        elif (re.search(r"\bUSPEX\s+-r\b", cmdline) or re.search(r"(^|/)\s*USPEX(\s|$)", cmdline)) and in_run_dir:
            engines.append(process)
    risk = len(engines) > 1 or (len(engines) >= 1 and len(drivers) > 1)
    return {
        "engine_processes": engines,
        "driver_processes": drivers,
        "engine_count": len(engines),
        "driver_count": len(drivers),
        "risk": risk,
    }


# --------------------------------------------------------------------------- #
# Filesystem readers
# --------------------------------------------------------------------------- #


def read_text(path: Path, limit_bytes: int = 64 * 1024 * 1024) -> str | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit_bytes)
    except OSError:
        return None


def newest_age(paths: list[Path], now: float) -> float | None:
    ages = []
    for path in paths:
        try:
            ages.append(now - path.stat().st_mtime)
        except OSError:
            continue
    return min(ages) if ages else None


def pick_results_dir(root: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for item in root.glob("results*"):
        if not item.is_dir():
            continue
        match = re.fullmatch(r"results(\d+)", item.name)
        if match:
            candidates.append((int(match.group(1)), item))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def count_calc_folds(root: Path) -> dict[str, int]:
    folds = 0
    geom_in = 0
    geom_out = 0
    energy = 0
    sentinel = 0
    unparsable = 0
    for item in root.glob("CalcFold*"):
        if not item.is_dir():
            continue
        if not re.fullmatch(r"CalcFold\d+", item.name):
            continue
        folds += 1
        if (item / "geom.in").is_file():
            geom_in += 1
        if (item / "geom.out").is_file():
            geom_out += 1
        energy_file = item / "energy.txt"
        if not energy_file.is_file():
            continue
        text = read_text(energy_file, limit_bytes=4096)
        if text is None:
            continue
        try:
            value = float(text.strip().splitlines()[0].strip())
        except (ValueError, IndexError):
            unparsable += 1
            continue
        energy += 1
        if value >= UNCONVERGED_ENERGY:
            sentinel += 1
    return {
        "calc_folds": folds,
        "with_geom_in": geom_in,
        "with_geom_out": geom_out,
        "with_energy_txt": energy,
        "sentinel_energy": sentinel,
        "unparsable_energy": unparsable,
    }


def gather_scheduler(run_dir: Path) -> tuple[str, str | None]:
    """Return (source, raw squeue output). Never raises."""
    if not shutil.which("squeue"):
        return "unavailable", None
    try:
        proc = subprocess.run(
            ["squeue", "-h", "-u", os.environ.get("USER", ""), "-o", "%T|%j|%Z"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - host dependent
        return f"error: {exc}", None
    if proc.returncode != 0:
        return f"error: {(proc.stderr or proc.stdout).strip()}", None
    return "squeue", proc.stdout


def gather_processes(run_dir: Path) -> tuple[str, str | None]:
    """Return (source, raw dump of pid/cwd/cmdline for engine and driver processes)."""
    if os.name == "nt" or not shutil.which("pgrep"):
        return "unavailable", None
    lines: list[str] = []
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "USPEX -r|run_uspex_loop|USPEX$"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - host dependent
        return f"error: {exc}", None
    for pid in proc.stdout.split():
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        text = read_text(cmdline_path, limit_bytes=4096) or ""
        cmdline = text.replace("\x00", " ").strip()
        lines.append(f"{pid}\t{cwd}\t{cmdline}")
    return "pgrep", "\n".join(lines)


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


def assess(
    root: Path,
    *,
    stale_minutes: float = 30.0,
    jobs_text: str | None = None,
    jobs_source: str = "unavailable",
    processes_text: str | None = None,
    processes_source: str = "unavailable",
    now: float | None = None,
) -> Report:
    now = time.time() if now is None else now
    run_dir = root.resolve()
    report = Report(
        run_dir=str(run_dir),
        observed_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )

    driver_log = run_dir / "uspex-driver.log"
    results_dir = pick_results_dir(run_dir)
    report.results_dir = str(results_dir) if results_dir else None
    output_txt = (results_dir / "OUTPUT.txt") if results_dir else None

    report.markers = {name: (run_dir / name).exists() for name in MARKER_FILES}

    # --- OUTPUT.txt: version + generation contract -------------------------- #
    output_text = read_text(output_txt) if output_txt else None
    if output_text is None:
        report.evidence_missing.append("results*/OUTPUT.txt")
        output_info: dict[str, object] = {}
    else:
        output_info = parse_output_txt(output_text)
    version = output_info.get("uspex_version")
    if version:
        release = output_info.get("uspex_release_date")
        report.uspex_version = f"{version} ({release})" if release else str(version)

    # --- driver -------------------------------------------------------------#
    driver_text = read_text(driver_log)
    driver_info = parse_driver_log(driver_text) if driver_text else {}
    driver_info["present"] = driver_text is not None
    driver_info["age_seconds"] = newest_age([driver_log], now)
    report.driver = driver_info
    if driver_text is None:
        report.evidence_missing.append("uspex-driver.log")

    # --- structures ---------------------------------------------------------#
    report.structures = count_calc_folds(run_dir)

    # --- Individuals / Pareto ----------------------------------------------#
    individuals_path = (results_dir / "Individuals") if results_dir else None
    individuals_text = read_text(individuals_path) if individuals_path else None
    if individuals_text is None:
        report.evidence_missing.append("results*/Individuals")
        report.individuals = {"present": False}
    else:
        info = parse_individuals(individuals_text)
        info["present"] = True
        info["age_seconds"] = newest_age([individuals_path], now)  # type: ignore[list-item]
        report.individuals = info

    pareto_path = (results_dir / "Pareto_ranking") if results_dir else None
    pareto_text = read_text(pareto_path) if pareto_path else None
    if pareto_text is None:
        report.pareto = {"present": False}
    else:
        info = parse_pareto_ranking(pareto_text)
        info["present"] = True
        report.pareto = info

    prig_path = (results_dir / "ParetoRankingInGeneration") if results_dir else None
    prig_text = read_text(prig_path) if prig_path else None
    if prig_text is not None:
        report.progress["pareto_ranking_in_generation"] = parse_pareto_ranking_in_generation(prig_text)

    # --- jobs history -------------------------------------------------------#
    history_path = run_dir / "CalcFoldTemp" / "Jobs.history"
    history_text = read_text(history_path)
    if history_text is not None:
        report.progress["jobs_history"] = parse_jobs_history(history_text)

    # --- progress -----------------------------------------------------------#
    completed = list(output_info.get("completed_generations") or [])
    current = output_info.get("current_generation")
    max_achieved = output_info.get("max_generation_achieved")
    report.progress.update(
        {
            "configured_generations": output_info.get("configured_generations"),
            "initial_population_size": output_info.get("initial_population_size"),
            "population_size": output_info.get("population_size"),
            "num_parallel_calcs": output_info.get("num_parallel_calcs"),
            "external_pressure_gpa": output_info.get("external_pressure_gpa"),
            "atom_types": output_info.get("atom_types"),
            "job_started_at": output_info.get("job_started_at"),
            "job_finished_at": output_info.get("job_finished_at"),
            "lowest_target_fitness": output_info.get("lowest_target_fitness"),
            "completed_generations": completed,
            "current_generation": current,
            "max_generation_achieved": max_achieved,
            "total_relaxations": output_info.get("total_relaxations"),
        }
    )

    # --- scheduler ----------------------------------------------------------#
    if jobs_text is None:
        report.jobs = {"source": jobs_source}
    else:
        attributed = attribute_jobs(parse_scheduler_dump(jobs_text), root)
        attributed["source"] = jobs_source
        report.jobs = attributed

    # --- concurrent writers -------------------------------------------------#
    if processes_text is None:
        report.concurrent_writers = {"source": processes_source}
    else:
        writers = classify_writers(parse_process_dump(processes_text), root)
        writers["source"] = processes_source
        report.concurrent_writers = writers
        if writers["risk"]:
            report.warn(
                "more than one USPEX engine or driver appears to be active for this directory; "
                "two runners can corrupt the same *.mat state — confirm before starting another"
            )
        if (
            writers["engine_count"] >= 1
            and writers["driver_count"] == 0
            and (report.markers.get("still_running") or report.markers.get("still_reading"))
        ):
            report.warn(
                "an engine process is active without the packaged driver loop; starting "
                "run_uspex_loop.sh now would give this run two writers"
            )

    # --- activity -----------------------------------------------------------#
    activity_paths = [driver_log]
    if output_txt is not None:
        activity_paths.append(output_txt)
    for name in ("still_running", "still_reading", "USPEX_IS_DONE"):
        candidate = run_dir / name
        if candidate.exists():
            activity_paths.append(candidate)
    if results_dir is not None:
        for name in ("Individuals", "Pareto_ranking"):
            candidate = results_dir / name
            if candidate.exists():
                activity_paths.append(candidate)
    history = run_dir / "CalcFoldTemp" / "Jobs.history"
    if history.exists():
        activity_paths.append(history)

    activity_age = newest_age(activity_paths, now)
    report.progress["newest_activity_age_seconds"] = activity_age
    report.progress["stale_minutes"] = stale_minutes

    jobs = report.jobs
    scheduler_active = int(jobs.get("active", 0) or 0) > 0
    scheduler_failed = int(jobs.get("failed", 0) or 0) > 0
    sentinel = int(report.structures.get("sentinel_energy", 0) or 0)
    relaxations = report.structures.get("with_energy_txt", 0)

    # --- facts that must not be silently downgraded -------------------------#
    enthalpy_note_done = False
    if individuals_text is not None and pareto_text is not None and not enthalpy_note_done:
        report.notes.append(
            "Individuals reports enthalpy per cell (Enthalpy(eV)) while Pareto_ranking reports "
            "eV/atom; compare per-atom values only after normalising by the composition"
        )
    if sentinel:
        report.warn(
            f"{sentinel} structure(s) carry the unconverged sentinel energy >= {UNCONVERGED_ENERGY:g}; "
            "they are failures, not high-energy candidates"
        )
    if report.structures.get("calc_folds") and not report.structures.get("with_energy_txt"):
        report.info(
            "no energy.txt in this run; energy.txt is a code-99 convention, so a VASP-stage run "
            "reports convergence through OUTCAR/OSZICAR instead"
        )

    # --- state machine ------------------------------------------------------#
    has_any_evidence = bool(
        output_text is not None
        or driver_text is not None
        or results_dir is not None
        or any(report.markers.get(name) for name in ("USPEX_IS_DONE", "still_running", "still_reading"))
        or report.structures.get("calc_folds", 0)
    )

    # Results bundles routinely omit USPEX_IS_DONE, so completion must also be
    # readable from OUTPUT.txt; otherwise a finished run is reported as stalled.
    configured = output_info.get("configured_generations")
    max_generation = output_info.get("max_generation_achieved")
    inferred_done = bool(output_info.get("job_finished_at")) or (
        isinstance(configured, int)
        and isinstance(max_generation, int)
        and configured > 0
        and max_generation >= configured
    )
    report.progress["completion_inferred"] = inferred_done and not report.markers.get("USPEX_IS_DONE")

    if results_dir is not None and not report.structures.get("calc_folds"):
        report.notes.append(
            "no CalcFold* directories in this copy: per-structure stage logs and energies are "
            "unavailable, so structure-level convergence cannot be verified here"
        )

    if report.markers.get("USPEX_IS_DONE") or inferred_done:
        report.state = "done"
        report.phase = "done" if report.markers.get("USPEX_IS_DONE") else "done_inferred"
        report.exit_code = EXIT_DONE
        if not report.markers.get("USPEX_IS_DONE"):
            report.info(
                "USPEX_IS_DONE is absent, but OUTPUT.txt reports the finish ('Job Finished at' or the "
                "configured generation ceiling); the search ended and the marker was not copied"
            )
        if report.markers.get("STOP_USPEX_LOOP"):
            report.info("STOP_USPEX_LOOP is also present; the driver will exit at its next check")
    elif not has_any_evidence:
        report.state = "unknown"
        report.phase = "not_started"
        report.exit_code = EXIT_UNKNOWN
        report.warn(
            "no run artefacts found here; if the run lives elsewhere, pass its root, otherwise "
            "this is not a USPEX calculation directory"
        )
    elif driver_info:
        stopping = bool(driver_info.get("stopping"))
        consecutive = int(driver_info.get("consecutive_failures") or 0)
        exit_code = driver_info.get("last_exit_code")
        if stopping or (exit_code not in (None, 0) and consecutive >= 1 and not scheduler_active):
            report.state = "failed"
            report.phase = "driver_failed"
            report.exit_code = EXIT_INTERVENE
            report.warn(
                f"driver log reports {consecutive} consecutive USPEX failure(s)"
                + (f" (last exit {exit_code})" if exit_code is not None else "")
                + "; fix the cause before restarting the driver"
            )
        if driver_info.get("health_check_failed"):
            report.warn("the driver refused to start because the environment health check failed")
    if report.state == "unknown" and report.exit_code == EXIT_UNKNOWN and has_any_evidence:
        # still undecided: fall through to running / stalled / failed
        if scheduler_failed and not scheduler_active:
            report.state = "failed"
            report.phase = "structure_jobs_failed"
            report.exit_code = EXIT_INTERVENE
            report.warn(
                f"{jobs.get('failed')} attributed scheduler job(s) ended in a failure state and no "
                "job of this run is active; inspect CalcFoldTemp/*.JOB-* and per-structure logs"
            )
        elif not scheduler_active and activity_age is not None and activity_age > stale_minutes * 60:
            report.state = "stalled"
            report.phase = "no_activity"
            report.exit_code = EXIT_INTERVENE
            report.warn(
                f"nothing has changed for {activity_age / 60:.1f} min (> {stale_minutes:g} min) and no "
                "job of this run is active in the scheduler; the engine is not advancing"
            )
        else:
            report.state = "running"
            report.exit_code = EXIT_RUNNING
            if not completed and not relaxations:
                started = bool(
                    scheduler_active
                    or report.structures.get("calc_folds")
                    or report.markers.get("still_running")
                    or report.markers.get("still_reading")
                )
                report.phase = "initial_population_in_progress" if started else "initializing"
            elif current is not None and (not completed or current > max(completed)):
                report.phase = f"generation_{current}_in_progress"
            elif completed and (
                scheduler_active or report.structures.get("calc_folds") or report.markers.get("still_running")
            ):
                # A later USPEX -r batch prints no per-generation banner, so the
                # next generation has to be inferred from the ones already closed.
                report.phase = f"generation_{max(completed) + 1}_in_progress"
            else:
                report.phase = "between_generations"
            if scheduler_active:
                pending = int((jobs.get("by_state") or {}).get("PENDING", 0) or 0)
                running = int((jobs.get("by_state") or {}).get("RUNNING", 0) or 0)
                report.progress["scheduler_note"] = (
                    f"{running} running / {pending} pending structure job(s) attributed to this run"
                )
            if (
                activity_age is not None
                and activity_age > stale_minutes * 60
                and scheduler_active
                and "concurrent_writers" in report.concurrent_writers
                and report.concurrent_writers.get("engine_count", 0) == 0
            ):
                report.warn(
                    f"jobs are active but no new run artefacts appeared for {activity_age / 60:.1f} min "
                    "and no engine process is visible here; verify the run is not orphaned"
                )

    if report.state == "unknown" and report.exit_code == EXIT_UNKNOWN:
        report.exit_code = EXIT_UNKNOWN

    return report


# --------------------------------------------------------------------------- #
# Handoff bundle
# --------------------------------------------------------------------------- #


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def emit_bundle(report: Report, root: Path, output: Path, require_core: bool) -> dict[str, object]:
    results_dir = pick_results_dir(root)
    if results_dir is None:
        raise RuntimeError("no results<N> directory to package")
    present: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    empty: list[str] = []
    for name in BUNDLE_FILES:
        path = results_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        size = path.stat().st_size
        if size == 0:
            empty.append(name)
            continue
        present[name] = {"bytes": size, "sha256": sha256(path)}
    if require_core:
        absent_core = [name for name in BUNDLE_CORE if name not in present]
        if absent_core:
            raise RuntimeError(f"required core result file(s) missing or empty: {', '.join(absent_core)}")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_dir": report.run_dir,
        "results_dir": str(results_dir),
        "uspex_version": report.uspex_version,
        "level": report.state,
        "files": present,
        "missing": missing,
        "empty": empty,
        "excluded": ["results*/generation*", "CalcFold*", "*.mat", "*.log", "archives"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for name in sorted(present):
            archive.add(results_dir / name, arcname=name)
        manifest_path = output.parent / "USPEX_RESULTS_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        archive.add(manifest_path, arcname="USPEX_RESULTS_MANIFEST.json")
        manifest_path.unlink()
    return {
        "archive": str(output),
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "files": sorted(present),
        "missing": missing,
        "empty": empty,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


VALID_STATES = ("done", "running", "stalled", "failed", "unknown")


def render_text(report: Report) -> str:
    lines = [
        f"state       : {report.state} ({report.phase})",
        f"exit code   : {report.exit_code}",
        f"run dir     : {report.run_dir}",
        f"results dir : {report.results_dir or '-'}",
        f"uspex       : {report.uspex_version or 'unknown'}",
        f"markers     : " + ", ".join(f"{name}={value}" for name, value in report.markers.items()),
    ]
    progress = report.progress
    lines.append(
        "generations : completed={completed} current={current} configured={configured} "
        "relaxations={relax}".format(
            completed=progress.get("completed_generations"),
            current=progress.get("current_generation"),
            configured=progress.get("configured_generations"),
            relax=report.structures.get("with_energy_txt"),
        )
    )
    jobs = report.jobs
    if "by_state" in jobs:
        lines.append(
            f"jobs        : {jobs.get('total')} attributed ({jobs.get('by_state')}), "
            f"{jobs.get('other_jobs_on_account')} other jobs on the account"
        )
    else:
        lines.append(f"jobs        : {jobs.get('source')}")
    structures = report.structures
    lines.append(
        "structures  : {folds} CalcFold, {energy} energy.txt, {sentinel} sentinel, {bad} unparsable".format(
            folds=structures.get("calc_folds"),
            energy=structures.get("with_energy_txt"),
            sentinel=structures.get("sentinel_energy"),
            bad=structures.get("unparsable_energy"),
        )
    )
    if report.evidence_missing:
        lines.append("missing     : " + ", ".join(report.evidence_missing))
    for item in report.warnings:
        lines.append(f"{item.level:<11} : {item.message}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "exit codes: 0 done, 1 running, 2 intervene (stalled/failed), 3 unknown\n"
            "scheduler and process state are gathered only when available; inject them with\n"
            "--squeue-output/--processes-output to reproduce a decision offline."
        ),
    )
    parser.add_argument("run_dir", type=Path, help="USPEX calculation root (the directory holding INPUT.txt)")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--text", action="store_true", help="emit the compact human-readable summary (default)")
    parser.add_argument(
        "--stale-minutes",
        type=float,
        default=30.0,
        help="with no active scheduler job, treat this much silence as stalled (default 30)",
    )
    parser.add_argument("--no-scheduler", action="store_true", help="do not call squeue")
    parser.add_argument("--no-processes", action="store_true", help="do not inspect /proc for engine/driver processes")
    parser.add_argument(
        "--squeue-output",
        type=Path,
        help="read a captured 'squeue -h -u USER -o %%T|%%j|%%Z' dump instead of running squeue",
    )
    parser.add_argument(
        "--processes-output",
        type=Path,
        help="read a captured 'pid<TAB>cwd<TAB>cmdline' dump instead of inspecting /proc",
    )
    parser.add_argument("--emit-bundle", type=Path, help="also write a results handoff tar.gz to this path")
    parser.add_argument(
        "--bundle-require-core",
        action="store_true",
        help="fail the bundle step unless Individuals, Parameters.txt and gatheredPOSCARS are present",
    )
    args = parser.parse_args(argv)

    if args.stale_minutes <= 0:
        parser.error("--stale-minutes must be positive")

    root = args.run_dir
    if not root.is_dir():
        print(f"FAIL: not a directory: {root}", file=sys.stderr)
        return EXIT_UNKNOWN

    if args.squeue_output:
        jobs_text = args.squeue_output.read_text(encoding="utf-8", errors="replace")
        jobs_source = f"file:{args.squeue_output}"
    elif args.no_scheduler:
        jobs_text, jobs_source = None, "disabled"
    else:
        jobs_source, jobs_text = gather_scheduler(root)

    if args.processes_output:
        processes_text = args.processes_output.read_text(encoding="utf-8", errors="replace")
        processes_source = f"file:{args.processes_output}"
    elif args.no_processes:
        processes_text, processes_source = None, "disabled"
    else:
        processes_source, processes_text = gather_processes(root)

    report = assess(
        root,
        stale_minutes=args.stale_minutes,
        jobs_text=jobs_text,
        jobs_source=jobs_source,
        processes_text=processes_text,
        processes_source=processes_source,
    )

    if args.emit_bundle:
        try:
            bundle = emit_bundle(report, root, args.emit_bundle, args.bundle_require_core)
        except (OSError, RuntimeError, tarfile.TarError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return EXIT_INTERVENE
        report.notes.append(f"bundle: {json.dumps(bundle, ensure_ascii=False)}")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
