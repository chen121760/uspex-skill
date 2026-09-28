#!/usr/bin/env python3
"""Scheduler discovery for USPEX runs: which queues/partitions exist and which
ones this account may actually use.

Read-only and non-interactive by design. It never submits a job except when you
pass --test-submit, which uses Slurm's ``sbatch --test-only`` (a dry run, no
allocation is created).

Usage:
    python scripts/check_schedulers.py                 # human-readable report
    python scripts/check_schedulers.py --json          # machine-readable
    python scripts/check_schedulers.py --engine-command "mpirun -np 8 vasp_std"
    python scripts/check_schedulers.py --test-submit --nodes 1 --ntasks 8 --walltime 01:00:00
    python scripts/check_schedulers.py --workdir RUN_DIR   # read Submission/ requests

Exit codes: 0 = a usable scheduler was found, 2 = none found / no evidence,
3 = a scheduler exists but no queue could be confirmed.
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

TIMEOUT_SECONDS = 15

# Slurm partition states that mean "not accepting jobs from anyone".
SLURM_DEAD_STATES = {"DOWN", "DRAIN", "DRAINING", "FAIL", "FAILING", "INVAL", "UNKNOWN"}
# PBS queue states/attributes that mean "not accepting jobs".
PBS_DISABLED_ATTRS = {"disabled", "down", "offline", "unknown"}
# PBS queue states that are not accepting work: D = disabled, S = suspended/down.
# Note that "E" means *enabled* in qstat output and is a healthy state, so the check
# must be against these specific letters rather than "not one of the good letters".
PBS_BAD_QUEUE_STATES = {"D", "S"}


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.error

    @property
    def message(self) -> str:
        return (self.stderr + self.stdout).strip()


@dataclass
class Queue:
    name: str
    state: str = ""
    available: bool = False
    reason: str = ""
    limits: str = ""
    source: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Scheduler:
    family: str  # slurm | pbs | torque | ospbs | unknown
    commands: dict[str, str] = field(default_factory=dict)
    version: str = ""
    queues: list[Queue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Probe:
    """Runs scheduler commands with a timeout and never raises."""

    def __init__(self, workdir: Path | None = None) -> None:
        self.workdir = workdir
        self.cache: dict[tuple[str, ...], CommandResult] = {}

    def run(self, *argv: str) -> CommandResult:
        key = tuple(argv)
        if key in self.cache:
            return self.cache[key]
        try:
            proc = subprocess.run(
                list(argv),
                text=True,
                capture_output=True,
                check=False,
                timeout=TIMEOUT_SECONDS,
                cwd=str(self.workdir) if self.workdir else None,
            )
            result = CommandResult(list(argv), proc.returncode, proc.stdout, proc.stderr)
        except FileNotFoundError:
            result = CommandResult(list(argv), 127, "", "", "command not found")
        except subprocess.TimeoutExpired:
            result = CommandResult(
                list(argv), 124, "", "", f"timed out after {TIMEOUT_SECONDS}s"
            )
        except OSError as exc:  # permissions, exec format, ...
            result = CommandResult(list(argv), 126, "", "", f"could not execute: {exc}")
        self.cache[key] = result
        return result


def _which(names: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in names:
        path = shutil.which(name)
        if path:
            found[name] = path
    return found


def _detect_kind(commands: dict[str, str], probe: Probe | None = None) -> str:
    """Distinguish Slurm from the PBS family from the commands that exist.

    ``qsub`` alone is ambiguous: PBS Pro, Torque, OpenPBS and the Chinese
    academic clusters that run PBS-derived controllers all provide it.
    """
    if "sbatch" in commands:
        return "slurm"  # partial installs are reported by probe_slurm
    if "qsub" in commands or "qstat" in commands:
        probe = probe or Probe()
        version = probe.run("qstat", "--version")
        text = (version.stdout + version.stderr).lower()
        if "pbs pro" in text or re.search(r"\bpbspro\b", text):
            return "pbspro"
        if "torque" in text:
            return "torque"
        if "openpbs" in text:
            return "openpbs"
        return "pbs"
    return "unknown"


# --------------------------------------------------------------------------- Slurm


def _normalise_time_limit(value: str) -> str:
    """Report an unlimited time limit readably.

    ``sinfo %l`` prints the literal string ``infinite`` on at least one real Slurm
    (22.05.9); map it to the conventional UNLIMITED so the report is unambiguous.
    """
    return "UNLIMITED" if value.strip().lower() in {"infinite", "unlimited"} else value


def _parse_sinfo_partitions(text: str) -> list[Queue]:
    """Parse ``sinfo -h -o '%P|%a|%l|%D|%c|%m|%G|%f'``."""
    queues: list[Queue] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split("|")
        if len(fields) < 2:
            continue
        name = fields[0].strip().rstrip("*")
        avail_raw = fields[1].strip().lower() if len(fields) > 1 else ""
        limits = []
        labels = ["walltime", "nodes", "cpus", "mem", "gres", "features"]
        for label, value in zip(labels, fields[2:]):
            value = value.strip()
            if value and value not in {"(null)", "N/A", "n/a"}:
                if label == "walltime":
                    value = _normalise_time_limit(value)
                limits.append(f"{label}={value}")
        reason = ""
        available = avail_raw in {"up", "yes", "1"}
        if not available and avail_raw:
            reason = f"partition state is {avail_raw}"
        elif not available:
            reason = "partition availability not reported"
        queues.append(
            Queue(
                name=name,
                state=avail_raw or "unknown",
                available=available,
                reason=reason,
                limits=" ".join(limits),
                source="sinfo",
            )
        )
    return queues


def _parse_sinfo_long(text: str) -> list[Queue]:
    """Parse ``sinfo -h -N -o '%P|%a|%t|%D|%N'`` to catch DOWN/DRAIN nodes."""
    seen: dict[str, Queue] = {}
    for line in text.splitlines():
        fields = line.strip().split("|")
        if len(fields) < 3:
            continue
        name = fields[0].strip().rstrip("*")
        avail = fields[1].strip().lower()
        node_state = fields[2].strip().lower()
        bad = any(state in node_state for state in ("down", "drain", "fail", "inval"))
        entry = seen.setdefault(
            name,
            Queue(
                name=name,
                state=avail,
                available=avail in {"up", "yes", "1"},
                reason="" if avail in {"up", "yes", "1"} else f"partition state is {avail}",
                source="sinfo -N",
            ),
        )
        if bad:
            entry.available = False
            entry.reason = f"nodes are {node_state}" if not entry.reason else entry.reason
    return list(seen.values())


def _parse_scontrol_partition(text: str) -> dict[str, dict[str, str]]:
    """Parse ``scontrol show partition`` blocks into ``{name: {key: value}}``."""
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current.get("PartitionName"):
                blocks[current["PartitionName"]] = current
            current = {}
            continue
        for token in line.split():
            if "=" not in token:
                continue
            key, _, value = token.partition("=")
            current[key] = value
    if current.get("PartitionName"):
        blocks[current["PartitionName"]] = current
    return blocks


def _merge_queues(*groups: list[Queue]) -> list[Queue]:
    merged: dict[str, Queue] = {}
    for group in groups:
        for queue in group:
            existing = merged.get(queue.name)
            if existing is None:
                merged[queue.name] = queue
                continue
            existing.available = existing.available and queue.available
            existing.reason = "; ".join(part for part in (existing.reason, queue.reason) if part)
            existing.limits = " ".join(part for part in (existing.limits, queue.limits) if part)
            existing.source = ", ".join(
                dict.fromkeys((existing.source, queue.source))
            )
            existing.state = existing.state or queue.state
    return sorted(merged.values(), key=lambda item: item.name)


def probe_slurm(commands: dict[str, str], probe: Probe) -> Scheduler:
    scheduler = Scheduler(family="slurm", commands=commands)

    version = probe.run("sinfo", "--version")
    if version.ok:
        scheduler.version = version.stdout.strip()

    for required in ("sbatch", "sinfo", "squeue"):
        if required not in commands:
            scheduler.errors.append(
                f"{required} is not on PATH; a Slurm client may be incomplete or this is a login node without the client"
            )

    queues: list[Queue] = []
    if "sinfo" in commands:
        listing = probe.run("sinfo", "-h", "-o", "%P|%a|%l|%D|%c|%m|%G|%f")
        if listing.ok and listing.stdout.strip():
            queues = _parse_sinfo_partitions(listing.stdout)
        elif not listing.ok:
            scheduler.errors.append(f"sinfo failed: {listing.error or listing.message}")
        else:
            scheduler.errors.append("sinfo returned no partitions")

        long_listing = probe.run("sinfo", "-h", "-N", "-o", "%P|%a|%t|%D|%N")
        if long_listing.ok and long_listing.stdout.strip():
            queues = _merge_queues(queues, _parse_sinfo_long(long_listing.stdout))

    if "scontrol" in commands:
        detail = probe.run("scontrol", "show", "partition")
        if detail.ok and detail.stdout.strip():
            for name, values in _parse_scontrol_partition(detail.stdout).items():
                state = values.get("State", "").upper()
                for queue in queues:
                    if queue.name != name:
                        continue
                    bits = [
                        f"MaxTime={values['MaxTime']}" if values.get("MaxTime") else "",
                        f"MaxNodes={values['MaxNodes']}" if values.get("MaxNodes") else "",
                        f"TRES={values['TRES']}" if values.get("TRES") else "",
                        f"AllowAccounts={values['AllowAccounts']}"
                        if values.get("AllowAccounts")
                        else "",
                    ]
                    extra = " ".join(part for part in bits if part)
                    if extra:
                        queue.limits = " ".join(part for part in (queue.limits, extra) if part)
                    if "AllowGroups" in values:
                        scheduler.notes.append(
                            f"partition {name}: AllowGroups={values['AllowGroups']}"
                        )
                    if state in SLURM_DEAD_STATES:
                        queue.available = False
                        queue.reason = f"scontrol State={state}"
                    break

    scheduler.queues = queues
    scheduler.notes.append(
        "sinfo lists every partition, not only those your account may submit to; "
        "the authoritative check is a dry run (--test-submit) or scontrol show partition AllowAccounts/AllowGroups"
    )
    return scheduler


# ----------------------------------------------------------------------------- PBS


def _parse_qstat_queues(text: str) -> list[Queue]:
    """Parse ``qstat -Q`` (any column layout) into queues.

    Only the queue-name column is positional; the remaining tokens are kept as
    limits, and the PBS state is recognised wherever it appears. A state token is
    a single state character or a known attribute word, so a queue called
    ``workq`` is never mistaken for a state just because it starts with ``w``.
    """
    queues: list[Queue] = []
    # ``qstat -Q`` exists in two real layouts:
    #   Torque/OpenPBS:  Ena  Str  Que  Run ... Ext State   <- state is the LAST column
    #   PBS-derived:     Ena  Str  Que  Run ... Ext T   Cpt <- last columns are type/completed
    # The availability truth in both is the Ena/Str pair, so parse the header to find
    # those columns instead of trusting the final token.
    labels: list[str] = []
    saw_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("queue"):
            labels = line.split()
            saw_header = True
            continue
        if set(line) <= {"-", "=", "+", " "}:
            continue
        if len(line.split()) == 2 and line.split()[0].isdigit():
            continue  # trailing totals row
        tokens = line.split()
        if not tokens:
            continue
        name = tokens[0]

        def column(label: str) -> str:
            if label not in labels:
                return ""
            index = labels.index(label)
            return tokens[index] if index < len(tokens) else ""

        enabled = column("Ena").lower()
        started = column("Str").lower()
        available = True
        reason = ""
        if enabled in {"no", "false", "f", "0"}:
            available = False
            reason = "queue Ena=no (disabled)"
        if started in {"no", "false", "f", "0"}:
            available = False
            reason = (reason + "; " if reason else "") + "queue Str=no (not started)"

        state_token = ""
        header_state_index = labels.index("State") if "State" in labels else None
        if header_state_index is not None and header_state_index < len(tokens):
            state_token = tokens[header_state_index]
        elif saw_header and header_state_index is not None:
            # Short/sparse row: fall back to the final column, which is the state in
            # this family of outputs.
            state_token = tokens[-1]
        elif saw_header:
            # Header present but no "State" column: the trailing column is
            # site-specific and often a counter (the GridView dispatcher puts "Cpt"
            # there). Prefer a real state token, and never report a digit as a state.
            for token in tokens[1:]:
                if token.upper() in {"D", "S"} or token.lower() in PBS_DISABLED_ATTRS:
                    state_token = token
                    break
            if not state_token:
                for token in tokens[1:]:
                    if token.upper() in {"E", "R"}:
                        state_token = token
                        break
        else:
            # Headerless legacy output (the last column is the state).
            state_token = tokens[-1]
        if not available and not state_token:
            state_token = "unknown"
        if available and state_token.upper() in PBS_BAD_QUEUE_STATES:
            available = False
            reason = f"PBS queue state is {state_token}"

        limits = " ".join(
            f"{label}={value}"
            for label in labels
            if label not in {"Queue", "Ena", "Str", "State"}
            and (value := column(label)) not in {"", "-", "--", "0"}
        )
        queues.append(
            Queue(
                name=name,
                state=state_token or "unknown",
                available=available,
                reason=reason,
                limits=limits,
                source="qstat -Q",
            )
        )
    return queues


def _indent(text: str) -> int:
    return len(text) - len(text.lstrip(" \t"))


def _join_wrapped_lines(text: str) -> list[str]:
    """Rejoin PBS attribute lines that the server wrapped at a fixed column.

    Ordinary attribute lines are indented; a wrapped *continuation* is indented
    further, so the rule is "deeper indent than the previous line", not "any
    indent". Continuations are appended without a separator because ``qstat`` also
    wraps mid-token (real example: ``Comp`` + ``lete:0``).
    """
    logical: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if logical and "=" in logical[-1] and _indent(raw) > _indent(logical[-1]):
            logical[-1] = logical[-1].rstrip() + raw.strip()
        else:
            logical.append(raw.rstrip())
    return logical


def _current_user() -> str:
    """Account name used for ACL checks; empty when it cannot be established."""
    for name in ("USER", "LOGNAME"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        return ""


# ------------------------------------------------------- cluster facts (guide)

DEFAULT_FACTS_PATH = Path.home() / ".cluster-agents.md"

# The canonical guide template is owned by the `hpc-submit` skill
# (references/cluster-guide-template.md). Keep the parser tolerant: bullets, optional
# `**bold**`, and optional values are all accepted.
_FACTS_FIELDS = {
    "scheduler": "scheduler",
    "default queue/partition": "default_queue",
    "submit command": "submit_command",
    "queue/status command": "status_command",
    "cancel command": "cancel_command",
    "accounting/history command": "accounting_command",
    "max unattended jobs": "max_unattended_jobs",
}


def _unquote(value: str) -> str:
    """Strip the markdown decorations a guide author may wrap a value in."""
    return re.sub(r"^[`*_]+|[`*_]+$", "", value.strip()).strip()


def _facts_field_key(label: str) -> str | None:
    cleaned = re.sub(r"[*`_]", "", label).strip().rstrip(":").lower()
    if cleaned in _FACTS_FIELDS:
        return _FACTS_FIELDS[cleaned]
    for name, key in _FACTS_FIELDS.items():
        if cleaned.startswith(name):
            return key
    return None


def read_cluster_facts(path: Path) -> dict[str, object]:
    """Parse the site facts from a cluster guide such as ``~/.cluster-agents.md``.

    The guide is the cluster's own operating manual (the convention used by the
    `hpc-submit` skill). Reading it is the cheapest source of queue/partition,
    account, and job-script facts, and it is what stops the same question being
    asked twice.
    """
    facts: dict[str, object] = {"path": str(path), "exists": path.is_file(), "queues": []}
    if not path.is_file():
        return facts
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        facts["error"] = f"could not read {path}: {exc}"
        return facts
    facts["bytes"] = len(text.encode("utf-8"))
    facts["sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    section = ""
    # "Available queues/partitions" records its own multi-line structure: the entry
    # itself lives on indented sub-bullets, so capture the whole block until the next
    # top-level bullet.
    in_queues = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            section = re.sub(r"^#+\s*", "", stripped).strip().lower()
            in_queues = False
            continue
        if section != "scheduler":
            continue
        top_item = re.match(r"^[-*]\s+(.*)$", raw)
        if top_item:
            body = top_item.group(1)
            label, _, value = body.partition(":")
            if re.sub(r"[*`_]", "", label).strip().lower() == "available queues/partitions":
                in_queues = True
                if value.strip():
                    facts["queues"].append(value.strip())
                continue
            in_queues = False
            if ":" not in body:
                continue
            key = _facts_field_key(label)
            value = _unquote(value)
            if key and value:
                facts[key] = value
            continue
        if in_queues and stripped:
            sub = _unquote(re.sub(r"^[-*]\s+", "", stripped))
            if sub:
                facts["queues"].append(sub)
    return facts


def facts_advice(facts: dict[str, object]) -> dict[str, object]:
    """Turn recorded site facts into the queue the run should use.

    A recorded fact wins over guessing, but it never replaces verification: the
    queue still has to be confirmed against the live scheduler.
    """
    advice: dict[str, object] = dict(facts)
    queue = _unquote(str(facts.get("default_queue") or ""))
    advice["resolved_queue"] = queue
    if not facts.get("exists"):
        advice["verdict"] = "no-guide"
        advice["action"] = (
            f"no cluster guide at {facts.get('path')}: probe the scheduler, then offer to create "
            "the guide so the next session does not repeat the discovery"
        )
    elif queue:
        advice["verdict"] = "recorded"
        advice["action"] = (
            f"the guide records queue/partition '{queue}'; confirm it on the live scheduler "
            "(this script's queue list plus --test-submit) before writing it into Submission/"
        )
    else:
        advice["verdict"] = "guide-incomplete"
        advice["action"] = (
            f"{facts.get('path')} exists but records no default queue/partition: ask the user "
            "for it, verify it here, then record it in the guide"
        )
    return advice


def render_facts_block(
    scheduler_family: str,
    queues: list[Queue],
    version: str,
    detected_user: str,
) -> str:
    """Render a ``## Scheduler`` section filled only with observed facts.

    Nothing is invented: a field with no observed value stays blank so the next
    reader knows to ask. Append or merge this into the cluster guide.
    """
    lines = ["## Scheduler", ""]
    lines.append(f"- Scheduler: `{scheduler_family or 'unknown'}`")
    # Only a partition observed as available is proposed; a queue whose access was
    # not confirmed stays blank so the next reader asks instead of assuming.
    available = [queue.name for queue in queues if queue.available]
    default = available[0] if available else ""
    if default:
        lines.append(f"- Default queue/partition: `{default}`")
    else:
        lines.append("- Default queue/partition:")
    lines.append("- Available queues/partitions:")
    if queues:
        for queue in queues:
            limits = f" ({queue.limits})" if queue.limits else ""
            state = "available" if queue.available else f"NOT available - {queue.reason}"
            lines.append(f"  - {queue.name}: {state}{limits}")
    else:
        lines.append("  -")
    if scheduler_family == "slurm":
        lines.append("- Submit command: `sbatch myrun`")
        lines.append("- Queue/status command: `squeue -u $USER`")
        lines.append("- Cancel command: `scancel <id>`")
        lines.append(
            "- Accounting/history command: `sacct -j <id> --format=JobID,State,Elapsed,ExitCode`"
        )
    else:
        lines.append("- Submit command: `qsub myrun`")
        lines.append("- Queue/status command: `qstat -u $USER`")
        lines.append("- Cancel command: `qdel <id>`")
        lines.append("- Accounting/history command: `tracejob <id>`")
    lines.append("- Max unattended jobs:")
    lines.append("")
    lines.append("<!-- Filled by scripts/check_schedulers.py --facts-out. Verify every line;")
    lines.append(f"     blank fields mean 'ask the user'. Scheduler client: {version or 'unknown'};")
    lines.append(f"     detected account: {detected_user or 'unknown'}. -->")
    return "\n".join(lines) + "\n"


def _print_facts_report(advice: dict[str, object]) -> None:
    print("=== Cluster guide (site facts) ===")
    path = advice.get("path")
    if not advice.get("exists"):
        print(f"  {path}: not present")
    else:
        print(f"  {path}: {advice.get('bytes', 0)} bytes, sha256 {str(advice.get('sha256'))[:12]}...")
        for key in (
            "scheduler",
            "default_queue",
            "submit_command",
            "status_command",
            "cancel_command",
            "accounting_command",
            "max_unattended_jobs",
        ):
            if advice.get(key):
                print(f"    {key}: {advice[key]}")
        for queue in advice.get("queues") or []:
            print(f"    queue entry: {queue}")
    print(f"  verdict: {advice.get('verdict')}")
    if advice.get("action"):
        print(f"  next: {advice['action']}")
    print()


def _acl_allows_user(values: dict[str, str], user: str) -> bool | None:
    """Decide whether ``acl_users`` admits ``user``; ``None`` means it was not declared.

    Entries look like ``alice@*`` (host pattern after ``@``). A bare ``*`` entry
    admits everyone.
    """
    raw = values.get("acl_users", "").strip()
    if not raw:
        return None
    if values.get("acl_user_enable", "").lower() in {"false", "no", "0"}:
        return True
    entries = [item.strip() for item in re.split(r"[,\s]+", raw) if item.strip()]
    if not entries:
        return None
    for entry in entries:
        name, _, _host = entry.partition("@")
        if name in {"*", user}:
            return True
    return False


def _parse_qstat_q(text: str) -> dict[str, dict[str, str]]:
    """Parse ``qstat -q`` / ``qstat -Qf <queue>`` blocks into ``{name: {key: value}}``.

    Torque writes ``Queue: workq`` and then ``key = value`` lines; PBS Pro and the
    GridView dispatcher write the same header. Attribute names other than ``queue``
    are not queue names, so only the header (or a leading ``queue = name``) starts a
    new block.
    """
    blocks: dict[str, dict[str, str]] = {}
    current_name = ""
    current: dict[str, str] = {}
    for line in _join_wrapped_lines(text):
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("---")
            or stripped.lower().startswith("server:")
        ):
            continue
        header = re.match(r"^[Qq]ueue:\s*(\S+)\s*$", stripped)
        assigned = re.match(r"^queue\s*=\s*(\S+)\s*$", stripped, re.IGNORECASE)
        if header or assigned:
            if current_name:
                blocks[current_name] = current
            current_name = (header or assigned).group(1)
            current = {"queue": current_name}
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*)$", stripped)
        if not match or not current_name:
            continue
        key, value = match.group(1), match.group(2).strip()
        lowered = key.lower()
        if lowered in {
            "enabled",
            "started",
            "acl_users",
            "acl_user_enable",
            "acl_hosts",
            "acl_host_enable",
            "acl_group",
            "acl_user",
        }:
            current[lowered] = value
        elif lowered.startswith(("resources_max", "resources_default", "resources_min")):
            # Torque/PBS Pro write dotted sub-keys (resources_max.walltime); keep the
            # limit visible under the exact key the server reported.
            current.setdefault(lowered, value)
        elif lowered in {
            "max_running",
            "max_user_run",
            "max_user_queuable",
            "state_count",
            "total_jobs",
            "queued_jobs",
        }:
            current[lowered] = value
    if current_name:
        blocks[current_name] = current
    return blocks


def _pbsnodos_availability(
    probe: Probe,
) -> tuple[dict[str, str], str, dict[str, int], int]:
    """Summarise ``pbsnodes`` output.

    Returns ``({queue: state}, source, {node_state: count}, nodes_seen)``. Some PBS
    variants (the GridView dispatcher among them) declare no node-to-queue mapping,
    so the queue map stays empty while the node-state counts carry the health
    information; callers must not read an empty map as "all nodes healthy".
    """
    states: dict[str, str] = {}
    node_states: dict[str, int] = {}
    nodes_seen = 0
    for command in (("pbsnodes", "-a"), ("pbsnodes",)):
        result = probe.run(*command)
        if not result.ok or not result.stdout.strip():
            continue
        for block in re.split(r"\n\s*\n", result.stdout):
            first_line = block.split("\n")[0] if block.split("\n") else ""
            # A node header is a single bare token (real names include dots, as in
            # "node01.cluster"); lines carrying " = " are attributes, not headers.
            name_match = re.match(r"^\s*([^\s=]+)\s*$", first_line)
            # Only a line whose whole value is the state counts: the GridView
            # dispatcher also emits "status = ...,state=free,netload=..." which must
            # not be read as the node state.
            state_match = re.search(
                r"^\s*state\s*=\s*([^,\s]+)\s*$", block, re.MULTILINE
            )
            if not name_match or not state_match:
                continue
            nodes_seen += 1
            state = state_match.group(1)
            node_states[state] = node_states.get(state, 0) + 1
            queue_match = re.search(r"^\s*queue\s*=\s*([^,\s]+)\s*$", block, re.MULTILINE)
            queues = (queue_match.group(1).split() if queue_match else []) or ["(unassigned)"]
            for queue in queues:
                states[queue] = state
        return states, "pbsnodes", node_states, nodes_seen
    return states, "", node_states, nodes_seen


def probe_pbs(commands: dict[str, str], probe: Probe) -> Scheduler:
    family = _detect_kind(commands, probe)
    scheduler = Scheduler(family=family, commands=commands)

    for required in ("qsub", "qstat"):
        if required not in commands:
            scheduler.errors.append(
                f"{required} is not on PATH; the PBS client may be incomplete on this node"
            )

    version = probe.run("qstat", "--version")
    if version.returncode == 0:
        scheduler.version = version.message.splitlines()[0] if version.message else ""
    else:
        banner = probe.run("qstat", "-B")
        if banner.returncode == 0:
            scheduler.version = banner.message

    queues: list[Queue] = []
    if "qstat" in commands:
        listing = probe.run("qstat", "-Q")
        if listing.ok and listing.stdout.strip():
            queues = _parse_qstat_queues(listing.stdout)
        elif not listing.ok:
            scheduler.errors.append(
                f"qstat -Q failed: {listing.error or listing.message} - on OpenPBS/PBS Pro a restricted "
                "server may report non-queue lines or refuse the query"
            )

        detail = probe.run("qstat", "-q")
        if detail.ok and detail.stdout.strip():
            blocks = _parse_qstat_q(detail.stdout)
            known = {queue.name for queue in queues}
            for name in sorted(blocks):
                if name not in known:
                    queues.append(Queue(name=name, source="qstat -q"))
            for queue in queues:
                values = blocks.get(queue.name, {})
                if not values:
                    continue
                bits = [
                    f"{key}={values[key]}"
                    for key in sorted(values)
                    if key.startswith(("resources_max", "resources_default", "resources_min"))
                ]
                if values.get("max_user_queuable"):
                    bits.append(f"max_user_queuable={values['max_user_queuable']}")
                extra = " ".join(part for part in bits if part)
                if extra:
                    queue.limits = " ".join(part for part in (queue.limits, extra) if part)
                enabled = values.get("enabled", "").lower()
                started = values.get("started", "").lower()
                acl = values.get("acl_user") or values.get("acl_group") or values.get("acl_hosts")
                if enabled in {"false", "f", "no", "0"}:
                    queue.available = False
                    queue.reason = "queue enabled=false"
                if started in {"false", "f", "no", "0"}:
                    queue.available = False
                    queue.reason = "queue started=false"
                if acl and acl.lower() not in {"", "none"}:
                    scheduler.notes.append(
                        f"queue {queue.name}: access restricted (acl={acl}); confirm your account is listed"
                    )

        # Some servers (the GridView dispatcher among them) show neither limits nor
        # ACLs in `qstat -q`; `qstat -Qf <queue>` is where the account whitelist lives.
        # Resolve it against the account running this script instead of telling the
        # user to check by hand.
        current_user = _current_user()
        if current_user and "qstat" in commands:
            for queue in queues:
                if not queue.available:
                    continue
                full = probe.run("qstat", "-Qf", queue.name)
                if not full.ok or not full.stdout.strip():
                    continue
                values = _parse_qstat_q(full.stdout).get(queue.name, {})
                if not values:
                    continue
                verdict = _acl_allows_user(values, current_user)
                if verdict is True:
                    scheduler.notes.append(
                        f"queue {queue.name}: acl_users admits {current_user}"
                    )
                elif verdict is False:
                    queue.available = False
                    queue.reason = (
                        f"acl_users does not list {current_user} (qstat -Qf {queue.name})"
                    )
                default_walltime = values.get("resources_default.walltime", "")
                max_user_run = values.get("max_user_run", "")
                if default_walltime:
                    queue.limits = " ".join(
                        part
                        for part in (queue.limits, f"default_walltime={default_walltime}")
                        if part
                    )
                if max_user_run and max_user_run != "0":
                    queue.limits = " ".join(
                        part for part in (queue.limits, f"max_user_run={max_user_run}") if part
                    )

    node_states, node_source, node_state_counts, nodes_seen = _pbsnodos_availability(probe)
    if node_state_counts:
        summary = ", ".join(
            f"{state}={count}" for state, count in sorted(node_state_counts.items())
        )
        assigned = {name for name in node_states if name != "(unassigned)"}
        if assigned:
            for queue in queues:
                state = node_states.get(queue.name, "")
                if state and any(
                    bad in state.lower()
                    for bad in ("down", "offline", "unknown", "state-unknown")
                ):
                    queue.available = False
                    queue.reason = f"{node_source} reports nodes state={state}"
            unknown_queues = sorted(assigned - {queue.name for queue in queues})
            if unknown_queues:
                scheduler.notes.append(
                    "pbsnodes reports queues that qstat -Q did not list: "
                    + ", ".join(unknown_queues)
                )
        scheduler.notes.append(f"{node_source} node states: {summary} ({nodes_seen} nodes)")
        if "(unassigned)" in node_states:
            scheduler.notes.append(
                "this server declares no node-to-queue mapping in pbsnodes (no 'queue =' "
                "attribute), so per-queue node health cannot be derived from it; queue "
                "availability above comes from qstat (Ena/Str), and node states are reported "
                "only as a cluster-wide summary"
            )
    elif "pbsnodes" in commands:
        scheduler.notes.append(
            "pbsnodes returned no usable node list; queue availability is based on qstat "
            "attributes only"
        )
    else:
        scheduler.notes.append(
            "pbsnodes is not on PATH; per-queue node health could not be checked, so "
            "availability is based on qstat attributes (Ena/Str/state) alone"
        )

    if any(not queue.limits for queue in queues):
        scheduler.notes.append(
            "qstat -Q on this server reports no per-queue limits; read 'qstat -q' "
            "resources_max/default (some PBS-derived servers leave them empty there too)"
        )

    scheduler.queues = queues
    scheduler.notes.append(
        "PBS-family clusters differ (Torque/OpenPBS/PBS Pro, and PBS-derived controllers on "
        "Chinese academic clusters): confirm the actual server with 'qstat -B' before trusting "
        "any queue name, and never copy a queue name between sites"
    )
    return scheduler


# ------------------------------------------------------------------- Submission/

SKILL_ROOT = Path(__file__).resolve().parent.parent

# Directories that never contain a hand-written Submission pair worth copying.
SKIP_DIR_NAMES = {
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "site-packages",
    "AppData",
    "Library",
    ".cache",
    ".conda",
    ".npm",
    ".local",
    "Downloads",
    "OneDrive",
}


def _iter_submission_pairs(
    roots: Iterable[Path], max_depth: int = 4, max_dirs_per_root: int = 4000
):
    """Yield ``(submission_dir, submit_file)`` for local submit callbacks.

    Searching existing ``Submission/`` directories is the cheapest way to recover a
    working site configuration (queue name, module preamble, resource request) when
    a run directory has none. Each root gets its own directory budget: a large home
    directory must not consume the budget that a later, smaller root needs.
    """
    seen: set[Path] = set()
    for root in roots:
        root = root.expanduser()
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        visited = 0
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            visited += 1
            if visited > max_dirs_per_root:
                break
            current_path = Path(current)
            # Prune bulk run products and caches; they never hold a Submission pair.
            dirnames[:] = [
                name
                for name in dirnames
                if name not in SKIP_DIR_NAMES
                and not name.startswith(".")
                and not name.startswith(("results", "CalcFold", "generation"))
            ]
            if len(current_path.parts) - base_depth >= max_depth:
                dirnames[:] = []
            for name in ("submitJob_local.py", "submitJob_remote.py"):
                if name not in filenames:
                    continue
                candidate = current_path / name
                if candidate in seen:
                    continue
                seen.add(candidate)
                yield current_path, candidate



def _submission_state(workdir: Path) -> tuple[str, int]:
    """Return ``(state, whichCluster)`` for a run directory's Submission/ folder.

    ``state`` is ``missing``, ``empty``, ``pair_present`` or ``template``.
    """
    which = None
    input_path = workdir / "INPUT.txt"
    if input_path.is_file():
        match = re.search(
            r"^\s*([+-]?\d+)\s*:\s*whichCluster",
            input_path.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if match:
            which = int(match.group(1))
    suffix = "remote" if which == 2 else "local"
    submission = workdir / "Submission"
    if not submission.is_dir():
        return "missing", which if which is not None else -1
    if not any(submission.glob("*.py")):
        return "empty", which if which is not None else -1
    if not (submission / f"submitJob_{suffix}.py").is_file():
        return "pair_incomplete", which if which is not None else -1
    text = (submission / f"submitJob_{suffix}.py").read_text(
        encoding="utf-8", errors="replace"
    )
    if re.search(r"<(?:PARTITION|QUEUE|LOAD_MODULES_AND_ACTIVATE_ENV)>", text):
        return "template", which if which is not None else -1
    return "pair_present", which if which is not None else -1


def _discover_submissions(
    workdir: Path | None, roots: Iterable[Path], limit: int = 5
) -> list[dict[str, str]]:
    """Find usable submit callbacks on this machine, best candidates first."""
    bases: list[Path] = []
    # User-supplied / default roots first: they are the intended search area.
    for root in roots:
        bases.append(root)
    if workdir is not None:
        bases.append(workdir.parent)  # sibling runs of the same project
        bases.append(workdir.parent.parent)
    # Offer this skill's own neutral templates as a last resort, but never let the
    # discovery reach its own repository (copying the skill into a run is never the
    # intended fix). The script is sometimes copied to a cluster on its own, so the
    # repository guard must only apply when this really is a skill checkout.
    skill_repo = SKILL_ROOT.parent
    is_skill_checkout = any(
        (skill_repo / marker).exists()
        for marker in ("SKILL.md", "scripts/check_schedulers.py", "references/submission.md")
    )
    bases.append(SKILL_ROOT / "assets" / "submission")
    deduped: list[Path] = []
    for base in bases:
        if base not in deduped:
            deduped.append(base)
    bases = deduped

    candidates: list[dict[str, str]] = []
    seen_dirs: set[str] = set()
    for submission_dir, submit_file in _iter_submission_pairs(bases):
        if str(submission_dir) in seen_dirs:
            continue
        seen_dirs.add(str(submission_dir))
        if is_skill_checkout and submission_dir != SKILL_ROOT / "assets" / "submission" and (
            submission_dir.parent != SKILL_ROOT / "assets" / "submission"
        ) and (
            skill_repo == submission_dir or skill_repo in submission_dir.parents
        ):
            continue  # the skill's own repository is not a site configuration
        is_template = submission_dir == SKILL_ROOT / "assets" / "submission"
        # submitJob_local.py is the primary callback; its sibling _remote.py usually
        # carries no queue constant (the queue lives in the remote submission file),
        # so extract the request from both.
        texts = []
        for name in ("submitJob_local.py", "submitJob_remote.py"):
            candidate_file = submission_dir / name
            if candidate_file.is_file():
                try:
                    texts.append((name, candidate_file.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue
        if not texts:
            continue
        primary_name, primary_text = texts[0]
        combined = "\n".join(text for _name, text in texts)
        unresolved = sorted(
            set(re.findall(r"<(?:PARTITION|QUEUE|LOAD_MODULES_AND_ACTIVATE_ENV)>", combined))
        )
        queue = ""
        placeholder_found = False
        for pattern in (
            r'^\s*(?:PARTITION|QUEUE)\s*=\s*["\']([^"\']*)["\']',
            r"#SBATCH\s+--partition=(\S+)",
            r"#PBS\s+-q\s+(\S+)",
        ):
            match = re.search(pattern, combined, re.MULTILINE)
            if not match:
                continue
            value = match.group(1)
            if value.upper().startswith("<"):
                # A placeholder is a definite "not filled in yet": stop, otherwise a
                # later pattern can match the template text inside a comment and
                # produce a junk queue name.
                placeholder_found = True
                break
            queue = value
            break
        is_template = submission_dir == SKILL_ROOT / "assets" / "submission" or (
            submission_dir.parent == SKILL_ROOT / "assets" / "submission"
        )
        local_pair = (submission_dir / "submitJob_local.py").is_file() and (
            submission_dir / "checkStatus_local.py"
        ).is_file()
        candidates.append(
            {
                "dir": str(submission_dir),
                "file": primary_name,
                "queue": queue,
                "placeholder": ",".join(unresolved),
                "local_pair": "yes" if local_pair else "no",
                "kind": "neutral-template" if is_template else "site-copy",
                "note": "" if primary_text else "submitJob_local.py missing",
            }
        )
        if len(candidates) >= limit:
            break
    # A site copy with a complete local pair and a real queue name is the best find.
    candidates.sort(
        key=lambda item: (
            item["kind"] == "neutral-template",
            item["local_pair"] != "yes",
            not item["queue"],
        )
    )
    return candidates


def submission_advice(workdir: Path | None, roots: Iterable[Path]) -> dict[str, object]:
    """Decide what to tell the user when a run has no usable Submission pair."""
    state = "unknown"
    which = -1
    if workdir is not None:
        state, which = _submission_state(workdir)
    candidates = _discover_submissions(workdir, roots)

    advice: dict[str, object] = {
        "state": state,
        "whichCluster": which,
        "candidates": candidates,
        "searched": [str(item) for item in roots],
    }
    if state == "pair_present":
        advice["verdict"] = "configured"
        return advice
    if state != "pair_present":
        advice["hint"] = (
            "widen the search with --search-root <dir> (for example the directory that holds "
            "your other USPEX runs) if the sites's pair lives elsewhere"
        )

    site_copies = [item for item in candidates if item["kind"] == "site-copy"]
    if state == "pair_incomplete" and site_copies:
        best = site_copies[0]
        advice["verdict"] = "complete-from-copy"
        advice["action"] = (
            f"Submission/ holds only part of the pair; copy the missing file(s) from "
            f"{best['dir']} (queue={best['queue'] or 'not declared there'}) and re-run"
        )
    elif site_copies:
        best = site_copies[0]
        advice["verdict"] = "reuse-existing"
        advice["action"] = (
            f"copy {best['dir']}/submitJob_local.py and checkStatus_local.py into "
            f"{workdir or 'the run'}/Submission/, adjust the queue/account if this project differs, "
            "and re-run this script"
            if workdir
            else f"reuse the pair in {best['dir']}"
        )
    elif candidates:
        best = candidates[0]
        advice["verdict"] = "adapt-template"
        advice["action"] = (
            f"only neutral templates were found (for example {best['dir']}); copy the matching "
            "family, fill PARTITION/QUEUE, the account and the resource request, then re-run"
        )
    else:
        advice["verdict"] = "ask-user"
        advice["action"] = (
            "no submit callback exists anywhere on this machine: ask the user which scheduler "
            "and queue the batch nodes run, which account/module preamble to use, and whether "
            "their site provides a Submission pair to copy. Do not invent a queue name, an "
            "account, or scheduler directives."
        )
    return advice


def _print_submission_report(advice: dict[str, object]) -> None:
    print("=== Submission/ ===")
    state = advice.get("state")
    which = advice.get("whichCluster")
    if state != "unknown":
        suffix = {0: " (whichCluster=0: no job script expected)", 2: " (whichCluster=2: remote pair)",
                  1: " (whichCluster=1: local pair)"}.get(which, "")
        print(f"  run Submission/ state: {state}{suffix}")
    candidates = advice.get("candidates") or []
    if candidates:
        print(f"  {len(candidates)} existing submit callback(s) found on this machine:")
        for item in candidates:
            queue = item.get("queue") or "(queue not filled in)"
            note = item.get("placeholder") or ""
            marker = " [neutral template]" if item.get("kind") == "neutral-template" else ""
            print(f"    - {item['dir']}/{item['file']}: queue={queue}{marker}"
                  + (f" placeholders={note}" if note else ""))
    else:
        print("  no submit callback found on this machine")
    print(f"  verdict: {advice.get('verdict')}")
    if advice.get("action"):
        print(f"  next: {advice['action']}")
    print()


def _read_submission_request(workdir: Path) -> tuple[list[str], list[str]]:
    """Read the queue/partition a run's Submission callbacks were configured with.

    Returns ``(findings, examined_files)`` so the report can state "nothing declared"
    instead of printing an empty section.
    """
    found: list[str] = []
    examined: list[str] = []
    submission = workdir / "Submission"
    if not submission.is_dir():
        return found, examined
    for path in sorted(submission.glob("*.py")):
        examined.append(path.name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern, label in (
            (r'^\s*PARTITION\s*=\s*["\']([^"\']+)["\']', "Slurm partition"),
            (r'^\s*QUEUE\s*=\s*["\']([^"\']+)["\']', "PBS queue"),
            (r'^\s*RESOURCE_LINE\s*=\s*["\']([^"\']+)["\']', "PBS resource request"),
            (r"#SBATCH\s+--partition=(\S+)", "SBATCH partition"),
            (r"#PBS\s+-q\s+(\S+)", "PBS queue"),
        ):
            for match in re.finditer(pattern, text, re.MULTILINE):
                value = match.group(1)
                if value.upper().startswith("<"):
                    found.append(
                        f"{path.name}: {label} is still the template placeholder {value} - edit it before submitting"
                    )
                else:
                    found.append(f"{path.name}: {label} = {value}")
    return found, examined


# --------------------------------------------------------------- test submission


def _slurm_test_submit(
    partition: str,
    nodes: int,
    ntasks: int,
    walltime: str,
    gpus: int,
    account: str,
    probe: Probe,
) -> tuple[bool, str]:
    """Slurm dry run: ``sbatch --test-only`` validates the request without queueing it."""
    argv = ["sbatch", "--test-only", f"--partition={partition}", f"--nodes={nodes}"]
    if ntasks:
        argv.append(f"--ntasks={ntasks}")
    if walltime:
        argv.append(f"--time={walltime}")
    if gpus:
        argv.append(f"--gpus={gpus}")
    if account:
        argv.append(f"--account={account}")
    argv.append("--wrap=true")
    result = probe.run(*argv)
    if result.ok:
        return True, result.message or "sbatch --test-only accepted the request"
    return False, result.error or result.message or f"sbatch exited {result.returncode}"


# ------------------------------------------------------------------------- main


def _human_report(
    schedulers: list[Scheduler],
    request: list[str],
    examined: list[str],
    args,
    probe: Probe,
    advice: dict[str, object] | None = None,
    facts: dict[str, object] | None = None,
) -> str:
    lines: list[str] = []
    if facts is not None:
        path = facts.get("path")
        if facts.get("exists"):
            recorded = facts.get("default_queue") or "(not recorded)"
            lines.append("=== Cluster guide (site facts) ===")
            lines.append(f"  {path}")
            lines.append(f"    scheduler: {facts.get('scheduler') or '(not recorded)'}")
            lines.append(f"    default queue/partition: {recorded}")
            lines.append(f"    verdict: {facts.get('verdict')}")
            if facts.get("action"):
                lines.append(f"    next: {facts['action']}")
            lines.append("")
        else:
            lines.append("=== Cluster guide (site facts) ===")
            lines.append(f"  {path}: not present -> {facts.get('action')}")
            lines.append("")
    for scheduler in schedulers:
        title = f"{scheduler.family.upper()}"
        if scheduler.version:
            title += f" ({scheduler.version})"
        lines.append(f"=== {title} ===")
        if scheduler.commands:
            lines.append(
                "commands: " + ", ".join(f"{name}" for name in sorted(scheduler.commands))
            )
        if scheduler.queues:
            lines.append(f"{len(scheduler.queues)} queue(s) found:")
            for queue in scheduler.queues:
                state = queue.state or "?"
                status = "available" if queue.available else f"NOT available ({queue.reason})"
                limits = f" | {queue.limits}" if queue.limits else ""
                lines.append(f"  - {queue.name}: state={state} -> {status}{limits}")
        else:
            lines.append("no queue could be listed")
        for note in scheduler.notes:
            lines.append(f"  note: {note}")
        for error in scheduler.errors:
            lines.append(f"  problem: {error}")
        lines.append("")

    if request:
        lines.append("=== Submission/ request ===")
        lines.extend(f"  - {item}" for item in request)
        lines.append("")
    elif examined:
        lines.append("=== Submission/ request ===")
        lines.append(
            "  no queue/partition declared in "
            + ", ".join(examined)
            + " - the callback must name one before the production run"
        )
        lines.append("")
    elif advice and advice.get("state") != "unknown":
        lines.append("=== Submission/ ===")
        lines.append(f"  run Submission/ state: {advice['state']}")
        lines.append(f"  verdict: {advice.get('verdict')}")
        if advice.get("action"):
            lines.append(f"  next: {advice['action']}")
        for item in advice.get("candidates") or []:
            queue = item.get("queue") or "(queue not filled in)"
            marker = " [neutral template]" if item.get("kind") == "neutral-template" else ""
            lines.append(f"    - candidate: {item['dir']}/{item['file']} queue={queue}{marker}")
        lines.append("")

    if args.test_submit:
        lines.append("=== dry-run validation ===")
        for scheduler in schedulers:
            if scheduler.family != "slurm":
                lines.append("  (--test-submit uses sbatch --test-only; skipped for non-Slurm)")
                continue
            candidates = [q.name for q in scheduler.queues if q.available]
            if args.partition and args.partition not in candidates:
                candidates.append(args.partition)
            if not candidates:
                lines.append("  no available partition to test")
                continue
            for name in candidates:
                ok, message = _slurm_test_submit(
                    name,
                    args.nodes,
                    args.ntasks,
                    args.walltime,
                    args.gpus,
                    args.account,
                    probe,
                )
                lines.append(
                    f"  - {name}: {'ACCEPTED' if ok else 'REJECTED'} - {message.splitlines()[0] if message else ''}"
                )
        lines.append("")

    lines.append("Next steps")
    lines.append("  1. Pick an available queue and write it into Submission/submitJob_local.py")
    lines.append("     (PARTITION for Slurm, QUEUE for PBS) - never guess a queue name.")
    lines.append("  2. Smoke-test one tiny job through the callback before the production run.")
    lines.append("  3. Re-run this script after login-node or policy changes.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--workdir", type=Path, default=None, help="USPEX run directory")
    parser.add_argument(
        "--search-root",
        type=Path,
        action="append",
        default=None,
        help="extra root to scan for existing Submission/ pairs (repeatable); "
        "~ is searched by default",
    )
    parser.add_argument("--partition", default="", help="partition to test with --test-submit")
    parser.add_argument(
        "--facts",
        type=Path,
        default=None,
        help="cluster guide to read site facts from (default: ~/.cluster-agents.md)",
    )
    parser.add_argument(
        "--facts-out",
        type=Path,
        default=None,
        help="write the observed facts as a '## Scheduler' block for the cluster guide",
    )
    parser.add_argument("--account", default="", help="account/project for the dry run")
    parser.add_argument("--nodes", type=int, default=1)
    parser.add_argument("--ntasks", type=int, default=8)
    parser.add_argument("--gpus", type=int, default=0)
    parser.add_argument("--walltime", default="01:00:00")
    parser.add_argument(
        "--test-submit",
        action="store_true",
        help="Slurm only: run 'sbatch --test-only' (dry run, no allocation)",
    )
    args = parser.parse_args(argv)

    probe = Probe(args.workdir)
    commands = _which(["sbatch", "srun", "sinfo", "squeue", "scontrol", "scancel",
                       "qsub", "qstat", "qdel", "pbsnodes", "qselect"])

    schedulers: list[Scheduler] = []
    kind = _detect_kind(commands, probe)
    if kind == "slurm":
        schedulers.append(probe_slurm(commands, probe))
    elif kind in {"pbspro", "torque", "openpbs", "pbs"}:
        schedulers.append(probe_pbs(commands, probe))
    else:
        schedulers.append(
            Scheduler(
                family="unknown",
                commands=commands,
                errors=[
                    "no scheduler client found on PATH (looked for sbatch/sinfo/squeue and qsub/qstat)"
                ],
                notes=[
                    "this may be a login node without the client module loaded, a container, or a "
                    "cluster reached through a wrapper. Try 'module avail' / 'module load slurm' or "
                    "'module load pbs', and ask the site which scheduler the batch nodes run"
                ],
            )
        )

    request, examined = (
        _read_submission_request(args.workdir) if args.workdir else ([], [])
    )
    # By default search the run's own project area and this skill; a wider search is
    # an explicit choice because it can be slow on a large home directory.
    roots = args.search_root if args.search_root else (
        [args.workdir.parent] if args.workdir else [SKILL_ROOT]
    )
    advice = submission_advice(args.workdir, roots)
    facts = facts_advice(
        read_cluster_facts(args.facts if args.facts else DEFAULT_FACTS_PATH)
    )

    if args.facts_out:
        primary = schedulers[0]
        block = render_facts_block(
            primary.family,
            primary.queues,
            primary.version,
            _current_user(),
        )
        args.facts_out.parent.mkdir(parents=True, exist_ok=True)
        args.facts_out.write_text(block, encoding="utf-8")
        print(f"wrote {args.facts_out} ({len(block)} bytes); merge it into the cluster guide", file=sys.stderr)

    if args.json:
        payload = {
            "workdir": str(args.workdir) if args.workdir else None,
            "schedulers": [
                {
                    "family": s.family,
                    "version": s.version,
                    "commands": s.commands,
                    "queues": [q.to_json() for q in s.queues],
                    "notes": s.notes,
                    "errors": s.errors,
                }
                for s in schedulers
            ],
            "submission_request": request,
            "submission_scripts_examined": examined,
            "submission_advice": advice,
            "cluster_facts": facts,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(_human_report(schedulers, request, examined, args, probe, advice, facts))

    usable = any(queue.available for s in schedulers for queue in s.queues)
    if usable:
        return 0
    if all(not s.commands for s in schedulers):
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
