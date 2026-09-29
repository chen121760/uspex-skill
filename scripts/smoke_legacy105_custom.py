#!/usr/bin/env python3
"""Contract smoke test for a USPEX 10.5 legacy custom-property adapter.

Stock USPEX 10.5 has no native arbitrary-property callback. A documented legacy
pattern uses **two independent patch points** in the installation tree:

    patch point 1  <USPEX>/.../FunctionFolder/Tool/userCalcFitness.py
        the code-99 user-code stage: flag 0 reports completion, flag 1 transports
        the enthalpy through energy.txt (and deletes it). This is what unlocks
        ``abinitioCode 99``.
    patch point 2  <USPEX>/.../FunctionFolder/calcElasticML.py
        replaces the stock ML-elasticity calculator with an adapter that maps one
        calculation directory to one ``<CALLRESULT>`` line. This is what the
        ``optType 12**`` channel then reads.

Neither patch alone is sufficient, and the adapter is NOT an ``abinitioCode``
entry: it is reached through ``optType``. This harness tests patch point 2 only,
against mock slot files:

    calculation directory (written by the patched 10.5 run)
        geom.in            POSCAR written by USPEX
        geom.out           POSCAR written by the code-99 producer
        energy.txt         one number, read and deleted by patched
                           userCalcFitness.py when flag == 1
        P1.txt ... P7.txt  one scalar each, written by the property calculator
            |
            v
    python <adapter> <structure_path> [model_path]
            |
            v
    exactly one stdout line:  <CALLRESULT> P1 P2 P3 P4 P5 P6 P7
    (seven space-separated values, fixed order; slot i is what optType 12i selects)
            |
            v
    patched 10.5  ->  MLProperties / 1201 ... 1207

THE CONTRACT (what USPEX itself depends on)
    * the adapter resolves the calculation directory from the structure-path
      argument, i.e. ``os.path.dirname(os.path.abspath(structure_path))``, and
      never from its own module location, so it may live outside the
      calculation directory;
    * it writes exactly one ``<CALLRESULT> P1 P2 P3 P4 P5 P6 P7`` line on
      stdout: seven finite values in fixed slot order and nothing else on that
      line. Seven fixed-order values on one line is the contract.

RECOMMENDED HARDENING (better than the provided legacy adapter, not guaranteed
by it)
    A missing ``Pi.txt``, an empty or whitespace-only file, a non-numeric
    token, ``NaN`` / ``Inf`` / ``-Inf``, an overflow such as ``1e999``, two
    tokens in one file, or a header/log line in the file is a fatal
    misconfiguration: the adapter reports it on stderr and exits non-zero
    WITHOUT printing any ``<CALLRESULT>`` line. The provided legacy adapter
    instead falls back to ``0.0`` for any read failure and still exits 0, and
    ``0.0`` is the best possible value for a minimisation objective, so a
    failed property calculation becomes indistinguishable from a perfect
    structure. Finite negative values are legal (they can be a deliberate
    penalty) and so is scientific notation such as ``1.5e-3``.

HOW TO USE
    python scripts/smoke_legacy105_custom.py
    python scripts/smoke_legacy105_custom.py --compat-legacy
    python scripts/smoke_legacy105_custom.py --adapter /path/to/project_adapter.py --allow-skip
    python scripts/smoke_legacy105_custom.py --require-fresh --json report.json

    The default run tests the embedded strict reference adapter. ``--compat-legacy``
    tests the embedded legacy-compatible adapter (default ``0.0`` on read
    failure) and reports every state the strict contract rejects as
    CHARACTERIZED instead of a contract violation, flagging the dangerous
    silent-zero fallback explicitly. Combined with ``--adapter PATH`` it
    characterises the user-owned adapter instead: the adapter under test is
    unchanged and only the reporting mode changes, so a state the strict
    contract rejects is listed as CHARACTERIZED with its reason instead of
    failing the run. ``--adapter PATH`` runs a user-owned adapter against the
    same scenarios; cases that cannot be expressed for a foreign adapter (the
    harness self-check probes and the optional model_path case) are reported as
    SKIP with a reason, and a SKIP makes the exit code non-zero unless
    ``--allow-skip`` is given, so nothing is skipped silently.
    ``--require-fresh`` turns the default staleness WARN (a ``P*.txt`` older
    than ``geom.in``) into a failure.

PATHS AND THE JSON REPORT
    Every path option is resolved against the directory the command was started
    in, before any scratch directory exists: ``--adapter`` is made absolute once
    and that absolute path is what each adapter subprocess receives, so a
    relative ``--adapter`` keeps working even though every case runs with its
    own scratch directory as the subprocess working directory. The same applies
    to a relative ``--python``. A missing ``--adapter`` is a usage error
    (exit 2) naming both the path as given and the path it resolved to; the run
    never continues with an unreachable adapter, because then the
    fatal-expectation cases would pass vacuously and the report would be
    misleading.

    ``--json`` writes schema ``uspex-legacy105-smoke/2`` and records the adapter
    path as given (``adapter``), the absolute path actually executed
    (``adapter_resolved``) and the exact command line of every case (per-case
    ``argv``, with a top-level ``argv`` for the first executed case), so the run
    can be reproduced from the report alone. For the embedded adapters
    ``adapter`` is the embedded label and ``adapter_resolved`` is the temporary
    file written from it (see ``--keep-tmp``).

EXIT CODES
    0  every contract case behaved as expected
    1  at least one case failed, or a case was skipped without --allow-skip
    2  usage or IO error (for example a missing --adapter path)
    3  internal harness error

LIMITS
    This is an offline filesystem smoke test. It never runs USPEX, the patched
    ``userCalcFitness.py``, MPI or a scheduler, and it does not know any
    project property definition; it validates the directory handshake and the
    adapter contract only. Every subprocess run has a timeout (default 60 s)
    and a timeout is reported as a case failure, never as a hang.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple
from pathlib import Path


SCHEMA = "uspex-legacy105-smoke/2"

GEOM_IN = "geom.in"
GEOM_OUT = "geom.out"
ENERGY_TXT = "energy.txt"
SLOT_COUNT = 7
CALLRESULT_TAG = "<CALLRESULT>"
DEFAULT_TIMEOUT = 60.0
MODEL_ARG_NAME = "model.bin"
DEFAULT_ENERGY_TEXT = "-12.5\n"
INTERNAL_ERROR_PREFIX = "internal harness error: "

ADAPTER_LABEL_STRICT = "embedded:strict-reference"
ADAPTER_LABEL_LEGACY = "embedded:legacy-compatible"

# A plain decimal or scientific number: no underscores, no units, no BOM, no
# second token. float() is deliberately stricter here than Python's own parser
# (float('1_000') == 1000.0, float('\ufeff11') raises only after decode).
STRICT_VALUE_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?$")
CALLRESULT_LINE_RE = re.compile(r"^<CALLRESULT>\s*(.*)$")

# The mapping the producer is supposed to write for the happy path.
NOMINAL_SLOT_TEXT = ("11", "22", "33", "44", "55", "66", "77")
NOMINAL_VALUES = tuple(float(text) for text in NOMINAL_SLOT_TEXT)
VALUE_ABS_TOL = 1e-9

# Deterministic timestamps: no test depends on how fast the wall clock moves.
# geom.in gets BASE, fresh producer output gets BASE + 10 s, stale output
# gets BASE - 10 s (strictly older than geom.in).
BASE_MTIME_NS = 1600000000000000000
FRESH_OFFSET_NS = 10 * 1000 * 1000 * 1000
STALE_OFFSET_NS = -10 * 1000 * 1000 * 1000

# Only the presence and size of these placeholders matter; no geometry is ever
# parsed by this harness, so the cell below is deliberately trivial.
POSCAR_PLACEHOLDER = """smoke placeholder geometry, never parsed by this harness
1.0
5.0 0.0 0.0
0.0 5.0 0.0
0.0 0.0 5.0
Si
2
Direct
0.00 0.00 0.00
0.25 0.25 0.25
"""

MODEL_PLACEHOLDER = "smoke placeholder for the optional model_path argument\n"

Finding = namedtuple("Finding", "level message")

# Human wording for the slot states the strict contract rejects, used by the
# DANGEROUS diagnostics so the report names the real defect.
SLOT_STATE_WORDS = {
    "absent": "missing",
    "unreadable": "unreadable",
    "empty": "empty",
    "syntax": "not a number",
    "nonfinite": "not finite",
}


class UsageError(Exception):
    """Bad invocation: maps to exit code 2."""


class HarnessError(Exception):
    """Internal harness problem: maps to exit code 3."""


class ContractViolation(Exception):
    """A property slot does not satisfy the strict adapter contract."""


STRICT_ADAPTER = r'''"""Reference USPEX 10.5 legacy custom-property adapter (strict contract).

