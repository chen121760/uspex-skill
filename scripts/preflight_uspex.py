#!/usr/bin/env python3
"""Static preflight checks for USPEX 10.5/10.6 calculation directories."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Finding:
    level: str
    message: str


@dataclass
class ParsedInput:
    scalars: dict[str, str]
    blocks: dict[str, list[str]]


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_input(path: Path) -> ParsedInput:
    scalars: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    current: str | None = None

    text = path.read_text(encoding="utf-8", errors="replace")
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("*"):
            continue
        if stripped.startswith("%"):
            marker_text = stripped[1:].strip().split("#", 1)[0].strip()
            marker = _normalise(marker_text)
            is_end = marker.startswith("end") or marker.endswith("end")
            if current is not None and is_end:
                current = None
            elif current is None and marker and not is_end:
                current = marker
                blocks.setdefault(current, [])
            continue

        if current is not None:
            blocks[current].append(stripped)
            continue

        match = re.match(r"^(.*?)\s*:\s*([A-Za-z][A-Za-z0-9_]*)", stripped)
        if match:
            value, key = match.groups()
            scalars[_normalise(key)] = value.strip()

    return ParsedInput(scalars=scalars, blocks=blocks)


def _without_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def block_lines(parsed: ParsedInput, name: str) -> list[str]:
    lines = parsed.blocks.get(_normalise(name), [])
    if _normalise(name) == "commandexecutable":
        return [line.strip() for line in lines if line.strip()]
    return [clean for line in lines if (clean := _without_comment(line))]


def block_tokens(parsed: ParsedInput, name: str) -> list[str]:
    return [token for line in block_lines(parsed, name) for token in line.split()]


def scalar(parsed: ParsedInput, name: str) -> str | None:
    return parsed.scalars.get(_normalise(name))


def _int_scalar(parsed: ParsedInput, name: str) -> int | None:
    value = scalar(parsed, name)
    if value is None:
        return None
    match = re.search(r"[-+]?\d+", value)
    return int(match.group(0)) if match else None


def _float_scalar(parsed: ParsedInput, name: str) -> float | None:
    value = scalar(parsed, name)
    if value is None:
        return None
    try:
        return float(value.split()[0])
    except (ValueError, IndexError):
        return None


def _has_magnetic_suffix(raw: str | None) -> bool:
    """USPEX 10.6 manual 4.1: calculationType may carry 's'/'S' to enable magnetism.

    The manual shows the flag before the indices ('s300', 's201') and 4.1 lists it as a
    trailing option ('s' or 'S'); accept either position.
    """
    if raw is None:
        return False
    return bool(re.search(r"[sS]\s*\d|\d\s*[sS]", raw.strip()))


def _casefold_files(directory: Path) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {item.name.casefold(): item for item in directory.iterdir()}


class Checker:
    def __init__(self, root: Path, version: str):
        self.root = root
        self.version = version
        self.findings: list[Finding] = []

    def error(self, message: str) -> None:
        self.findings.append(Finding("ERROR", message))

    def warn(self, message: str) -> None:
        self.findings.append(Finding("WARN", message))

    def info(self, message: str) -> None:
        self.findings.append(Finding("INFO", message))

    def check(self) -> list[Finding]:
        input_path = self.root / "INPUT.txt"
        if not input_path.is_file():
            self.error(f"missing {input_path}")
            return self.findings

        parsed = parse_input(input_path)
        calc_type = _int_scalar(parsed, "calculationType")
        if calc_type is None:
            self.error("calculationType is missing or not an integer")
        elif calc_type not in {300, 301, -200, -201, 200, 201}:
            self.warn(
                f"calculationType {calc_type} is outside this skill's audited bulk/2D/surface scope"
            )

        if _has_magnetic_suffix(scalar(parsed, "calculationType")):
            self.info(
                f"calculationType carries the manual's magnetic suffix (s/S); the numeric part is "
                f"{calc_type}, so the calculation runs as that type with spin. Magnetic searches "
                "evaluate every structure as NM/FM/AFM, change the meaning of the energy ranking, "
                "and are steered by fracSpinMut and magRatio (manual 4.1/4.4). Confirm both are set "
                "deliberately and that the magnetic policy is recorded with the results."
            )

        atoms = block_tokens(parsed, "atomType")
        if not atoms:
            self.error("atomType block is missing or empty")

        self._check_composition(parsed, atoms, calc_type)
        self._check_geometry(parsed, atoms, calc_type)
        self._check_variation(parsed, atoms, calc_type)
        self._check_objectives(parsed)
        self._check_stages(parsed, atoms)
        self._check_submission(parsed)
        self._check_seeds(parsed)
        self._check_pressure(parsed)
        return self.findings

    def _check_composition(
        self, parsed: ParsedInput, atoms: list[str], calc_type: int | None
    ) -> None:
        rows = block_lines(parsed, "numSpecies")
        if not rows:
            self.error("numSpecies block is missing or empty")
            return
        for index, row in enumerate(rows, 1):
            tokens = row.split()
            if atoms and len(tokens) != len(atoms):
                self.error(
                    f"numSpecies row {index} has {len(tokens)} entries; atomType has {len(atoms)}"
                )
            for token in tokens:
                try:
                    value = int(token)
                except ValueError:
                    self.error(f"numSpecies row {index} contains non-integer {token!r}")
                    continue
                if value < 0:
                    self.error(f"numSpecies row {index} contains negative entry {value}")

        if calc_type in {300, -200, 200} and len(rows) != 1:
            self.warn(
                f"fixed/single-block calculationType {calc_type} usually has one numSpecies row; found {len(rows)}"
            )
        if calc_type in {-200, -201, 200, 201} and len(atoms) > 2:
            self.warn(
                "ternary/quaternary 2D or surface scope is not assumed supported; verify the exact release with a reduced official-example test"
            )

    def _check_variation(
        self, parsed: ParsedInput, atoms: list[str], calc_type: int | None
    ) -> None:
        fraction_names = (
            "fracGene",
            "fracRand",
            "fracTopRand",
            "fracAtomsMut",
            "fracTrans",
            "fracLatMut",
            "fracPerm",
            "fracRotMut",
            "fracSpinMut",
            "fracDB",
        )
        present: dict[str, float] = {}
        for name in fraction_names:
            raw = scalar(parsed, name)
            if raw is None:
                continue
            value = _float_scalar(parsed, name)
            if value is None or not math.isfinite(value):
                self.error(f"{name} must be a finite number, got {raw!r}")
                continue
            if value < 0 or value > 1:
                self.error(f"{name} must lie between 0 and 1, got {value}")
            present[name] = value

        if present:
            total = sum(present.values())
            # USPEX normalises the frac* family (manual 4.4 NOTE), so a sum other than
            # 1.0 is legal: it rescales the intended mix. Report the realised shares so
            # the author can confirm the ratio, and never present it as a defect.
            if total > 1.02 or (len(present) >= 5 and 0 < total < 0.98):
                shares = ", ".join(f"{name}={value / total:.3f}" for name, value in present.items())
                self.info(
                    "explicit variation fractions sum to "
                    f"{total:.6g}; USPEX normalises them, so the effective shares are {shares}. "
                    "Set the ratio deliberately or use AutoFrac = 1."
                )

        frac_db = present.get("fracDB", 0.0)
        if frac_db > 0:
            if self.version == "10.5":
                self.error("fracDB is a USPEX 10.6 feature and is not available in 10.5")
            elif calc_type not in {300, 301}:
                self.warn("fracDB is documented for 3D calculationType 300/301")
            elif len(atoms) > 2:
                self.info(
                    f"fracDB is enabled inside a {len(atoms)}-component search, but the 10.6 "
                    "DAICS importer supplies only unary/binary candidates; ternary/quaternary "
                    "coverage must come from the other variation operators"
                )

        magnetic = _has_magnetic_suffix(scalar(parsed, "calculationType"))
        if present.get("fracSpinMut", 0.0) > 0 and not magnetic:
            self.warn(
                "fracSpinMut is set but calculationType carries no magnetic suffix (s/S); the "
                "spin-mutation operator applies to magnetic structure optimization (manual 4.4), "
                "so either enable magnetism in calculationType or drop the fraction"
            )
        if magnetic and "fracSpinMut" not in present:
            self.info(
                "magnetic calculationType without an explicit fracSpinMut: the manual default is "
                "0.1, and every mutation changes the magnetic structure. Set it deliberately."
            )

        if present.get("fracTopRand", 0.0) > 0:
            self.info(
                "fracTopRand is enabled; run check_uspex_env.py with the same helper Python "
                "used by USPEX and require the compiled randomTopology import to pass"
            )

    def _check_geometry(
        self, parsed: ParsedInput, atoms: list[str], calc_type: int | None
    ) -> None:
        if calc_type in {-200, -201}:
            for name in ("thicknessS", "vacuumSize"):
                if scalar(parsed, name) is None:
                    self.warn(f"2D calculation has no explicit {name}; verify the release default")
            if (self.root / "POSCAR_SUBSTRATE").exists():
                self.warn("2D calculation contains POSCAR_SUBSTRATE; surface and 2D modes may be mixed")

        if calc_type in {200, 201}:
            if not (self.root / "POSCAR_SUBSTRATE").is_file():
                self.error("surface calculation requires POSCAR_SUBSTRATE")
            for name in ("thicknessS", "thicknessB", "reconstruct"):
                if scalar(parsed, name) is None:
                    self.warn(f"surface calculation has no explicit {name}; verify the copied example")

    def _check_objectives(self, parsed: ParsedInput) -> None:
        tokens = block_tokens(parsed, "optType")
        if not tokens:
            self.error("optType block is missing or empty")
            return

        lower = {token.lower() for token in tokens}
        uses_xy = any(
            token in {"x", "y", "min_x", "max_x", "min_y", "max_y"}
            for token in lower
        )
        if uses_xy and self.version == "10.5":
            self.error("x/y arbitrary properties are native to 10.6, not stock 10.5")
        if uses_xy:
            calculator = self.root / "Specific" / "user_property_calculator.py"
            if not calculator.is_file():
                self.error("x/y objective requires Specific/user_property_calculator.py")

        if any(re.fullmatch(r"-?120[1-7]", token) for token in tokens):
            self.warn(
                "optType 1201-1207 are official ML elastic-property channels; document whether this run uses stock behavior or an isolated 10.5 custom remap"
            )

        for token in tokens:
            match = re.fullmatch(r"\[(\d+)\]", token)
            if match and int(match.group(1)) <= 0:
                self.error(f"objective switch generation must be positive, got {token}")

    def _check_stages(self, parsed: ParsedInput, atoms: list[str]) -> None:
        raw_codes = " ".join(block_lines(parsed, "abinitioCode"))
        codes = [int(value) for value in re.findall(r"(?<!\d)-?\d+(?!\d)", raw_codes)]
        if not codes:
            self.error("abinitioCode block is missing or contains no numeric stages")
            return

        commands = block_lines(parsed, "commandExecutable")
        if not commands:
            self.error("commandExecutable block is missing or empty")
        elif len(commands) not in {1, len(codes)}:
            self.error(
                f"commandExecutable has {len(commands)} line(s) for {len(codes)} stages; use one reusable line or one line per stage"
            )
        elif len(set(codes)) > 1 and len(commands) == 1:
            self.warn("heterogeneous abinitioCode stages share one commandExecutable line")

        kresol = block_tokens(parsed, "KresolStart")
        vasp_count = sum(code == 1 for code in codes)
        if vasp_count and not kresol:
            self.warn("VASP stages have no explicit KresolStart block")
        elif kresol and len(kresol) not in {1, len(codes), vasp_count}:
            self.warn(
                f"KresolStart has {len(kresol)} value(s) for {len(codes)} total/{vasp_count} VASP stages"
            )

        specific = self.root / "Specific"
        files = _casefold_files(specific)
        if (vasp_count or 99 in codes) and not specific.is_dir():
            self.error("Specific directory is required by the selected engine stages")
            return

        if vasp_count:
            for position, code in enumerate(codes, 1):
                if code == 1 and f"incar_{position}".casefold() not in files:
                    self.error(f"VASP stage {position} requires Specific/INCAR_{position}")

            by_element = all(f"potcar_{atom}".casefold() in files for atom in atoms)
            by_index = all(f"potcar_{index}".casefold() in files for index in range(1, len(atoms) + 1))
            if atoms and not (by_element or by_index):
                self.error(
                    "VASP stages require a complete POTCAR_<element> or POTCAR_1..N set in atomType order"
                )

        if 99 in codes:
            if self.version == "10.5":
                self.warn(
                    "code 99 on 10.5 requires a documented isolated custom interface; it is native only in 10.6"
                )
            else:
                for position, code in enumerate(codes, 1):
                    if code == 99 and f"userinput_{position}".casefold() not in files:
                        self.warn(
                            f"10.6 code-99 stage {position} has no Specific/userInput_{position}; confirm EX38 and whether the external code needs it"
                        )

    def _check_submission(self, parsed: ParsedInput) -> None:
        parallel = _int_scalar(parsed, "numParallelCalcs")
        if parallel is not None:
            if parallel <= 0:
                self.error(f"numParallelCalcs must be positive, got {parallel}")
            else:
                self.info(
                    f"numParallelCalcs={parallel} is the USPEX in-flight structure-job cap; "
                    "with a scheduler, excess jobs may remain queued and do not require that many physical slots"
                )

        which = _int_scalar(parsed, "whichCluster")
        if which is None:
            self.warn("whichCluster is missing; scheduler ownership is unclear")
            return
        if which == 0:
            return
        suffix = "local" if which == 1 else "remote" if which == 2 else None
        if suffix is None:
            self.error(f"unsupported whichCluster value {which}")
            return

        submission = self.root / "Submission"
        required = [f"submitJob_{suffix}.py", f"checkStatus_{suffix}.py"]
        for name in required:
            path = submission / name
            if not path.is_file():
                self.error(f"whichCluster={which} requires Submission/{name}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            placeholders = re.findall(r"<(?:PARTITION|QUEUE|LOAD_MODULES_AND_ACTIVATE_ENV)>", text)
            if placeholders:
                self.error(f"Submission/{name} still contains template placeholder(s): {sorted(set(placeholders))}")
            if "<CALLRESULT>" not in text:
                self.warn(f"Submission/{name} does not visibly emit <CALLRESULT>")

        self._check_queue_choice(submission)

    def _check_queue_choice(self, submission: Path) -> None:
        """Report the configured queue/partition without inventing a queue catalogue.

        Queue names and limits are site-specific, so a static check can only state what
        the run will ask for. Validating the name requires the cluster:
        ``python scripts/check_schedulers.py --workdir RUN --test-submit``.
        """
        patterns = (
            (r'^\s*PARTITION\s*=\s*["\']([^"\']+)["\']', "Slurm partition"),
            (r'^\s*QUEUE\s*=\s*["\']([^"\']+)["\']', "PBS queue"),
            (r'^\s*RESOURCE_LINE\s*=\s*["\']([^"\']+)["\']', "PBS resource request"),
            (r"#SBATCH\s+--partition=(\S+)", "SBATCH partition"),
            (r"#PBS\s+-q\s+(\S+)", "PBS queue"),
        )
        found: list[str] = []
        for path in sorted(submission.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern, label in patterns:
                for match in re.finditer(pattern, text, re.MULTILINE):
                    value = match.group(1)
                    if value.upper().startswith("<"):
                        continue  # already reported as an unresolved placeholder
                    found.append(f"{label} {value}")
        if not found:
            self.warn(
                "Submission/ does not visibly declare a queue/partition/resource request; "
                "confirm the scheduler request before the production run"
            )
            return
        self.info(
            "configured scheduler request: "
            + "; ".join(dict.fromkeys(found))
            + " — verify the name and limits against the cluster with "
            "'python scripts/check_schedulers.py --workdir <run> --test-submit'"
        )

    def _check_seeds(self, parsed: ParsedInput) -> None:
        seeds = self.root / "Seeds"
        if not seeds.is_dir():
            return
        for path in seeds.glob("POSCARS*"):
            if path.is_file() and path.stat().st_size == 0:
                self.error(f"seed file is empty: {path.relative_to(self.root)}")

        compositions = seeds / "compositions"
        if compositions.is_file():
            expected = len(block_lines(parsed, "numSpecies"))
            for index, raw in enumerate(
                compositions.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                line = _without_comment(raw)
                if not line:
                    continue
                tokens = line.split()
                if expected and len(tokens) != expected:
                    self.warn(
                        f"Seeds/compositions line {index} has {len(tokens)} coefficient(s); numSpecies defines {expected} block(s)"
                    )

    def _check_pressure(self, parsed: ParsedInput) -> None:
        value = scalar(parsed, "ExternalPressure")
        if value is None:
            return
        try:
            pressure = float(value.split()[0])
        except (ValueError, IndexError):
            self.error(f"ExternalPressure is not numeric: {value!r}")
            return
        if not math.isfinite(pressure):
            self.error("ExternalPressure must be finite")


def run_check(root: Path, version: str) -> list[Finding]:
    return Checker(root.resolve(), version).check()


def _count(findings: Iterable[Finding], level: str) -> int:
    return sum(item.level == level for item in findings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="USPEX calculation root")
    parser.add_argument(
        "--uspex-version", choices=("10.5", "10.6"), required=True, help="installed release"
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable findings")
    args = parser.parse_args()

    findings = run_check(args.run_dir, args.uspex_version)
    errors = _count(findings, "ERROR")
    warnings = _count(findings, "WARN")
    if args.json:
        print(
            json.dumps(
                {
                    "run_dir": str(args.run_dir.resolve()),
                    "uspex_version": args.uspex_version,
                    "errors": errors,
                    "warnings": warnings,
                    "findings": [asdict(item) for item in findings],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        for item in findings:
            print(f"[{item.level}] {item.message}")
        status = "PASS" if errors == 0 else "FAIL"
        print(f"{status}: {errors} error(s), {warnings} warning(s)")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
