#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reference legacy property adapter for a patched USPEX 10.5 installation.

Copy this file into an isolated, documented USPEX 10.5 tree as

    <USPEX>/application/archive/src/FunctionFolder/calcElasticML.py

after backing up the shipped file and recording its SHA-256. USPEX invokes that
path for ``optType`` values ``1201``-``1207``; it expects the file to print

    <CALLRESULT> <slot 1> <slot 2> ... <slot 7>

on one stdout line, with a fixed order. Slot *i* is the value that ``optType 12i``
selects. The slot labels are project-defined: this adapter never needs to know what
property a slot carries, only that the value is one finite scalar per file.

Division of labour (keep it this way):

    structure -> your DFT / ML / scientific program -> scalar -> P<i>.txt
                                                                     |
                                       this adapter (transport only) |
                                                                     v
                                          <CALLRESULT> P1 ... P7 -> patched USPEX

Read the slot files from the *calculation directory*, never from this file's own
directory: USPEX runs one structure per ``CalcFold*`` directory and reuses those
directories between generations.

Contract enforced here (see references/custom-objectives-105.md):

  * the calculation directory is resolved from the structure-path argument, and a
    bare relative filename is accepted only when the working directory really holds
    that structure file -- otherwise the adapter aborts instead of scoring slot
    files that belong to some other directory;
  * a missing, empty, unreadable, non-numeric, non-finite or multi-token slot file
    is fatal: the adapter prints a diagnostic to stderr and exits 2 **without**
    printing a ``<CALLRESULT>`` line, so USPEX sees no result rather than a fake
    one, unless that exact slot was declared optional with ``--allow-default``;
  * a finite negative value is legal and passes through unchanged, because it can be
    a deliberate direction-aware penalty;
  * the number of emitted values is fixed and never reduced, so slot order cannot
    silently shift when one property is unavailable.

Fail-loudly is *recommended hardening*. The original teaching adapter substituted a
default of ``0.0`` for an unreadable slot, which is the best possible value for a
minimization objective and can therefore attract the search to a broken structure.
If you must reproduce that behaviour while migrating, name every slot explicitly:
``--allow-default P1.txt=0 --allow-default P2.txt=0 ...``.

Exit codes: 0 success, 2 a fatal contract violation (missing/malformed slot, or an
unresolvable calculation directory), 3 unusable command line.

Usage (USPEX calls this form; the second argument is reserved, accepted and
ignored, matching the shipped calculator's ``<structure_path> [model_path]``
invocation):

    python calcElasticML.py <structure_path> [model_path]

Manual use:

    python calcElasticML_adapter_template.py <structure_path> --print-slots
    python calcElasticML_adapter_template.py <structure_path> --allow-default P5.txt=0
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

LABEL = "<CALLRESULT>"
SLOT_FILES = ("P1.txt", "P2.txt", "P3.txt", "P4.txt", "P5.txt", "P6.txt", "P7.txt")

EXIT_OK = 0
EXIT_FATAL = 2
EXIT_USAGE = 3


class SlotError(RuntimeError):
    """A slot file is present but unusable, or the directory cannot be resolved."""


def resolve_calc_dir(structure_path: str) -> Path:
    """Return the calculation directory that owns both the structure and the slots.

    An argument carrying a directory component always resolves beside itself. A bare
    relative filename would otherwise collapse to the process working directory, so
    that collapse is accepted only when the working directory really holds the
    structure file; anything else is an error rather than a silent wrong read.
    """
    raw = Path(structure_path)
    if raw.parent == Path("."):
        candidate = Path.cwd()
        if not candidate.joinpath(structure_path).is_file():
            raise SlotError(
                f"{structure_path!r} has no directory component and "
                f"{candidate} does not contain it; refusing to guess the slot directory"
            )
        return candidate

    candidate = Path(os.path.abspath(structure_path)).parent
    if not candidate.is_dir():
        raise SlotError(f"not a directory: {candidate}")
    return candidate