The patched 10.5 ``userCalcFitness.py`` invokes this file as

    python <adapter> <structure_path> [model_path]

and parses exactly one stdout line:  <CALLRESULT> P1 P2 P3 P4 P5 P6 P7

The calculation directory is resolved from the structure-path argument, so the
adapter may live anywhere. Any missing, unreadable, empty, non-numeric or
non-finite ``Pi.txt`` is a fatal misconfiguration: nothing is written to stdout
and the exit status is non-zero, so a failed property calculation can never be
mistaken for a good one. Finite negative values are legal.
"""

import math
import os
import re
import sys

SLOTS = 7
VALUE_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?$")


def fail(message):
    sys.stderr.write("adapter error: " + message + "\n")
    raise SystemExit(1)


def read_slot(directory, index):
    path = os.path.join(directory, "P%d.txt" % index)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        fail("%s is missing" % path)
    except (OSError, UnicodeError) as exc:
        fail("cannot read %s: %s" % (path, exc))
    token = text.strip()
    if not token:
        fail("%s is empty or whitespace only" % path)
    if not VALUE_RE.match(token):
        fail("%s is not a plain number: %r" % (path, token))
    value = float(token)
    if not math.isfinite(value):
        fail("%s is not finite: %r" % (path, token))
    return token


def main(argv):
    if len(argv) < 2:
        fail("usage: adapter <structure_path> [model_path]")
    directory = os.path.dirname(os.path.abspath(argv[1]))
    # Read every slot before writing anything, so a failure never leaves a
    # partial <CALLRESULT> line behind.
    tokens = [read_slot(directory, index) for index in range(1, SLOTS + 1)]
    sys.stdout.write("<CALLRESULT> " + " ".join(tokens) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


LEGACY_ADAPTER = r'''"""Legacy-compatible USPEX 10.5 custom-property adapter.

