#!/usr/bin/env python3
"""Decide whether a quiet USPEX run is dead or merely inside a long silent phase.

``uspex_status.py`` answers "finished, running, or does it need intervention?" from artefacts and
a *fixed* staleness threshold. That threshold is wrong for searches whose generation cycle takes
hours: a healthy run can be silent for far longer than any constant while the embedded MATLAB
engine ranks the population and evolves the next generation. This tool measures the run's **own**
generation cadence, samples the engine for any movement, and returns one of:

    done     (0)  USPEX_IS_DONE, or OUTPUT.txt records the finish
    working  (1)  artefacts/IO moved, or job(s) of this run are active, or silence is below
                  this run's own generation cadence
    stuck    (2)  engine alive, silent far past this run's own cadence, no file or IO movement,
                  and no active job of this run -> a dead loop; triage the cause before restarting
    stopped  (3)  no engine and no driver for this directory, and not finished
    unknown  (4)  not enough evidence on disk (wrong directory, nothing started)

It never kills, writes or restarts anything: it prints the evidence it used and the next actions,
so the interpretation can be shown to the user before anyone touches the run. See
``references/liveness.md`` for the reasoning, the false-positive traps and the recovery branches.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

EXIT_DONE = 0
EXIT_WORKING = 1
EXIT_STUCK = 2
EXIT_STOPPED = 3
EXIT_UNKNOWN = 4

# A quiet window shorter than this says nothing about a structure search.
MIN_CADENCE_SECONDS = 15 * 60.0
# Fallback when no cadence can be measured on this run: deliberately generous, because calling a
# healthy run dead is the expensive error. Override with --cadence-minutes when the user knows.
DEFAULT_CADENCE_SECONDS = 4 * 3600.0
# Silence past this many cadences, with no IO movement and no job of this run, is a dead loop.
STUCK_CADENCE_FACTOR = 3.0
DEFAULT_SAMPLE_SECONDS = 60.0

RESULTS_RE = re.compile(r"^results(\d+)$")
GENERATION_RE = re.compile(r"^generation(\d+)$")
JOBS_HISTORY_RE = re.compile(
    r"Generation\s+(\d+)\s+Step\s+(\d+)\s+of\s+Structure\s+(\d+)\s+at\s+CalcFold\s*(\d+)\s*:\s*JOBID\s+(\d+)"
    r"(?:\s+Submitted\s+(\S+))?",
    re.IGNORECASE,
)
SUBMITTED_RE = re.compile(r"^([A-Z][a-z]{2})(\d{1,2})-(\d{2}):(\d{2}):(\d{2})$")

MARKER_FILES = ("USPEX_IS_DONE", "still_running", "still_reading", "STOP_USPEX_LOOP")
ROOT_ACTIVITY_FILES = (
    "USPEX_IS_DONE",
    "still_running",
    "still_reading",
    "log",
    "uspex-driver.log",
    "nohup.out",
    "POSCAR",
    "POSCAR_order",
    "Current_POP.mat",
    "Current_ORG.mat",
    "ANTISEEDS.mat",
)
RESULTS_ACTIVITY_FILES = (
    "OUTPUT.txt",
    "Individuals",
    "BESTIndividuals",
    "Pareto_ranking",
    "ParetoRankingInGeneration",
    "gatheredPOSCARS",
    "USPEX.mat",
    "POOL.mat",
)
PROCESS_ACTIVITY = (
    "CalcFoldTemp/Jobs.history",
    "CalcFoldTemp/rc.out",
    "CalcFoldTemp/rc.in",
)

TERMINAL_JOB_STATES = {
    "COMPLETED",
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

MONTHS = {name: index for index, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}


def normalize_path(value: str) -> str:
    """Compare paths from different platforms and mount views (same rule as uspex_status.py)."""
    text = value.strip().replace("\\", "/").rstrip("/")
    match = re.match(r"^[A-Za-z]:/(.*)$", text)
    if match:
        return "/" + match.group(1)
    return text or "/"


def _read_text(path: str, limit: int = 64 * 1024) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def pick_results_dir(root: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    for item in root.glob("results*"):
        match = RESULTS_RE.match(item.name)
        if match and item.is_dir():
            candidates.append((int(match.group(1)), item))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


# --------------------------------------------------------------------------- #
# Cadence: how long this run normally takes to close one generation
# --------------------------------------------------------------------------- #


def generation_cadence(root: Path) -> dict[str, object]:
    """Intervals between finished generations, taken from results*/generation*/ mtimes.

    Only intervals inside one ``results<N>`` folder are used: two folders normally mean two
    attempts, and the gap between attempts is not a generation.
    """
    per_folder: dict[str, list[tuple[float, str]]] = {}
    for results in sorted(root.glob("results*")):
        if not results.is_dir() or not RESULTS_RE.match(results.name):
            continue
        for gen in results.glob("generation*"):
            match = GENERATION_RE.match(gen.name)
            if match and gen.is_dir():
                try:
                    per_folder.setdefault(results.name, []).append((gen.stat().st_mtime, gen.name))
                except OSError:
                    continue
    intervals: list[float] = []
    last: tuple[float, str] | None = None
    for folder in sorted(per_folder, key=lambda name: int(RESULTS_RE.match(name).group(1))):  # type: ignore[union-attr]
        stamps = sorted(per_folder[folder])
        for previous, current in zip(stamps, stamps[1:]):
            intervals.append(current[0] - previous[0])
        if stamps:
            last = (stamps[-1][0], f"{folder}/{stamps[-1][1]}")
    return {
        "source": "results*/generation* mtimes",
        "generations_seen": sum(len(v) for v in per_folder.values()),
        "intervals_seconds": [round(value, 1) for value in intervals],
        "median_seconds": statistics.median(intervals) if intervals else None,
        "last_generation": last[1] if last else None,
        "last_generation_mtime": last[0] if last else None,
    }


def _parse_submitted(token: str, now: datetime) -> datetime | None:
    """Parse the 'Sep28-21:31:26' stamp used by CalcFoldTemp/Jobs.history (no year present)."""
    match = SUBMITTED_RE.match(token.strip())
    if not match:
        return None
    month = MONTHS.get(match.group(1).title())
    if month is None:
        return None
    try:
        stamp = datetime(now.year, month, int(match.group(2)), int(match.group(3)),
                         int(match.group(4)), int(match.group(5)))
    except ValueError:
        return None
    if stamp - now > timedelta(days=7):  # a stamp well ahead of "now" belongs to the previous year
        stamp = stamp.replace(year=stamp.year - 1)
    return stamp


def parse_jobs_history_submissions(text: str, now: datetime | None = None) -> dict[str, object]:
    """First submission time per generation, from CalcFoldTemp/Jobs.history."""
    now = now or datetime.now()
    first_by_generation: dict[int, datetime] = {}
    submissions = 0
    for line in text.splitlines():
        match = JOBS_HISTORY_RE.search(line)
        if not match:
            continue
        submissions += 1
        generation = int(match.group(1))
        stamp = _parse_submitted(match.group(6) or "", now)
        if stamp is None:
            continue
        if generation not in first_by_generation or stamp < first_by_generation[generation]:
            first_by_generation[generation] = stamp
    ordered = [first_by_generation[key] for key in sorted(first_by_generation)]
    intervals = [(current - previous).total_seconds() for previous, current in zip(ordered, ordered[1:])]
    return {
        "source": "CalcFoldTemp/Jobs.history submissions",
        "submissions": submissions,
        "generations_seen": len(first_by_generation),
        "intervals_seconds": [round(value, 1) for value in intervals],
        "median_seconds": statistics.median(intervals) if intervals else None,
        "last_submission": ordered[-1].isoformat(timespec="seconds") if ordered else None,
    }


def resolve_cadence(root: Path, override_seconds: float | None) -> dict[str, object]:
    if override_seconds:
        return {"median_seconds": override_seconds, "source": "command line override",
                "generations_seen": None, "intervals_seconds": [], "last_generation": None,
                "last_generation_mtime": None}
    measured = generation_cadence(root)
    if measured.get("median_seconds"):
        return measured
    history = root / "CalcFoldTemp" / "Jobs.history"
    text = _read_text(str(history), limit=8 * 1024 * 1024)
    if text:
        fallback = parse_jobs_history_submissions(text)
        if fallback.get("median_seconds"):
            fallback["fallback_from"] = measured.get("source")
            return fallback
    return {"median_seconds": None, "source": None, "generations_seen": 0,
            "intervals_seconds": [], "last_generation": None, "last_generation_mtime": None}


# --------------------------------------------------------------------------- #
# Activity: the newest artefact this run produced
# --------------------------------------------------------------------------- #


def newest_activity(root: Path, results_dir: Path | None, now: float | None = None) -> dict[str, object]:
    """Newest artefact mtime across the curated set plus one level inside every CalcFold*.

    Deliberately curated rather than "newest file anywhere": a run directory holds tens of
    thousands of files, and a stray editor backup should not look like progress.
    """
    now = time.time() if now is None else now
    candidates: list[tuple[float, str]] = []

    def add(path: Path) -> None:
        try:
            candidates.append((path.stat().st_mtime, str(path)))
        except OSError:
            return

    for name in ROOT_ACTIVITY_FILES:
        add(root / name)
    if results_dir is not None:
        for name in RESULTS_ACTIVITY_FILES:
            add(results_dir / name)
        for gen in results_dir.glob("generation*"):
            if GENERATION_RE.match(gen.name):
                add(gen)
    for name in PROCESS_ACTIVITY:
        add(root / name)
    for fold in root.glob("CalcFold*"):
        if not fold.is_dir():
            continue
        newest: float | None = None
        newest_name = ""
        try:
            with os.scandir(fold) as entries:
                for entry in entries:
                    try:
                        if entry.is_file():
                            stamp = entry.stat().st_mtime
                            if newest is None or stamp > newest:
                                newest, newest_name = stamp, entry.name
                    except OSError:
                        continue
        except OSError:
            continue
        if newest is not None:
            candidates.append((newest, f"{fold.name}/{newest_name}"))

    if not candidates:
        return {"path": None, "mtime": None, "age_seconds": None, "future_dated": False}
    mtime, path = max(candidates)
    # A file dated ahead of "now" means clock/timezone skew between this host and the writer
    # (common on shared storage); clamp so no caller ever reasons about a negative silence.
    future_dated = mtime > now + 60
    return {"path": path, "mtime": mtime, "age_seconds": max(now - mtime, 0.0),
            "future_dated": future_dated}


# --------------------------------------------------------------------------- #
# Processes and I/O
# --------------------------------------------------------------------------- #


def _role_of(cmdline: str) -> str | None:
    if "matlab_helper" in cmdline:
        return "helper"
    if re.search(r"(^|\s|/)uspex\.x(\s|$)", cmdline):
        return "engine"
    if re.search(r"USPEX\s+-r(\s|$)", cmdline):
        return "driver"
    return None


def _read_proc_cpu_ticks(pid: int, proc_root: str) -> int | None:
    text = _read_text(os.path.join(proc_root, str(pid), "stat"))
    if not text:
        return None
    tail = text[text.rfind(")") + 2:].split()
    if len(tail) < 13:
        return None
    try:
        return int(tail[11]) + int(tail[12])  # utime + stime
    except ValueError:
        return None


def read_proc_io(pid: int, proc_root: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in _read_text(os.path.join(proc_root, str(pid), "io")).splitlines():
        parts = line.split(":")
        if len(parts) == 2 and parts[1].strip().isdigit():
            values[parts[0].strip()] = int(parts[1])
    return values


def discover_processes(run_root: Path, proc_root: str = "/proc") -> list[dict[str, object]]:
    """Engine/driver/helper processes whose working directory belongs to this run."""
    root = normalize_path(str(run_root))
    found: list[dict[str, object]] = []
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline = _read_text(os.path.join(proc_root, entry.name, "cmdline"), limit=4096)
        cmdline = cmdline.replace("\x00", " ").strip()
        if not cmdline:
            continue
        role = _role_of(cmdline)
        if role is None:
            continue
        try:
            cwd = normalize_path(os.readlink(os.path.join(proc_root, entry.name, "cwd")))
        except OSError:
            continue
        if not (cwd == root or cwd.startswith(root + "/")):
            continue
        found.append({
            "pid": pid,
            "role": role,
            "cwd": cwd,
            "cmdline": cmdline,
            "wchan": _read_text(os.path.join(proc_root, entry.name, "wchan")).strip(),
            "cpu_ticks": _read_proc_cpu_ticks(pid, proc_root),
        })
    return found


def sample_window(pids: list[int], seconds: float, proc_root: str = "/proc",
                  sleep_fn=time.sleep) -> dict[str, object]:
    """I/O and CPU movement of these processes over a real time window."""
    if seconds <= 0 or not pids:
        return {"seconds": 0.0, "sampled": False, "io_delta_bytes": None, "cpu_busy_fraction": None}
    before_io = {pid: read_proc_io(pid, proc_root) for pid in pids}
    before_cpu = {pid: _read_proc_cpu_ticks(pid, proc_root) for pid in pids}
    sleep_fn(seconds)
    after_io = {pid: read_proc_io(pid, proc_root) for pid in pids}
    after_cpu = {pid: _read_proc_cpu_ticks(pid, proc_root) for pid in pids}

    io_delta = 0
    io_seen = False
    for pid in pids:
        for key in ("rchar", "wchar"):
            old = before_io.get(pid, {}).get(key)
            new = after_io.get(pid, {}).get(key)
            if old is not None and new is not None:
                io_delta += max(new - old, 0)
                io_seen = True

    cpu_delta = 0
    cpu_seen = False
    for pid in pids:
        old = before_cpu.get(pid)
        new = after_cpu.get(pid)
        if old is not None and new is not None:
            cpu_delta += max(new - old, 0)
            cpu_seen = True
    try:
        ticks_per_second = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):  # pragma: no cover - platform dependent
        ticks_per_second = 100
    busy = cpu_delta / (ticks_per_second * seconds) if cpu_seen else None
    return {
        "seconds": seconds,
        "sampled": True,
        "io_delta_bytes": io_delta if io_seen else None,
        "cpu_ticks_delta": cpu_delta if cpu_seen else None,
        "cpu_busy_fraction": busy,
    }


# --------------------------------------------------------------------------- #
# Scheduler
# --------------------------------------------------------------------------- #


def gather_jobs(user: str | None) -> tuple[str, str | None]:
    if not shutil.which("squeue"):
        return "unavailable", None
    try:
        proc = subprocess.run(
            ["squeue", "-h", "-u", user or os.environ.get("USER", ""), "-o", "%T|%j|%Z"],
            text=True, capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - host dependent
        return f"error: {exc}", None
    if proc.returncode != 0:
        return f"error: {(proc.stderr or proc.stdout).strip()}", None
    return "squeue", proc.stdout


def attribute_jobs(text: str, run_root: Path) -> dict[str, object]:
    """Attribute jobs to this run by workdir; job names are site-defined and unreliable."""
    root = normalize_path(str(run_root))
    ours: dict[str, int] = {}
    other = 0
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 3 or not line.strip():
            continue
        state, workdir = parts[0].strip().upper(), parts[2].strip()
        normalised = normalize_path(workdir) if workdir else ""
        if normalised and (normalised == root or normalised.startswith(root + "/")):
            ours[state] = ours.get(state, 0) + 1
        else:
            other += 1
    active = sum(count for state, count in ours.items() if state not in TERMINAL_JOB_STATES)
    return {"total": sum(ours.values()), "by_state": dict(sorted(ours.items())),
            "active": active, "other_jobs_on_account": other}


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


def detect_finished(markers: dict[str, bool], results_dir: Path | None) -> bool:
    if markers.get("USPEX_IS_DONE"):
        return True
    if results_dir is None:
        return False
    text = _read_text(str(results_dir / "OUTPUT.txt"), limit=4 * 1024 * 1024)
    return "Job Finished at" in text


def decide(evidence: dict[str, object]) -> dict[str, object]:
    """Pure verdict function: evidence in, verdict + next actions out (unit-testable)."""
    reasons: list[str] = []
    warnings: list[str] = []
    age = evidence.get("activity_age_seconds")
    cadence = evidence.get("cadence_seconds")
    jobs_active = int(evidence.get("jobs_active") or 0)
    io_delta = evidence.get("io_delta_bytes")
    file_moved = evidence.get("file_moved")
    engine_count = int(evidence.get("engine_count") or 0)
    driver_count = int(evidence.get("driver_count") or 0)
    busy = evidence.get("cpu_busy_fraction")

    if evidence.get("finished"):
        return _verdict("done", EXIT_DONE,
                        ["USPEX_IS_DONE is present or OUTPUT.txt records the finish"],
                        warnings, ["Analyse the results and package what the user asked for."])

    if jobs_active > 0:
        reasons.append(f"{jobs_active} scheduler job(s) of this run are active or pending")
        return _verdict("working", EXIT_WORKING, reasons, warnings,
                        ["No action. Re-check when the queue empties."])

    if file_moved or (io_delta or 0) > 0:
        reasons.append(
            f"artefacts moved (file_moved={bool(file_moved)}) and engine I/O changed by "
            f"{io_delta} bytes during the {evidence.get('sample_seconds')} s window"
        )
        return _verdict("working", EXIT_WORKING, reasons, warnings, ["No action."])

    processes_checked = bool(evidence.get("engine_checked", True))
    if not processes_checked:
        warnings.append(
            "processes were not inspected (--no-proc), so 'nothing is alive' was not established"
        )
    if processes_checked and engine_count == 0 and driver_count == 0:
        reasons.append("no engine (uspex.x) and no driver (USPEX -r) process belongs to this run")
        return _verdict("stopped", EXIT_STOPPED, reasons, warnings, [
            "Nothing is advancing: this needs a restart, not a kill.",
            "Read the driver log for exit codes, then re-enter the loop (assets/run_uspex_loop.sh).",
            "A plain `USPEX -r` re-enters the completion path if USPEX_IS_DONE is still present.",
        ])

    if age is None:
        return _verdict("unknown", EXIT_UNKNOWN, ["no artefact of this run was found on disk"],
                        warnings, ["Point the tool at the directory that holds INPUT.txt."])

    reference = max(float(cadence) if cadence else DEFAULT_CADENCE_SECONDS, MIN_CADENCE_SECONDS)
    source = evidence.get("cadence_source") or "assumed default"
    if cadence is None:
        warnings.append(
            f"no cadence measurable from results*/generation* or CalcFoldTemp/Jobs.history; "
            f"using the conservative default of {DEFAULT_CADENCE_SECONDS / 3600:.1f} h per generation"
        )
    if busy is not None and busy < 0.02 and evidence.get("sampled"):
        warnings.append(
            f"the engine is alive but consumed {busy * 100:.1f}% of one core during the window "
            "(waiting rather than computing)"
        )
    if evidence.get("activity_future_dated"):
        warnings.append(
            "an artefact is dated in the future (clock or timezone skew between hosts): the "
            "silence measured here is a lower bound, so treat 'working' with more suspicion"
        )
    if engine_count == 0:
        warnings.append("no engine process is visible right now; the driver may be between invocations")
    if evidence.get("helpers_in_pipe_read"):
        reasons.append(
            f"{evidence['helpers_in_pipe_read']} matlab_helper process(es) sit in pipe_read: "
            "normal while the embedded MATLAB engine runs inside uspex.x"
        )

    quiet_note = (
        f"newest artefact {age / 3600:.2f} h old ({evidence.get('activity_path')}), "
        f"cadence {reference / 3600:.2f} h/generation from {source}"
    )
    if age <= reference:
        reasons.append(f"quiet for less than one generation cycle: {quiet_note}")
        return _verdict("working", EXIT_WORKING, reasons, warnings,
                        [f"No action. Re-check in about {reference / 7200:.1f} h (half a cycle)."])

    if age <= STUCK_CADENCE_FACTOR * reference:
        reasons.append(f"quiet past one generation cycle but below the dead-loop bound: {quiet_note}")
        warnings.append("corroborate with the queue and re-run this check before waiting longer")
        return _verdict("working", EXIT_WORKING, reasons, warnings,
                        ["Re-check now; if I/O is still frozen and the queue stays empty, treat it as stuck."])

    movement = ("no I/O movement" if evidence.get("sampled")
                else "no artefact movement (I/O was not sampled: --no-proc or --sample-seconds 0)")
    reasons.append(
        f"quiet for {age / reference:.1f} generation cycles with {movement}: {quiet_note}"
    )
    if busy is not None and busy > 0.5:
        reasons.append(
            f"the engine burned {busy * 100:.0f}% of one core during the window while touching "
            "no I/O: a user-space spin, not computation"
        )
    return _verdict("stuck", EXIT_STUCK, reasons, warnings, [
        "1. Preserve evidence: log/uspex-driver.log, nohup.out, Warnings, the helper's stderr, the",
        "   last CalcFold* directories. Do not restart or delete anything yet.",
        "2. Read them for the cause and TELL THE USER your interpretation, e.g. a MATLAB error such as",
        "   `MATLAB:badsubscript` in SoftModeMutation_*/EA_* (soft-mode mutation on a bad structure),",
        "   an engine<->matlab_helper deadlock, a driver that exited, or a full disk / OOM.",
        "3. Prove no writer is alive: no uspex.x, no USPEX -r, no matlab_helper of this run, no job.",
        "4. Then choose with the user (references/liveness.md): A continue in place",
        "   (`rm -f still_running still_reading`, one foreground USPEX -r, then the loop),",
        "   B pick-up restart (`USPEX --clean` + pickUpYN/pickUpGen/pickUpFolder from the last",
        "   complete generation), or C a fresh start.",
    ])


def _verdict(state: str, code: int, reasons: list[str], warnings: list[str],
             next_steps: list[str]) -> dict[str, object]:
    return {"state": state, "exit_code": code, "reasons": reasons, "warnings": warnings,
            "next_steps": next_steps}


# --------------------------------------------------------------------------- #
# Collection and rendering
# --------------------------------------------------------------------------- #


def collect(root: Path, *, now: float | None = None, sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
            proc_root: str = "/proc", inspect_processes: bool = True,
            cadence_override: float | None = None, jobs_text: str | None = None,
            jobs_source: str = "unavailable", sleep_fn=time.sleep) -> dict[str, object]:
    now = time.time() if now is None else now
    results_dir = pick_results_dir(root)
    markers = {name: (root / name).exists() for name in MARKER_FILES}
    cadence = resolve_cadence(root, cadence_override)

    processes: list[dict[str, object]] = []
    if inspect_processes:
        processes = discover_processes(root, proc_root)
    engines = [item for item in processes if item["role"] == "engine"]
    drivers = [item for item in processes if item["role"] == "driver"]
    helpers = [item for item in processes if item["role"] == "helper"]

    sampling = sample_window([int(item["pid"]) for item in processes], sample_seconds, proc_root,
                             sleep_fn=sleep_fn)
    activity = newest_activity(root, results_dir, now=now)
    if sampling.get("sampled"):
        # Recompute after the window: file movement during the sample is direct progress evidence.
        after = newest_activity(root, results_dir, now=time.time())
        if (after.get("mtime") or 0) > (activity.get("mtime") or 0):
            activity = {"path": after.get("path"), "mtime": after.get("mtime"),
                        "age_seconds": 0.0, "future_dated": bool(after.get("future_dated")),
                        "moved_during_sample": True}

    if jobs_text is None and jobs_source == "unavailable":
        jobs_source, jobs_text = gather_jobs(os.environ.get("USER"))
    jobs = attribute_jobs(jobs_text, root) if jobs_text else {"total": 0, "by_state": {}, "active": 0,
                                                              "other_jobs_on_account": 0}
    jobs["source"] = jobs_source

    evidence: dict[str, object] = {
        "finished": detect_finished(markers, results_dir),
        "activity_age_seconds": activity.get("age_seconds"),
        "activity_path": activity.get("path"),
        "activity_future_dated": bool(activity.get("future_dated")),
        "file_moved": bool(activity.get("moved_during_sample")),
        "cadence_seconds": cadence.get("median_seconds"),
        "cadence_source": cadence.get("source"),
        "jobs_active": jobs.get("active", 0),
        "engine_count": len(engines),
        "engine_checked": inspect_processes,
        "driver_count": len(drivers),
        "helper_count": len(helpers),
        "helpers_in_pipe_read": sum(1 for item in helpers if item.get("wchan") == "pipe_read"),
        "io_delta_bytes": sampling.get("io_delta_bytes"),
        "cpu_busy_fraction": sampling.get("cpu_busy_fraction"),
        "sample_seconds": sampling.get("seconds"),
        "sampled": sampling.get("sampled"),
    }
    decision = decide(evidence)

    missing: list[str] = []
    if results_dir is None:
        missing.append("results<N>/")
    if cadence.get("median_seconds") is None:
        missing.append("a measurable generation cadence")
    if not processes:
        missing.append("this run's processes (checked /proc only when run on the cluster)")

    return {
        "schema_version": 1,
        "run_dir": str(root),
        "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "results_dir": str(results_dir) if results_dir else None,
        "state": decision["state"],
        "exit_code": decision["exit_code"],
        "markers": markers,
        "cadence": cadence,
        "activity": activity,
        "processes": {"engine": engines, "driver": drivers, "helper": helpers},
        "scheduler": jobs,
        "sample": sampling,
        "evidence": evidence,
        "reasons": decision["reasons"],
        "warnings": decision["warnings"],
        "next_steps": decision["next_steps"],
        "evidence_missing": missing,
    }


def render_text(report: dict[str, object]) -> str:
    lines = [
        f"state       : {report['state']}",
        f"exit code   : {report['exit_code']}",
        f"run dir     : {report['run_dir']}",
        f"results dir : {report['results_dir'] or '-'}",
        f"markers     : " + ", ".join(f"{k}={v}" for k, v in (report["markers"] or {}).items()),  # type: ignore[union-attr]
    ]
    cadence = report["cadence"]
    if isinstance(cadence, dict):
        median = cadence.get("median_seconds")
        lines.append(
            "cadence     : "
            + (f"{median / 3600:.2f} h/generation (median of {len(cadence.get('intervals_seconds') or [])} "
               f"intervals, {cadence.get('source')})" if median else
               "not measurable (no results*/generation*, no Jobs.history)")
        )
    activity = report["activity"]
    if isinstance(activity, dict) and activity.get("age_seconds") is not None:
        lines.append(
            f"last artefact: {activity.get('path')} ({activity['age_seconds'] / 3600:.2f} h ago)"
        )
    sample = report["sample"]
    if isinstance(sample, dict):
        if sample.get("sampled"):
            busy = sample.get("cpu_busy_fraction")
            lines.append(
                f"sample       : {sample['seconds']:.0f} s -> engine I/O "
                f"{sample.get('io_delta_bytes')} bytes, CPU "
                + (f"{busy * 100:.0f}% of one core" if isinstance(busy, float) else "unknown")
            )
        else:
            lines.append("sample       : skipped")
    processes = report["processes"]
    if isinstance(processes, dict):
        for role in ("engine", "driver", "helper"):
            items = processes.get(role) or []
            summary = ", ".join(
                f"pid {item['pid']}"
                + (f" ({item['wchan']})" if item.get("wchan") else "")
                + (f" cwd={item['cwd']}" if role == "engine" else "")
                for item in items
            )
            lines.append(f"{role + 's':<13}: {summary or 'none'}")
    jobs = report["scheduler"]
    if isinstance(jobs, dict):
        lines.append(
            f"jobs        : {jobs.get('total')} attributed ({jobs.get('by_state')}), "
            f"{jobs.get('active')} active, {jobs.get('other_jobs_on_account')} other ({jobs.get('source')})"
        )
    for reason in report["reasons"] or []:
        lines.append(f"reason      : {reason}")
    for warning in report["warnings"] or []:
        lines.append(f"WARN        : {warning}")
    if report["evidence_missing"]:
        lines.append("missing     : " + ", ".join(report["evidence_missing"]))
    for step in report["next_steps"] or []:
        lines.append(f"next        : {step}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit codes: 0 done, 1 working, 2 stuck, 3 stopped, 4 unknown",
    )
    parser.add_argument("run_dir", type=Path, help="USPEX calculation root (the directory holding INPUT.txt)")
    parser.add_argument("--sample-seconds", type=float, default=DEFAULT_SAMPLE_SECONDS,
                        help=f"window for I/O and CPU sampling (default {DEFAULT_SAMPLE_SECONDS:.0f}; 0 skips it)")
    parser.add_argument("--cadence-minutes", type=float, default=None,
                        help="override the measured generation cadence")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("--no-proc", action="store_true", help="do not inspect processes or sample I/O")
    parser.add_argument("--no-scheduler", action="store_true", help="do not call squeue")
    parser.add_argument("--jobs-output", type=Path, help="read a captured 'squeue -h -u USER -o %%T|%%j|%%Z' dump")
    parser.add_argument("--proc-root", default="/proc", help=argparse.SUPPRESS)
    parser.add_argument("--now", type=float, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    root = args.run_dir
    if not root.is_dir():
        print(f"FAIL: not a directory: {root}", file=sys.stderr)
        return EXIT_UNKNOWN

    jobs_text = None
    jobs_source = "disabled"
    if args.jobs_output:
        jobs_text = args.jobs_output.read_text(encoding="utf-8", errors="replace")
        jobs_source = f"file:{args.jobs_output}"
    elif not args.no_scheduler:
        jobs_source, jobs_text = gather_jobs(os.environ.get("USER"))

    report = collect(
        root,
        now=args.now,
        sample_seconds=0.0 if args.no_proc else args.sample_seconds,
        proc_root=args.proc_root,
        inspect_processes=not args.no_proc,
        cadence_override=args.cadence_minutes * 60 if args.cadence_minutes else None,
        jobs_text=jobs_text,
        jobs_source=jobs_source,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
