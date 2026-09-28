#!/usr/bin/env python3
"""Run and validate one native USPEX 10.6 code-99 calculation directory.

The checker runs an optional external-engine ``--command``, requires every
``--required-file`` output to exist and be non-empty (and, with
``--require-fresh``, to be refreshed by that command), then invokes the
``user_property_calculator.py`` callback with flags ``0``, ``1`` and each
``--property-flags`` value, parsing exactly one ``<CALLRESULT> <value>`` line
per invocation.

Two real production failure modes are covered explicitly:

``--penalty VALUE``
    A documented penalty sentinel such as ``-10`` for "no valid Li site". A
    smoke run that only ever sees that sentinel never exercised the property
    path, so a ``--property-flags`` value matching a declared penalty fails the
    smoke. Flags ``0`` and ``1`` are never matched. The option is repeatable and
    the comparison is tight (``abs_tol=1e-9``), so ``-10.0`` matches ``-10``.

``--consume-geom-out``
    USPEX 10.6 may consume ``geom.out`` before the X/Y property callback reads
    it, which used to crash a real external-calculator run with
    ``FileNotFoundError``. This flag moves ``run_dir/geom.out`` aside to
    ``geom.out.consumed-by-smoke`` before the callback flags run, requires those
    flags to still succeed (which only holds when the calculator wrapper kept a
    preserved copy), checks ``--preserved`` (default ``relaxed_geom.out``) as a
    parseable VASP-5 POSCAR, and finally restores the original ``geom.out`` even
    on failure. It needs no ``--command``; without one, ``run_dir/geom.out`` must
    already exist, while with one the engine may create it (freshness is checked
    before it is consumed).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path


CALLRESULT_RE = re.compile(
    r"^\s*<CALLRESULT>\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)\s*$"
)

GEOM_OUT = "geom.out"
CONSUMED_GEOM_OUT = "geom.out.consumed-by-smoke"
DEFAULT_PRESERVED = "relaxed_geom.out"
# Tight comparison so a declared "-10" sentinel matches a reported "-10.0".
PENALTY_ABS_TOL = 1e-9


class SmokeFailure(RuntimeError):
    """A code-99 contract check failed."""


def poscar_species_count(path: Path) -> int:
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    if len(lines) < 7:
        raise SmokeFailure(f"{path} is too short to be a VASP-5 POSCAR")
    species = lines[5].split()
    counts = lines[6].split()
    if not species or len(species) != len(counts):
        raise SmokeFailure(f"{path} has no parseable VASP-5 species/count lines")
    try:
        parsed_counts = [int(value) for value in counts]
    except ValueError as exc:
        raise SmokeFailure(f"{path} contains non-integer species counts") from exc
    return sum(value > 0 for value in parsed_counts)


def parse_callresult(stdout: str, flag: int) -> float:
    matches = [CALLRESULT_RE.fullmatch(line) for line in stdout.splitlines()]
    values = [float(match.group(1)) for match in matches if match is not None]
    if len(values) != 1:
        raise SmokeFailure(
            f"callback flag {flag} must emit exactly one '<CALLRESULT> <value>' line; "
            f"found {len(values)}"
        )
    value = values[0]
    if not math.isfinite(value):
        raise SmokeFailure(f"callback flag {flag} returned non-finite value {value}")
    return value


def run_callback(python: Path, callback: Path, run_dir: Path, flag: int, step: int) -> float:
    proc = subprocess.run(
        [str(python), str(callback), str(flag), str(step)],
        cwd=run_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise SmokeFailure(f"callback flag {flag} exited {proc.returncode}: {detail}")
    return parse_callresult(proc.stdout, flag)


def check_penalties(property_flags: list[int], values: dict[str, float], penalties: list[float]) -> None:
    """Reject a property flag whose value is a declared "path not exercised" sentinel."""
    if not penalties:
        return
    for flag in property_flags:
        if flag == 0 or flag == 1:
            # Flags 0 and 1 carry convergence/energy semantics, never a property sentinel.
            continue
        value = values[str(flag)]
        if any(math.isclose(value, penalty, rel_tol=0.0, abs_tol=PENALTY_ABS_TOL) for penalty in penalties):
            raise SmokeFailure(
                f"property flag {flag} returned the declared penalty sentinel {value!r} "
                f"(declared penalties: {penalties}); the property path returned the project's "
                "penalty sentinel and therefore was not actually exercised, so this smoke run "
                "does not validate the real objective"
            )


def require_outputs(run_dir: Path, names: list[str], started_ns: int | None) -> None:
    for name in names:
        path = run_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SmokeFailure(f"required output is missing or empty: {path}")
        if started_ns is not None and path.stat().st_mtime_ns < started_ns:
            raise SmokeFailure(f"required output was not refreshed by this smoke run: {path}")


def run_smoke(args: argparse.Namespace) -> dict[str, object]:
    run_dir = args.run_dir.resolve()
    if not run_dir.is_dir():
        raise SmokeFailure(f"run directory does not exist: {run_dir}")

    geom_input = run_dir / args.geom_input
    if not geom_input.is_file():
        raise SmokeFailure(f"geometry input is missing: {geom_input}")
    species_count = poscar_species_count(geom_input)
    if species_count < args.min_species:
        raise SmokeFailure(
            f"{geom_input.name} contains {species_count} populated species; "
            f"this smoke requires at least {args.min_species}"
        )

    started_ns: int | None = None
    engine_seconds: float | None = None
    if args.command:
        started_ns = time.time_ns()
        started = time.monotonic()
        proc = subprocess.run(
            args.command,
            cwd=run_dir,
            shell=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        engine_seconds = time.monotonic() - started
        if proc.returncode != 0:
            raise SmokeFailure(f"external code command exited {proc.returncode}")

    require_outputs(run_dir, args.required_file, started_ns if args.require_fresh else None)

    callback = args.callback
    if not callback.is_absolute():
        callback = run_dir / callback
    if not callback.is_file():
        raise SmokeFailure(f"callback is missing: {callback}")

    # Newer options are read defensively so callers that build a Namespace by hand
    # (older test helpers) keep the pre-penalty, non-consuming behaviour.
    penalties = list(getattr(args, "penalty", None) or [])
    consume_geom_out = bool(getattr(args, "consume_geom_out", False))
    preserved_name = getattr(args, "preserved", None) or DEFAULT_PRESERVED

    geom_out = run_dir / GEOM_OUT
    stash = run_dir / CONSUMED_GEOM_OUT
    preserved_path = run_dir / preserved_name
    if consume_geom_out and not geom_out.is_file():
        raise SmokeFailure(f"--consume-geom-out requires an existing geometry output: {geom_out}")

    flags = [0, 1, *args.property_flags]
    preserved_species: int | None = None
    try:
        if consume_geom_out:
            # Simulate USPEX 10.6 consuming geom.out before the property callback reads it.
            geom_out.replace(stash)
        values = {
            str(flag): run_callback(args.python, callback, run_dir, flag, args.step)
            for flag in flags
        }
        if values["0"] == 0:
            raise SmokeFailure("callback flag 0 reports that the calculation is not converged")
        check_penalties(args.property_flags, values, penalties)
        if consume_geom_out:
            if not preserved_path.is_file() or preserved_path.stat().st_size == 0:
                raise SmokeFailure(
                    f"callback flags succeeded without {geom_out.name}, but the wrapper left no "
                    f"preserved geometry copy: {preserved_path}"
                )
            preserved_species = poscar_species_count(preserved_path)
            if preserved_species < args.min_species:
                raise SmokeFailure(
                    f"{preserved_path.name} contains {preserved_species} populated species; "
                    f"this smoke requires at least {args.min_species}"
                )
    finally:
        # Leave the directory as found, even when the callback or a check failed.
        if consume_geom_out and stash.exists():
            stash.replace(geom_out)

    return {
        "status": "pass",
        "run_dir": str(run_dir),
        "species_count": species_count,
        "engine_seconds": engine_seconds,
        "required_files": args.required_file,
        "callback_values": values,
        "declared_penalties": penalties,
        "property_values": {str(flag): values[str(flag)] for flag in args.property_flags},
        "consume_geom_out": consume_geom_out,
        "preserved_file": (
            {
                "name": preserved_name,
                "path": str(preserved_path),
                "species_count": preserved_species,
            }
            if consume_geom_out
            else None
        ),
        "note": "finite penalty sentinels are reported as values; scientific acceptance remains project-specific",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="one CalcFold-like directory containing geom.in")
    parser.add_argument(
        "--command",
        help="external-engine command to run first, using the same shell syntax as commandExecutable",
    )
    parser.add_argument(
        "--callback",
        type=Path,
        default=Path("user_property_calculator.py"),
        help="callback path, relative to run_dir unless absolute",
    )
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--property-flags",
        type=int,
        nargs="+",
        default=[101],
        help="property flags to exercise in addition to flags 0 and 1, e.g. 101 (X) and 102 (Y)",
    )
    parser.add_argument(
        "--penalty",
        type=float,
        action="append",
        default=[],
        metavar="VALUE",
        help=(
            "documented penalty sentinel of this project, e.g. -10 for 'no valid Li site'; repeat for "
            "several sentinels. A --property-flags value equal to a declared sentinel fails the smoke, "
            "because that means the property path was never actually exercised. Flags 0 and 1 are never "
            "matched. Default: none declared, so finite values are merely reported"
        ),
    )
    parser.add_argument(
        "--consume-geom-out",
        action="store_true",
        help=(
            "simulate USPEX 10.6 consuming run_dir/geom.out by moving it to geom.out.consumed-by-smoke "
            "before the callback flags run; the flags must still succeed, and run_dir/<--preserved> must "
            "then be a parseable VASP-5 POSCAR. geom.out is restored afterwards, even on failure. Needs "
            "no --command, but then run_dir/geom.out must already exist; with --command the engine may "
            "create it, and freshness is checked before it is consumed"
        ),
    )
    parser.add_argument(
        "--preserved",
        default=DEFAULT_PRESERVED,
        metavar="NAME",
        help=(
            "geometry copy the callback wrapper must leave behind for the property callback when "
            "--consume-geom-out is used"
        ),
    )
    parser.add_argument(
        "--required-file",
        action="append",
        default=[],
        help="output that must exist and be non-empty; repeat for multiple files",
    )
    parser.add_argument(
        "--require-fresh",
        action="store_true",
        help="require every --required-file to be refreshed by --command",
    )
    parser.add_argument(
        "--min-species",
        type=int,
        default=1,
        help=(
            "minimum populated species required in geom.in (and in the preserved copy when "
            "--consume-geom-out is used); use 2+ for composition-dependent properties"
        ),
    )
    parser.add_argument("--geom-input", default="geom.in")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    if args.min_species <= 0 or args.step <= 0 or args.timeout <= 0:
        parser.error("--min-species, --step, and --timeout must be positive")
    if args.require_fresh and not args.command:
        parser.error("--require-fresh requires --command")
    if args.consume_geom_out and not args.command:
        # Without --command nothing can create geom.out, so a missing one is a usage error.
        # With --command the engine may legitimately produce it, so run_smoke checks after it ran.
        geom_out = args.run_dir / GEOM_OUT
        if args.run_dir.is_dir() and not geom_out.is_file():
            parser.error(f"--consume-geom-out requires an existing {GEOM_OUT}: {geom_out}")

    try:
        result = run_smoke(args)
    except (SmokeFailure, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