This mirrors the provided patched adapter: any read or parse failure for a slot
silently becomes 0.0 and the adapter still exits 0. It exists so
``--compat-legacy`` can characterise that fallback against the strict contract.
"""

import os
import sys

SLOTS = 7


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: adapter <structure_path> [model_path]\n")
        return 1
    directory = os.path.dirname(os.path.abspath(argv[1]))
    values = []
    for index in range(1, SLOTS + 1):
        path = os.path.join(directory, "P%d.txt" % index)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                values.append(float(handle.read().strip()))
        except Exception:
            values.append(0.0)
    sys.stdout.write("<CALLRESULT> " + " ".join("%g" % value for value in values) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''


# Deliberately broken adapters used by the harness self-check cases. They prove
# the harness itself rejects malformed output instead of trusting it.
PROBE_TRUNCATED = 'import sys\n\nsys.stdout.write("<CALLRESULT> 11 22 33\\n")\n'

PROBE_EXTRA_TOKEN = 'import sys\n\nsys.stdout.write("<CALLRESULT> 11 22 33 44 55 66 77 88\\n")\n'

PROBE_NO_LINE = 'import sys\n\nsys.stdout.write("computing properties...\\n")\n'

PROBE_NONZERO_EXIT = (
    'import sys\n\n'
    'sys.stdout.write("<CALLRESULT> 11 22 33 44 55 66 77\\n")\n'
    'sys.stderr.write("placeholder model file could not be read\\n")\n'
    'sys.exit(3)\n'
)

PROBE_NONFINITE = 'import sys\n\nsys.stdout.write("<CALLRESULT> 11 22 1e999 44 55 66 77\\n")\n'

PROBE_NAN_TEXT = 'import sys\n\nsys.stdout.write("<CALLRESULT> 11 22 nan 44 55 66 77\\n")\n'

PROBE_SKIP_REASON = (
    "harness self-check probe: it runs its own deliberately broken embedded adapter, so it "
    "cannot be asserted against a user-owned --adapter"
)
MODEL_ARG_SKIP_REASON = (
    "the optional model_path argument is passed as a placeholder file and a user-owned "
    "adapter may legitimately require a real model, so the harness does not assert this case "
    "against --adapter"
)


def slot_name(index: int) -> str:
    return "P%d.txt" % index


def write_text(path, text: str) -> None:
    # Path.write_text(newline=...) is Python 3.10+, so open explicitly to keep the
    # file bytes identical on every platform and locale.
    with open(str(path), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def classify_value(text) -> tuple:
    """Classify one raw ``Pi.txt`` text.

    Returns ``(kind, value, detail)`` where kind is ``ok``, ``empty``, ``syntax``
    or ``nonfinite``. Only ``ok`` is accepted by the strict contract; the three
    failure kinds are what the legacy adapter hides behind a ``0.0`` default.
    """
    token = (text or "").strip()
    if not token:
        return ("empty", None, "empty or whitespace only")
    if not STRICT_VALUE_RE.match(token):
        return ("syntax", None, "not a plain decimal or scientific number")
    value = float(token)
    if not math.isfinite(value):
        return ("nonfinite", value, "not finite (it overflows to +/-inf)")
    return ("ok", value, None)


def is_strict_number(text) -> bool:
    """True only for a plain finite decimal or scientific number."""
    return classify_value(text)[0] == "ok"


def parse_property_value(text) -> float:
    """Return the float for one slot text or raise ``ContractViolation``."""
    kind, value, detail = classify_value(text)
    if kind != "ok":
        raise ContractViolation("%s: %r" % (detail, (text or "").strip()))
    return value


def read_property_slot(path: Path) -> tuple:
    """Classify one ``Pi.txt`` file.

    Returns ``(kind, value, detail)`` where kind is ``ok``, ``absent``,
    ``unreadable``, ``empty``, ``syntax`` or ``nonfinite``. ``absent`` covers a
    missing file and a broken symlink; ``unreadable`` covers a directory, a
    symlink to a directory and a non-UTF-8 file.
    """
    path = Path(path)
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return ("absent", None, "%s does not exist" % path.name)
    except (OSError, UnicodeError) as exc:
        return ("unreadable", None, "%s cannot be read: %s" % (path.name, exc))
    return classify_value(text)


def values_match(observed, expected) -> bool:
    if observed is None or expected is None or len(observed) != len(expected):
        return False
    return all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=VALUE_ABS_TOL)
        for left, right in zip(observed, expected)
    )


def nominal_mismatch_slots(values) -> list:
    """Slots whose reported value differs from the nominal producer mapping."""
    if values is None:
        return []
    slots = []
    for index in range(1, SLOT_COUNT + 1):
        if index > len(values):
            break
        if not math.isclose(values[index - 1], NOMINAL_VALUES[index - 1], rel_tol=0.0, abs_tol=VALUE_ABS_TOL):
            slots.append(index)
    return slots


def judge_adapter_output(exit_code: int, stdout: str) -> dict:
    """Decide whether one adapter invocation produced a valid ``<CALLRESULT>`` line."""
    lines = [line for line in stdout.splitlines() if CALLRESULT_TAG in line]
    result = {
        "ok": False,
        "problem": None,
        "line": None,
        "tokens": None,
        "values": None,
        "has_callresult": bool(lines),
        "callresult_count": len(lines),
        "exit_code": exit_code,
    }
    token_problem = None
    if len(lines) == 1:
        line = lines[0].strip()
        result["line"] = line
        match = CALLRESULT_LINE_RE.match(line)
        if match is None:
            token_problem = "the %s tag must start the line" % CALLRESULT_TAG
        else:
            tokens = match.group(1).split()
            result["tokens"] = tokens
            if len(tokens) != SLOT_COUNT:
                token_problem = "expected exactly %d values after %s, found %d" % (
                    SLOT_COUNT,
                    CALLRESULT_TAG,
                    len(tokens),
                )
            else:
                values = []
                for index, token in enumerate(tokens, start=1):
                    try:
                        values.append(float(token))
                    except ValueError:
                        values = None
                        token_problem = "value %r is not a number" % token
                        break
                    kind, _, _ = classify_value(token)
                    if kind != "ok":
                        token_problem = "slot %d (%s) is %s: %r" % (index, slot_name(index), kind, token)
                        break
                result["values"] = values
    if exit_code != 0:
        result["problem"] = "adapter exited %d" % exit_code
    elif not lines:
        result["problem"] = "no %s line on stdout" % CALLRESULT_TAG
    elif len(lines) > 1:
        result["problem"] = "expected exactly one %s line, found %d" % (CALLRESULT_TAG, len(lines))
    else:
        result["problem"] = token_problem
    result["ok"] = result["problem"] is None
    return result


def first_line_of(path: Path):
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def inspect_directory(root: Path, require_fresh: bool = False) -> list:
    """Check the legacy 10.5 handshake files that USPEX itself inspects.

    This is independent of the adapter: the patched ``userCalcFitness.py``
    flag 0 requires a non-empty ``geom.out`` and ``energy.txt`` before the
    property path is reachable at all, and flag 1 does ``float(readline())`` on
    ``energy.txt``.
    """
    root = Path(root)
    findings = []
    geom_in = root / GEOM_IN
    geom_out = root / GEOM_OUT
    energy = root / ENERGY_TXT

    if not geom_in.is_file() or geom_in.stat().st_size == 0:
        findings.append(
            Finding(
                "error",
                "%s is missing or empty: USPEX writes it before the calculation, so this "
                "directory is not a USPEX 10.5 calculation directory" % GEOM_IN,
            )
        )
    if not geom_out.is_file() or geom_out.stat().st_size == 0:
        findings.append(
            Finding(
                "error",
                "%s is missing or empty: the patched userCalcFitness.py flag 0 requires it, so "
                "USPEX reports the calculation as unfinished and never reaches the property "
                "path" % GEOM_OUT,
            )
        )
    if not energy.is_file() or energy.stat().st_size == 0:
        findings.append(
            Finding(
                "error",
                "%s is missing or empty: the patched userCalcFitness.py flag 0 requires it and "
                "flag 1 reads its first line" % ENERGY_TXT,
            )
        )
    elif classify_value(first_line_of(energy))[0] != "ok":
        findings.append(
            Finding(
                "error",
                "%s does not start with a plain finite number, but the patched flag 1 does "
                "float(readline())" % ENERGY_TXT,
            )
        )

    if geom_in.is_file():
        stamp = geom_in.stat().st_mtime_ns
        stale = []
        for index in range(1, SLOT_COUNT + 1):
            path = root / slot_name(index)
            if path.is_file() and path.stat().st_mtime_ns < stamp:
                stale.append(path.name)
        if stale:
            findings.append(
                Finding(
                    "error" if require_fresh else "warn",
                    "%s older than %s: a previous run's value may be stale, so re-run the "
                    "producer or delete the file before trusting this property (--require-fresh "
                    "turns this into a failure)" % (", ".join(stale), GEOM_IN),
                )
            )
    return findings


def mapping_findings(judgement: dict) -> list:
    """Report a producer value/expected mapping mismatch, e.g. two swapped slots."""
    if not judgement["ok"]:
        return []
    slots = nominal_mismatch_slots(judgement["values"])
    if not slots:
        return []
    tokens = judgement["tokens"] or []
    detail = ", ".join(
        "%s=%s (nominal %s)" % (slot_name(index), tokens[index - 1] if index <= len(tokens) else "?",
                                NOMINAL_SLOT_TEXT[index - 1])
        for index in slots
    )
    return [
        Finding(
            "warn",
            "value/expected mapping mismatch on slot(s) %s: %s"
            % (", ".join(str(index) for index in slots), detail),
        )
    ]


def broken_slots(scenario: "Scenario") -> dict:
    """Slots the strict contract rejects, as ``{index: kind}``."""
    broken = {}
    for index in range(1, SLOT_COUNT + 1):
        text = scenario.slot_text(index)
        if text is None:
            broken[index] = "absent"
            continue
        kind, _, _ = classify_value(text)
        if kind != "ok":
            broken[index] = kind
    return broken


def danger_notes(broken: dict, values) -> list:
    """Flag the legacy silent-zero fallback and non-finite reported values.

    ``broken`` maps slot index to the classify_value/read_property_slot kind of
    a slot the strict contract rejects, so a silent 0.0 for such a slot is the
    dangerous legacy fallback rather than a real measurement.
    """
    notes = []
    if values is None:
        return notes
    silent = sorted(index for index in broken if index <= len(values) and values[index - 1] == 0.0)
    nonfinite = sorted(
        index for index in broken if index <= len(values) and not math.isfinite(values[index - 1])
    )
    if silent:
        slots = ", ".join("%s (%s)" % (slot_name(index), SLOT_STATE_WORDS.get(broken[index], broken[index]))
                          for index in silent)
        notes.append(
            "DANGEROUS: %s silently became 0.0 in the %s line; 0.0 is the best possible value "
            "for a minimisation objective, so a failed property calculation is "
            "indistinguishable from an optimal structure" % (slots, CALLRESULT_TAG)
        )
    if nonfinite:
        notes.append(
            "DANGEROUS: the adapter reported a non-finite value for %s"
            % ", ".join("%s=%r" % (slot_name(i), values[i - 1]) for i in nonfinite)
        )
    return notes


class Scenario(object):
    """Files to create in one fresh scratch directory, plus their timestamps."""

    __slots__ = ("slots", "drop", "geom_in", "geom_out", "energy", "extra_files", "stale",
                 "model_arg", "structure_file")

    def __init__(self, slots=None, drop=(), geom_in=True, geom_out=True, energy=DEFAULT_ENERGY_TEXT,
                 extra_files=None, stale=False, model_arg=False, structure_file=None):
        self.slots = dict(slots or {})
        self.drop = tuple(drop)
        self.geom_in = geom_in
        self.geom_out = geom_out
        self.energy = energy
        self.extra_files = dict(extra_files or {})
        self.stale = stale
        self.model_arg = model_arg
        if structure_file is not None:
            self.structure_file = structure_file
        else:
            # USPEX passes the structure file it wrote; when geom.out is missing
            # on purpose, the always-present geom.in keeps the argument real.
            self.structure_file = GEOM_OUT if geom_out else GEOM_IN

    def slot_text(self, index: int):
        if index in self.slots:
            return self.slots[index]
        if index in self.drop:
            return None
        return NOMINAL_SLOT_TEXT[index - 1]

    def build(self, root: Path) -> Path:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        texts = {}
        if self.geom_in:
            texts[GEOM_IN] = POSCAR_PLACEHOLDER
        if self.geom_out:
            texts[GEOM_OUT] = POSCAR_PLACEHOLDER
        if self.energy is not None:
            texts[ENERGY_TXT] = self.energy
        for index in range(1, SLOT_COUNT + 1):
            text = self.slot_text(index)
            if text is not None:
                texts[slot_name(index)] = text
        for name in sorted(self.extra_files):
            texts[name] = self.extra_files[name]
        if self.model_arg:
            texts[MODEL_ARG_NAME] = MODEL_PLACEHOLDER
        for name in texts:
            write_text(root / name, texts[name])
        for name in texts:
            if name == GEOM_IN:
                offset = 0
            elif self.stale and name.startswith("P"):
                offset = STALE_OFFSET_NS
            else:
                offset = FRESH_OFFSET_NS
            stamp = BASE_MTIME_NS + offset
            os.utime(str(root / name), ns=(stamp, stamp))
        return root

    def expected_values(self):
        values = []
        for index in range(1, SLOT_COUNT + 1):
            text = self.slot_text(index)
            if text is None:
                return None
            kind, value, _ = classify_value(text)
            if kind != "ok":
                return None
            values.append(value)
        return tuple(values)


class CaseSpec(object):
    """One smoke scenario and the behaviour it expects."""

    __slots__ = ("name", "kind", "scenario", "expected", "problem_contains",
                 "handshake_pattern", "mismatch_slots", "probe", "stale", "skip_with_adapter")

    def __init__(self, name, kind, scenario, expected, problem_contains=None,
                 handshake_pattern=None, mismatch_slots=(), probe=None, stale=False,
                 skip_with_adapter=None):
        self.name = name
        self.kind = kind
        self.scenario = scenario
        self.expected = expected
        self.problem_contains = problem_contains
        self.handshake_pattern = handshake_pattern
        self.mismatch_slots = tuple(mismatch_slots)
        self.probe = probe
        self.stale = stale
        self.skip_with_adapter = skip_with_adapter

    def expected_values(self):
        return self.scenario.expected_values()


def build_cases() -> tuple:
    """Return every smoke case in report order (deterministic, no dict ordering)."""
    fatal = "fatal: exit != 0, no <CALLRESULT> line, stderr diagnostic"
    cases = [
        CaseSpec("happy_path", "values", Scenario(),
                 "exactly <CALLRESULT> 11 22 33 44 55 66 77 and exit 0"),
        CaseSpec("happy_path_with_model_arg", "values", Scenario(model_arg=True),
                 "same line when the optional model_path argument is also passed",
                 skip_with_adapter=MODEL_ARG_SKIP_REASON),
        CaseSpec("missing_p1", "fatal", Scenario(drop=(1,)), fatal + " (P1.txt missing)"),
        CaseSpec("missing_middle_p4", "fatal", Scenario(drop=(4,)), fatal + " (P4.txt missing)"),
        CaseSpec("missing_p7", "fatal", Scenario(drop=(7,)), fatal + " (P7.txt missing)"),
        CaseSpec("empty_slot_file", "fatal", Scenario(slots={3: ""}),
                 fatal + " (P3.txt is empty)"),
        CaseSpec("whitespace_only_slot_file", "fatal", Scenario(slots={3: " \t\n \n"}),
                 fatal + " (P3.txt is whitespace only)"),
        CaseSpec("nonnumeric_slot", "fatal", Scenario(slots={2: "abc\n"}),
                 fatal + " (P2.txt is not a number)"),
        CaseSpec("unit_suffix_slot", "fatal", Scenario(slots={2: "12.3 eV\n"}),
                 fatal + " (P2.txt carries a unit suffix)"),
        CaseSpec("nan_slot", "fatal", Scenario(slots={2: "NaN\n"}),
                 fatal + " (P2.txt is NaN)"),
        CaseSpec("inf_slot", "fatal", Scenario(slots={2: "Inf\n"}),
                 fatal + " (P2.txt is Inf)"),
        CaseSpec("negative_inf_slot", "fatal", Scenario(slots={2: "-Inf\n"}),
                 fatal + " (P2.txt is -Inf)"),
        CaseSpec("overflow_slot", "fatal", Scenario(slots={3: "1e999\n"}),
                 fatal + " (P3.txt=1e999 overflows to inf)"),
        CaseSpec("scientific_notation_slot", "values", Scenario(slots={2: "1.5e-3\n"}),
                 "accepted: <CALLRESULT> 11 0.0015 33 44 55 66 77"),
        CaseSpec("negative_penalty_slot", "values", Scenario(slots={2: "-42.5\n"}),
                 "accepted: <CALLRESULT> 11 -42.5 33 44 55 66 77 (finite penalty is legal)"),
        CaseSpec("negative_zero_slot", "values", Scenario(slots={2: "-0.0\n"}),
                 "accepted: slot 2 is a finite -0.0"),
        CaseSpec("two_tokens_in_slot", "fatal", Scenario(slots={3: "1 2\n"}),
                 fatal + " (P3.txt holds two tokens)"),
        CaseSpec("header_line_in_slot", "fatal", Scenario(slots={3: "# P3 property value\n"}),
                 fatal + " (P3.txt holds a header line)"),
        CaseSpec("extra_p8_ignored", "values", Scenario(extra_files={"P8.txt": "88\n"}),
                 "accepted: exactly seven values, the extra P8.txt is ignored"),
        CaseSpec("stale_property_files", "values", Scenario(stale=True),
                 "accepted with a stale-file WARN (fails under --require-fresh)", stale=True),
        CaseSpec("wrong_ordering", "values", Scenario(slots={1: "22\n", 2: "11\n"}),
                 "harness reports the P1/P2 mapping mismatch <CALLRESULT> 22 11 33 44 55 66 77",
                 mismatch_slots=(1, 2)),
        CaseSpec("properties_without_geom_out", "handshake", Scenario(geom_out=False),
                 "harness rejects the directory: geom.out missing although P*.txt look valid",
                 handshake_pattern="geom.out"),
        CaseSpec("geom_out_missing", "fatal_and_handshake",
                 Scenario(geom_out=False, drop=tuple(range(1, SLOT_COUNT + 1))),
                 fatal + " and harness rejects the directory: geom.out missing",
                 handshake_pattern="geom.out"),
        CaseSpec("energy_txt_missing", "handshake", Scenario(energy=None),
                 "harness rejects the directory: energy.txt missing",
                 handshake_pattern="energy.txt"),
        CaseSpec("energy_txt_non_numeric", "handshake", Scenario(energy="not-a-number\n"),
                 "harness rejects the directory: energy.txt is not a number",
                 handshake_pattern="energy.txt"),
        CaseSpec("probe_truncated_stdout", "detect_output_fault", Scenario(),
                 "harness rejects the truncated line: expected exactly 7 values, found 3",
                 problem_contains="expected exactly 7 values", probe=PROBE_TRUNCATED,
                 skip_with_adapter=PROBE_SKIP_REASON),
        CaseSpec("probe_extra_trailing_token", "detect_output_fault", Scenario(),
                 "harness rejects the eighth token: found 8",
                 problem_contains="found 8", probe=PROBE_EXTRA_TOKEN,
                 skip_with_adapter=PROBE_SKIP_REASON),
        CaseSpec("probe_missing_callresult", "detect_output_fault", Scenario(),
                 "harness rejects stdout with no <CALLRESULT> line",
                 problem_contains="no <CALLRESULT> line", probe=PROBE_NO_LINE,
                 skip_with_adapter=PROBE_SKIP_REASON),
        CaseSpec("probe_nonzero_exit_valid_line", "detect_output_fault", Scenario(),
                 "harness rejects a valid line from an adapter that exited 3",
                 problem_contains="exited 3", probe=PROBE_NONZERO_EXIT,
                 skip_with_adapter=PROBE_SKIP_REASON),
        CaseSpec("probe_nonfinite_value", "detect_output_fault", Scenario(),
                 "harness rejects a value that overflows to inf (1e999)",
                 problem_contains="is nonfinite", probe=PROBE_NONFINITE,
                 skip_with_adapter=PROBE_SKIP_REASON),
        CaseSpec("probe_nan_text", "detect_output_fault", Scenario(),
                 "harness rejects the literal token nan on the <CALLRESULT> line",
                 problem_contains="is syntax", probe=PROBE_NAN_TEXT,
                 skip_with_adapter=PROBE_SKIP_REASON),
    ]
    return tuple(cases)


CASE_SPECS = build_cases()
CASE_NAMES = tuple(case.name for case in CASE_SPECS)


def resolve_cases(names) -> list:
    """Select cases by name, keeping the definition order."""
    wanted = list(names or [])
    if not wanted:
        return list(CASE_SPECS)
    known = set(CASE_NAMES)
    unknown = [name for name in wanted if name not in known]
    if unknown:
        raise UsageError(
            "unknown --case name(s): %s; valid names are: %s"
            % (", ".join(unknown), ", ".join(CASE_NAMES))
        )
    selected = set(wanted)
    return [case for case in CASE_SPECS if case.name in selected]


def caller_base_dir(args) -> Path:
    """Directory the command was started in, used to resolve relative paths.

    ``main`` captures it with ``os.getcwd()`` before it creates any scratch
    directory; a caller that builds a Namespace by hand falls back to the
    current working directory.
    """
    value = getattr(args, "base_dir", None)
    if value is None:
        return Path.cwd()
    return Path(value)


def resolve_input_path(base_dir, value) -> Path:
    """Make a user-supplied path absolute against the caller's directory.

    Each case runs the adapter with its own scratch directory as the subprocess
    working directory, so a relative ``--adapter`` must be resolved once here
    and never be handed to the subprocess unresolved.
    """
    path = Path(value)
    if not path.is_absolute():
        path = Path(base_dir) / path
    return path.resolve()


def resolve_python(value, base_dir=None) -> Path:
    """Resolve the adapter interpreter to an existing absolute path."""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    if value is None:
        return Path(sys.executable).resolve()
    text = str(value)
    candidate = resolve_input_path(base, text)
    if candidate.is_file():
        return candidate
    found = shutil.which(text)
    if found:
        return Path(found).resolve()
    raise UsageError(
        "--python interpreter does not exist: %s (resolved to %s)" % (text, candidate)
    )


def resolve_adapter(args):
    """Return ``(adapter_as_given, adapter_resolved_or_None)`` for ``--adapter``.

    ``None`` for the resolved path means the embedded adapters are in use. A
    relative ``--adapter`` is resolved against the caller's directory and the
    file must exist, so the harness can never run every case against a missing
    adapter file and report vacuously passing fatal-expectation cases.
    """
    given = getattr(args, "adapter", None)
    if given is None:
        return (None, None)
    resolved = getattr(args, "adapter_resolved", None)
    if resolved is None:
        resolved = resolve_input_path(caller_base_dir(args), given)
    resolved = Path(resolved)
    if not resolved.is_file():
        raise UsageError(
            "--adapter not found: given '%s', resolved to '%s' (the path is resolved against the "
            "directory the command was started in '%s')"
            % (str(given), str(resolved), str(caller_base_dir(args)))
        )
    return (str(given), resolved)



def as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def run_case(case: CaseSpec, adapter_path: Path, python_exe: Path, workdir: Path,
             timeout: float, require_fresh: bool) -> dict:
    """Build one scratch directory, run the adapter in it and observe the result."""
    scratch = Path(workdir) / "cases" / case.name
    case.scenario.build(scratch)
    structure_path = scratch / case.scenario.structure_file
    argv = [str(python_exe), str(adapter_path), str(structure_path)]
    if case.scenario.model_arg:
        argv.append(str(scratch / MODEL_ARG_NAME))

    timed_out = False
    exit_code = 0
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            argv,
            cwd=str(scratch),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = as_text(exc.stdout)
        stderr = as_text(exc.stderr)

    judgement = judge_adapter_output(exit_code, stdout)
    if timed_out:
        stderr = ((stderr.rstrip("\n") + "\n") if stderr.strip() else "") + (
            "harness: timed out after %.1f s" % timeout
        )
        judgement["problem"] = "timed out after %.1f s" % timeout

    findings = inspect_directory(scratch, require_fresh)
    if case.mismatch_slots:
        # Only a case that deliberately constructs a swapped/misplaced producer
        # mapping expects a mapping warning; other scenarios change one slot on
        # purpose (scientific notation, a penalty sentinel) and are not defects.
        findings.extend(mapping_findings(judgement))
    broken = broken_slots(case.scenario)
    return {
        "scratch": scratch,
        "argv": argv,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "judgement": judgement,
        "findings": findings,
        "danger": danger_notes(broken, judgement["values"]),
        "broken_slots": broken,
    }


def describe_observation(observation: dict) -> str:
    judgement = observation["judgement"]
    if observation["timed_out"]:
        return "TIMEOUT after the adapter did not return"
    if judgement["line"] is not None:
        return judgement["line"]
    if judgement["has_callresult"]:
        return "exit %s, malformed %s (%s)" % (
            observation["exit_code"],
            CALLRESULT_TAG,
            judgement["problem"],
        )
    return "exit %s, no %s" % (observation["exit_code"], CALLRESULT_TAG)


def fatal_checks(observation: dict, judgement: dict) -> list:
    return [
        ("exit != 0", (not observation["timed_out"]) and observation["exit_code"] != 0),
        ("no %s line" % CALLRESULT_TAG, not judgement["has_callresult"]),
        ("diagnostic on stderr", bool(observation["stderr"].strip())),
    ]


def has_pattern(errors, pattern) -> bool:
    if not pattern:
        return False
    return any(pattern in finding.message for finding in errors)


def evaluate_case(case: CaseSpec, observation: dict, require_fresh: bool) -> tuple:
    """Return ``(ok, expected, observed, detail)`` for one case."""
    judgement = observation["judgement"]
    findings = observation["findings"]
    errors = [finding for finding in findings if finding.level == "error"]
    warns = [finding for finding in findings if finding.level == "warn"]

    kind = case.kind
    pattern = case.handshake_pattern
    expected = case.expected
    if case.stale and require_fresh:
        # --require-fresh turns the staleness WARN into a rejection, so the case
        # expects the harness to refuse the directory instead of the adapter to pass.
        kind = "handshake"
        pattern = "older than %s" % GEOM_IN
        expected = "harness rejects: P*.txt older than %s (--require-fresh)" % GEOM_IN

    checks = []
    if kind == "values":
        checks.append(("adapter output accepted", (not observation["timed_out"]) and judgement["ok"]))
        checks.append(("values match the expected mapping", values_match(judgement["values"], case.expected_values())))
        checks.append(("calculation directory accepted", not errors))
        if case.mismatch_slots:
            reported = any("mapping mismatch" in finding.message for finding in warns)
            slots = nominal_mismatch_slots(judgement["values"])
            checks.append(("mapping mismatch reported on slot(s) %s"
                           % ", ".join(str(index) for index in case.mismatch_slots),
                           reported and slots == list(case.mismatch_slots)))
    elif kind == "fatal":
        checks.extend(fatal_checks(observation, judgement))
    elif kind == "fatal_and_handshake":
        checks.extend(fatal_checks(observation, judgement))
        checks.append(("harness rejects the directory (%s)" % pattern, has_pattern(errors, pattern)))
    elif kind == "handshake":
        checks.append(("harness rejects the directory (%s)" % pattern, has_pattern(errors, pattern)))
    elif kind == "detect_output_fault":
        checks.append((
            "harness rejects the faulty adapter output",
            (not observation["timed_out"]) and not judgement["ok"]
            and (case.problem_contains or "") in (judgement["problem"] or ""),
        ))
    else:
        raise HarnessError("unknown case kind %r" % kind)

    unmet = [label for label, flag in checks if not flag]
    detail_parts = list(observation["danger"])
    detail_parts.extend("WARN: " + finding.message for finding in warns)
    detail_parts.extend("ERROR: " + finding.message for finding in errors)
    if unmet:
        detail_parts.append("unmet: " + "; ".join(unmet))
    return (not unmet, expected, describe_observation(observation), " | ".join(detail_parts))


def run_cases(args, workdir: Path) -> dict:
    """Run every selected case inside ``workdir`` and return the report payload.

    The caller owns ``workdir`` (creation and cleanup); this keeps the temp-tree
    lifetime visible to ``main`` for ``--keep-tmp``.
    """
    workdir = Path(workdir).resolve()
    adapters_dir = workdir / "adapters"
    adapters_dir.mkdir(parents=True, exist_ok=True)
    python_exe = resolve_python(getattr(args, "python", None), caller_base_dir(args))
    selected = resolve_cases(getattr(args, "case", None))
    compat = bool(getattr(args, "compat_legacy", False))
    require_fresh = bool(getattr(args, "require_fresh", False))
    allow_skip = bool(getattr(args, "allow_skip", False))
    timeout = float(getattr(args, "timeout", DEFAULT_TIMEOUT))

    adapter_given, adapter_resolved = resolve_adapter(args)
    user_adapter = adapter_given is not None
    if user_adapter:
        adapter_path = Path(adapter_resolved)
        adapter_label = str(adapter_given)
    elif compat:
        adapter_path = adapters_dir / "legacy_compatible_adapter.py"
        write_text(adapter_path, LEGACY_ADAPTER)
        adapter_label = ADAPTER_LABEL_LEGACY
    else:
        adapter_path = adapters_dir / "reference_adapter.py"
        write_text(adapter_path, STRICT_ADAPTER)
        adapter_label = ADAPTER_LABEL_STRICT
    adapter_path = Path(adapter_path).resolve()

    payload_cases = []
    for case in selected:
        if user_adapter and case.skip_with_adapter:
            payload_cases.append({
                "name": case.name,
                "expected": case.expected,
                "observed": "not run",
                "result": "skip",
                "detail": case.skip_with_adapter,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "argv": None,
            })
            continue
        payload_cases.append(
            run_one_case(case, adapter_path, adapters_dir, python_exe, workdir, timeout,
                         require_fresh, compat, user_adapter)
        )

    passed = sum(1 for entry in payload_cases if entry["result"] == "pass")
    failed = sum(1 for entry in payload_cases if entry["result"] == "fail")
    skipped = sum(1 for entry in payload_cases if entry["result"] == "skip")
    characterized = sum(1 for entry in payload_cases if entry["result"] == "characterized")
    first_argv = next((entry["argv"] for entry in payload_cases if entry["argv"]), [])
    return {
        "schema": SCHEMA,
        "adapter": adapter_label,
        "adapter_resolved": str(adapter_path),
        "compat_legacy": compat,
        "require_fresh": require_fresh,
        "python": str(python_exe),
        "argv": list(first_argv),
        "cases": payload_cases,
        "summary": {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "characterized": characterized,
        },
        "ok": failed == 0 and (skipped == 0 or allow_skip),
    }


def run_one_case(case: CaseSpec, adapter_path: Path, adapters_dir: Path, python_exe: Path,
                 workdir: Path, timeout: float, require_fresh: bool, compat: bool,
                 user_adapter: bool = False) -> dict:
    """Run one case and convert unexpected failures into an internal-error result."""
    try:
        case_adapter = adapter_path
        if case.probe is not None:
            case_adapter = adapters_dir / ("probe_%s.py" % case.name)
            write_text(case_adapter, case.probe)
        observation = run_case(case, case_adapter, python_exe, workdir, timeout, require_fresh)
        ok, expected, observed, detail = evaluate_case(case, observation, require_fresh)
        if ok:
            result = "pass"
        elif compat:
            # --compat-legacy characterises instead of failing: the embedded
            # legacy adapter never refuses a misconfiguration, and a user adapter
            # run with --adapter --compat-legacy is characterised the same way so
            # its behaviour in a rejected state is still visible.  The marker
            # stays its own detail segment so the DANGEROUS and WARN segments
            # after it are still reported separately.
            result = "characterized"
            if user_adapter:
                marker = ("characterized (--compat-legacy): the adapter under test did not meet the "
                          "strict contract on this case; characterized instead of failed")
            else:
                marker = ("characterized (--compat-legacy): the legacy fallback accepted a state "
                          "the contract rejects")
            detail = marker + (" | " + detail if detail else "")
        else:
            result = "fail"
        return {
            "name": case.name,
            "expected": expected,
            "observed": observed,
            "result": result,
            "detail": detail,
            "exit_code": observation["exit_code"],
            "stdout": observation["stdout"],
            "stderr": observation["stderr"],
            "argv": list(observation["argv"]),
        }
    except Exception as exc:  # noqa: BLE001 - reported per case, never swallowed
        return {
            "name": case.name,
            "expected": case.expected,
            "observed": "harness error",
            "result": "fail",
            "detail": INTERNAL_ERROR_PREFIX + "%s: %s" % (type(exc).__name__, exc),
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "argv": None,
        }


def run_smoke(args) -> dict:
    """Create a private temp tree, run every selected case and clean up."""
    workdir = Path(tempfile.mkdtemp(prefix="uspex-legacy105-smoke-"))
    try:
        return run_cases(args, workdir)
    finally:
        if not getattr(args, "keep_tmp", False):
            shutil.rmtree(str(workdir), ignore_errors=True)


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write the report atomically: temp file in the target directory + os.replace."""
    path = Path(path)
    parent = path.parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=str(parent),
            prefix=path.name + ".", suffix=".tmp", delete=False,
        )
        tmp_path = Path(handle.name)
        with handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(str(tmp_path), str(path))
    except OSError:
        if tmp_path is not None:
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
        raise