def parse_scalar(text: str, source: Path) -> float:
    """Parse exactly one finite scalar from a slot file."""
    stripped = text.strip()
    if not stripped:
        raise SlotError(f"{source.name}: empty file")
    fields = stripped.split()
    if len(fields) != 1:
        raise SlotError(
            f"{source.name}: expected exactly one value, found {len(fields)} "
            "(a property file must never carry a header, a log line or a unit)"
        )
    token = fields[0]
    try:
        value = float(token)
    except ValueError:
        raise SlotError(f"{source.name}: {token!r} is not a number") from None
    if not math.isfinite(value):
        raise SlotError(f"{source.name}: {value!r} is not finite")
    return value


def read_slot(calc_dir: Path, filename: str) -> float:
    """One finite scalar from ``filename``, or raise. Never returns a default."""
    path = calc_dir / filename
    if not path.is_file():
        raise SlotError(f"{filename}: not found in {calc_dir}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SlotError(f"{filename}: cannot read ({exc})") from exc
    return parse_scalar(text, path)


def parse_defaults(items: list[str]) -> dict[str, float]:
    """Turn ``--allow-default Pi.txt=VALUE`` into a per-slot default map."""
    defaults: dict[str, float] = {}
    for item in items:
        name, sep, raw = item.partition("=")
        if not sep or name not in SLOT_FILES:
            raise SlotError(
                f"--allow-default wants one of {' '.join(SLOT_FILES)} followed by "
                f"'=VALUE', got {item!r}"
            )
        try:
            value = float(raw)
        except ValueError:
            raise SlotError(f"--allow-default {item!r}: {raw!r} is not a number") from None
        if not math.isfinite(value):
            raise SlotError(f"--allow-default {item!r}: {raw!r} is not finite")
        defaults[name] = value
    return defaults


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy USPEX 10.5 property adapter: read one scalar per slot file and "
            "print one fixed-order <CALLRESULT> line. CONTRACT: the calculation "
            "directory comes from the structure path, and exactly seven values are "
            "emitted in slot order. RECOMMENDED HARDENING: a broken slot is fatal "
            "(exit 2, no <CALLRESULT>) unless declared with --allow-default, and the "
            "resolved slot directory is logged."
        )
    )
    parser.add_argument("structure_path", help="structure file path passed by USPEX")
    parser.add_argument("model_path", nargs="?", default=None,
                        help="second argument of the shipped calculator; accepted and ignored")
    parser.add_argument("--allow-default", action="append", default=[], metavar="Pi.txt=VALUE",
                        help="declare that exact slot optional and give its finite fallback; "
                             "repeatable. Without it, an unusable slot is fatal.")
    parser.add_argument("--print-slots", action="store_true",
                        help="also print slot=value diagnostics to stderr")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        defaults = parse_defaults(args.allow_default)
    except SlotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        calc_dir = resolve_calc_dir(args.structure_path)
        # Always logged: a silent directory read is how a wrong structure gets scored.
        print(f"[adapter] slot directory: {calc_dir}", file=sys.stderr)

        values = []
        fatal = []
        for name in SLOT_FILES:
            try:
                values.append(read_slot(calc_dir, name))
            except SlotError as exc:
                if name not in defaults:
                    fatal.append(str(exc))
                    continue
                print(f"[adapter] {exc}; using declared default {defaults[name]!r}",
                      file=sys.stderr)
                values.append(defaults[name])
    except SlotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_FATAL

    if fatal:
        for message in fatal:
            print(f"ERROR: {message}", file=sys.stderr)
        print("Refusing to emit <CALLRESULT>: a missing or malformed property must not "
              "reach the search as a valid value.", file=sys.stderr)
        return EXIT_FATAL

    if args.print_slots:
        for name, value in zip(SLOT_FILES, values):
            print(f"[adapter] {name} = {value!r}", file=sys.stderr)

    print(f"{LABEL} " + " ".join(str(value) for value in values))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