def clip(text, width: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def format_table(rows) -> str:
    headers = ("CASE", "EXPECTED", "OBSERVED", "RESULT")
    case_width = max([len(headers[0])] + [len(row[0]) for row in rows]) if rows else len(headers[0])
    widths = (case_width, 52, 48, 13)
    lines = [
        "%-*s  %-*s  %-*s  %-*s" % (
            widths[0], headers[0], widths[1], headers[1], widths[2], headers[2], widths[3], headers[3]
        ),
        "-" * (sum(widths) + 6),
    ]
    for name, expected, observed, result in rows:
        lines.append(
            "%-*s  %-*s  %-*s  %-*s" % (
                widths[0], clip(name, widths[0]),
                widths[1], clip(expected, widths[1]),
                widths[2], clip(observed, widths[2]),
                widths[3], result,
            )
        )
    return "\n".join(lines)


HUMAN_RESULT = {
    "pass": "PASS",
    "fail": "FAIL",
    "skip": "SKIP",
    "characterized": "CHARACTERIZED",
}


def report(payload: dict, args) -> None:
    print(
        "Legacy 10.5 custom-property smoke: %d case(s), adapter=%s, compat_legacy=%s, "
        "require_fresh=%s, timeout=%.1fs"
        % (
            len(payload["cases"]),
            payload["adapter"],
            payload["compat_legacy"],
            payload["require_fresh"],
            float(getattr(args, "timeout", DEFAULT_TIMEOUT)),
        )
    )
    if payload["compat_legacy"]:
        print(
            "NOTE: --compat-legacy characterises the legacy fallback-to-default behaviour; "
            "CHARACTERIZED means the legacy adapter did NOT meet the contract in EXPECTED."
        )
    rows = [
        (entry["name"], entry["expected"], entry["observed"], HUMAN_RESULT[entry["result"]])
        for entry in payload["cases"]
    ]
    print(format_table(rows))
    for entry in payload["cases"]:
        for segment in entry["detail"].split(" | "):
            if segment.startswith("DANGEROUS: "):
                print("DANGER %s: %s" % (entry["name"], segment[len("DANGEROUS: "):]))
            elif segment.startswith("WARN: "):
                print("WARN %s: %s" % (entry["name"], segment[len("WARN: "):]))
    if getattr(args, "verbose", False):
        for entry in payload["cases"]:
            print("[verbose] %s: result=%s exit_code=%s" % (entry["name"], entry["result"], entry["exit_code"]))
            print("[verbose]   stdout=%r" % clip(entry["stdout"], 400))
            print("[verbose]   stderr=%r" % clip(entry["stderr"], 400))
            if entry["detail"]:
                print("[verbose]   detail=%s" % entry["detail"])
    summary = payload["summary"]
    line = "SUMMARY: %d passed, %d failed, %d skipped" % (
        summary["passed"], summary["failed"], summary["skipped"]
    )
    if summary["characterized"]:
        line += " (%d characterized)" % summary["characterized"]
    print(line)
    failed_names = [entry["name"] for entry in payload["cases"] if entry["result"] == "fail"]
    skipped_names = [entry["name"] for entry in payload["cases"] if entry["result"] == "skip"]
    if failed_names:
        print("FAIL: %s" % ", ".join(failed_names), file=sys.stderr)
    if skipped_names and not getattr(args, "allow_skip", False):
        print("SKIPPED (use --allow-skip to accept): %s" % ", ".join(skipped_names), file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--adapter",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "run this user-owned adapter instead of the embedded reference adapters; the same "
            "scenarios and assertions are used. PATH is resolved against the directory the "
            "command was started in and that absolute path is what each case runs, so a relative "
            "PATH works even though every case uses its own scratch working directory. A PATH "
            "that does not exist is a usage error (exit 2) naming the given and the resolved "
            "path. Cases that cannot be expressed for a foreign adapter (harness self-check "
            "probes, the optional model_path case) are reported as SKIP with a reason. May be "
            "combined with --compat-legacy, which characterises this adapter instead of the "
            "embedded legacy one"
        ),
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help=(
            "interpreter used to run the adapter (default: the interpreter running this script); "
            "a relative interpreter path is resolved against the directory the command was "
            "started in"
        ),
    )
    parser.add_argument(
        "--compat-legacy",
        action="store_true",
        help=(
            "report every state the strict contract rejects as CHARACTERIZED instead of a "
            "contract violation, flagging the silent-zero fallback explicitly. Alone it tests "
            "the embedded legacy-compatible adapter (default 0.0 on any read failure); combined "
            "with --adapter it keeps that user-owned adapter and only changes the reporting, so "
            "its behaviour in a rejected state is characterised rather than failed"
        ),
    )
    parser.add_argument(
        "--require-fresh",
        action="store_true",
        help=(
            "fail instead of WARN when a P*.txt is older than geom.in, i.e. when a previous "
            "run's value may still be in place"
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "write the machine-readable report to PATH (atomically, via a temp file and "
            "os.replace). Schema uspex-legacy105-smoke/2 records the adapter path as given, the "
            "absolute path actually executed and the exact command line of every case"
        ),
    )
    parser.add_argument(
        "--keep-tmp",
        action="store_true",
        help="keep the temp scratch tree and print its path instead of deleting it",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="NAME",
        help="run only the named case; repeatable. Use --list-cases for the valid names",
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help=(
            "accept SKIP results for the exit code; without it a SKIP (for example the harness "
            "self-check probes under --adapter) makes the exit code non-zero so nothing is "
            "skipped silently"
        ),
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="print one case name per line and exit 0",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print per-case exit codes, captured stdout/stderr and the full detail",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="per-invocation timeout in seconds; a timeout is reported as a case failure",
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_cases:
        for case in CASE_SPECS:
            print(case.name)
        return 0

    # Captured before any scratch directory exists: every relative path option is
    # resolved against the directory the user started the command in, never
    # against a per-case scratch directory.
    args.base_dir = Path(os.getcwd())

    try:
        if args.timeout <= 0:
            raise UsageError("--timeout must be positive")
        resolve_cases(args.case)
        args.python = resolve_python(args.python, args.base_dir)
        given, resolved = resolve_adapter(args)
        if given is not None:
            # Keep the path as given for the report and pass the absolute path to
            # every subprocess, whose working directory is a case scratch dir.
            args.adapter = given
            args.adapter_resolved = resolved
    except UsageError as exc:
        print("usage error: %s" % exc, file=sys.stderr)
        return 2

    workdir = None
    try:
        workdir = Path(tempfile.mkdtemp(prefix="uspex-legacy105-smoke-"))
        payload = run_cases(args, workdir)
    except UsageError as exc:
        print("usage error: %s" % exc, file=sys.stderr)
        return 2
    except HarnessError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 3
    except OSError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    finally:
        if workdir is not None:
            if args.keep_tmp:
                print("kept temp workdir: %s" % workdir)
            else:
                shutil.rmtree(str(workdir), ignore_errors=True)

    report(payload, args)

    if args.json is not None:
        try:
            write_json_atomic(args.json, payload)
        except OSError as exc:
            print("ERROR: cannot write --json output to %s: %s" % (args.json, exc), file=sys.stderr)
            return 2

    if any(entry["detail"].startswith(INTERNAL_ERROR_PREFIX) for entry in payload["cases"]):
        return 3
    if payload["summary"]["failed"]:
        return 1
    if payload["summary"]["skipped"] and not args.allow_skip:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
