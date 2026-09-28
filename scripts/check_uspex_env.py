#!/usr/bin/env python3
"""Runtime environment health check for an installed USPEX 10.5/10.6 tree.

Run this *before* a search, and whenever a run reports that the random-topology
generator was skipped:

    python scripts/check_uspex_env.py --uspex-version 10.5
    python scripts/check_uspex_env.py --uspex-path /path/to/USPEX/application/archive

What it checks (each finding is an ERROR or a WARN, and every finding says what to do):

1. ``USPEXPATH`` / ``MCRROOT`` are set and point at a real installation, and the
   ``USPEX`` launcher resolves to that same tree (a different launcher on ``PATH``
   is a silent way to run the wrong release).
2. ``python3`` on ``PATH`` is the interpreter the running USPEX will use for its
   helper scripts, it imports ``numpy``/``scipy``/``spglib``, and it can load a
   shipped compiled topology module. Two packaging schemes exist: 10.5 ships
   version-tagged ``randomTopology.cpython-<tag>-*.so`` files (a hard CPython 3.6-3.9
   pin), while the 10.6 update ships a single stable-ABI ``randomTopology.abi3.so``
   that carries no minor-version pin. A mismatch is the usual reason behind
   "Unable to import numpy and scipy, Random Topology will be skipped!" and the
   matching ``ModuleNotFoundError`` / ``ImportError: randomTopology``.
3. The shipped compiled extension for that interpreter can actually be imported.
4. The scheduler the run depends on is reachable (``sbatch``/``qsub``) when
   ``whichCluster`` implies a batch system, and the optional ``USPEX_``
   environment overrides make sense.
5. For a calculation directory argument, the static preflight is also run.

The script never submits work and never writes inside the installation or the run.
"""

from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Interpreters this release family ships a compiled topology generator for.
# The shipped modules are named randomTopology.cpython-<tag>.so; the tag is the
# only dependable, release-local source of truth, so the table is a fallback only.
FALLBACK_SUPPORTED_PYTHONS = ("3.6", "3.7", "3.8", "3.9")

# The 10.6 update replaces the version-tagged builds with one stable-ABI (PEP 384)
# module. It carries no CPython minor-version pin; the import check below is the
# real gate. Verified against CPython 3.9, 3.10, 3.11 and 3.13.
ABI3_MODULE = "randomTopology.abi3.so"
ABI3_VERIFIED_FROM = "3.9"

# Helper scripts that need these at import time.
REQUIRED_MODULES = ("numpy", "scipy", "spglib")

HELPER_CONSUMERS = {
    "numpy": [
        "FunctionFolder/random_topology.py",
        "FunctionFolder/calcElasticML.py",
        "FunctionFolder/calcTemperature.py",
        "FunctionFolder/coord_number.py",
        "FunctionFolder/latConverter.py",
    ],
    "scipy": ["FunctionFolder/random_topology.py"],
    "spglib": ["FunctionFolder/spacegroup/spacegroup.py"],
}


class Report:
    def __init__(self) -> None:
        self.findings: list[tuple[str, str, str]] = []  # level, title, remedy

    def error(self, title: str, remedy: str) -> None:
        self.findings.append(("ERROR", title, remedy))

    def warn(self, title: str, remedy: str) -> None:
        self.findings.append(("WARN", title, remedy))

    def ok(self, title: str) -> None:
        self.findings.append(("OK", title, ""))

    def counts(self) -> tuple[int, int]:
        errors = sum(1 for level, _, _ in self.findings if level == "ERROR")
        warnings = sum(1 for level, _, _ in self.findings if level == "WARN")
        return errors, warnings


def _shipped_python_tags(archive: Path) -> list[str]:
    tags = set()
    for path in glob.glob(str(archive / "src" / "FunctionFolder" / "randomTopology.cpython-*.so")):
        match = re.search(r"randomTopology\.cpython-(\d)(\d+)([a-z]*)", os.path.basename(path))
        if match:
            tags.add("%s.%s" % (match.group(1), match.group(2)))
    return sorted(tags)


def _has_abi3_module(archive: Path) -> bool:
    return (archive / "src" / "FunctionFolder" / ABI3_MODULE).is_file()


def _minor_of(version_text: str) -> str | None:
    match = re.search(r"(\d+)\.(\d+)", version_text or "")
    return "%s.%s" % (match.group(1), match.group(2)) if match else None


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _interpreter_report(
    report: Report, python: str, tags: list[str], abi3: bool = False, runner=_run
) -> None:
    if not python:
        report.error(
            "no python3 on PATH",
            "USPEX's launcher and helpers are Python; activate the environment that carries "
            "numpy/scipy/spglib and put it first on PATH before running USPEX.",
        )
        return

    code, text = runner([python, "-c", "import sys; print(sys.version.split()[0]); print(sys.executable)"])
    if code != 0:
        report.error("python3 on PATH is not runnable: %s" % python, text.strip())
        return
    lines = [line for line in text.splitlines() if line.strip()]
    version = lines[0].strip() if lines else "?"
    executable = lines[1].strip() if len(lines) > 1 else python
    minor = _minor_of(version)
    report.ok("python3 = %s (%s), version %s" % (python, executable, version))

    supported = tags or list(FALLBACK_SUPPORTED_PYTHONS)
    if abi3:
        report.ok(
            "stable-ABI topology module %s is shipped: no CPython minor-version pin "
            "(verified from %s upwards; the import check is the real gate)"
            % (ABI3_MODULE, ABI3_VERIFIED_FROM)
        )
    if minor and minor not in supported and not abi3:
        report.error(
            "python3 is CPython %s but this installation ships compiled topology modules for %s"
            % (version, ", ".join(supported)),
            "Use an interpreter matching one of %s for this release (the bundled "
            "randomTopology.cpython-<tag>.so files are the source of truth). Otherwise the "
            "run falls back to 'Random Topology will be skipped' or crashes with "
            "ModuleNotFoundError/ImportError for randomTopology." % ", ".join(supported),
        )
    elif minor and abi3 and tags and minor not in tags:
        report.ok(
            "CPython %s has no version-tagged build here; it will load %s" % (version, ABI3_MODULE)
        )

    for module in REQUIRED_MODULES:
        code, text = runner([python, "-c", "import %s; print(getattr(%s,'__version__','?'))" % (module, module)])
        if code != 0:
            consumers = ", ".join(HELPER_CONSUMERS.get(module, []))
            report.error(
                "python3 cannot import %s" % module,
                "install %s into that interpreter (%s). Needed by: %s"
                % (module, module, consumers or "USPEX helper scripts"),
            )
        else:
            report.ok("import %s -> %s" % (module, text.strip().splitlines()[-1] if text.strip() else "ok"))


def _topology_import_report(report: Report, python: str, archive: Path, runner=_run) -> None:
    folder = archive / "src" / "FunctionFolder"
    if not folder.is_dir():
        return
    code, text = runner(
        [
            python,
            "-c",
            "import sys; sys.path.insert(0, %r); import randomTopology; print('ok')" % str(folder),
        ]
    )
    if code == 0:
        report.ok("compiled randomTopology extension imports with this python3")
        return
    tail = "\n".join(text.strip().splitlines()[-3:])
    if "No module named" in text and "numpy" in text:
        report.error(
            "randomTopology cannot import because numpy is missing for this interpreter",
            "numpy/scipy are prerequisites of the shipped topology generator; install them into "
            "the interpreter that will run USPEX, or USPEX will skip random topology.",
        )
    else:
        report.error(
            "compiled randomTopology extension does not import (wrong CPython ABI or missing binary)",
            "match the interpreter to a shipped randomTopology.cpython-<tag>.so, or use any "
            "interpreter able to load the stable-ABI randomTopology.abi3.so (CPython %s and newer), "
            "or rebuild/obtain the build for this release. Detail:\n%s" % (ABI3_VERIFIED_FROM, tail),
        )


def _compiled_module_report(report: Report, archive: Path) -> None:
    """Describe which topology modules the tree ships, and the tag/abi3 precedence trap."""
    tags = _shipped_python_tags(archive)
    abi3 = _has_abi3_module(archive)
    if tags:
        report.ok("shipped compiled topology modules support CPython: %s" % ", ".join(tags))
    if abi3:
        report.ok(
            "shipped stable-ABI topology module %s (loads on CPython %s and newer)"
            % (ABI3_MODULE, ABI3_VERIFIED_FROM)
        )
    if tags and abi3:
        report.warn(
            "this tree ships both version-tagged and stable-ABI topology modules; CPython %s "
            "prefers randomTopology.cpython-<tag>.so over randomTopology.abi3.so" % ", ".join(tags),
            "after applying the 10.6 update the version-tagged files are usually stale leftovers. "
            "If you intend to run the stable-ABI build, remove or rename randomTopology.cpython-*.so "
            "in FunctionFolder, or use an interpreter with no matching tag (for example >= 3.10 here).",
        )
    if not tags and not abi3:
        report.warn(
            "no compiled randomTopology module found in FunctionFolder",
            "the topology generator will be skipped and structures will come from space groups "
            "only; verify the installation is complete for this release.",
        )


def _installation_report(report: Report, archive: Path | None) -> None:
    uspexpath = os.environ.get("USPEXPATH")
    mcrroot = os.environ.get("MCRROOT")
    if not uspexpath:
        report.error("USPEXPATH is not set", "export USPEXPATH=<install>/application/archive/src")
    elif archive is None:
        archive = Path(uspexpath).parent
    if not mcrroot:
        report.error(
            "MCRROOT is not set",
            "export MCRROOT=<install path before the v91 folder>. USPEX 10.5 needs the MATLAB "
            "Runtime (R2016b / v91) that the package installer places there.",
        )
    else:
        if not (Path(mcrroot) / "v91" / "runtime" / "glnxa64").is_dir():
            report.error(
                "MCRROOT does not contain v91/runtime/glnxa64: %s" % mcrroot,
                "point MCRROOT at the directory *before* v91 (the folder the installer created).",
            )
        else:
            report.ok("MCRROOT contains the MATLAB Runtime v91 runtime directory")

    if not uspexpath:
        return
    src = Path(uspexpath)
    if not (src / "FunctionFolder" / "VERSION").is_file():
        report.error(
            "USPEXPATH does not look like a USPEX source tree: %s" % uspexpath,
            "USPEXPATH must be <install>/application/archive/src",
        )
        return
    version_line = (src / "FunctionFolder" / "VERSION").read_text(errors="replace").strip().splitlines()
    release = version_line[0] if version_line else "unknown"
    report.ok("USPEXPATH release: %s" % release)

    launcher = shutil.which("USPEX")
    if not launcher:
        report.error("no USPEX launcher on PATH", "add <install>/application/archive to PATH")
    else:
        resolved = Path(os.path.realpath(launcher))
        if os.path.realpath(src.parent) != str(resolved.parent):
            report.error(
                "the USPEX on PATH (%s) is not the tree USPEXPATH points at (%s)" % (launcher, src.parent),
                "you are about to run a different release than you configured; fix PATH ordering, "
                "or set USPEXPATH to match the launcher you intend to use.",
            )
        else:
            report.ok("USPEX launcher on PATH resolves to this installation: %s" % launcher)
        executable = src / "uspex.x"
        if executable.is_file() and not os.access(executable, os.X_OK):
            report.error("%s is not executable" % executable, "chmod +x the packaged uspex.x / clean")
        _compiled_module_report(report, src.parent)


def _scheduler_report(report: Report, calc_dir: Path | None) -> None:
    which = None
    if calc_dir and (calc_dir / "INPUT.txt").is_file():
        text = (calc_dir / "INPUT.txt").read_text(errors="replace")
        match = re.search(r"^\s*(\d+)\s*:\s*whichCluster", text, re.M)
        if match:
            which = int(match.group(1))
    if which is None:
        report.warn(
            "whichCluster not determined (no calculation directory given)",
            "pass RUN_DIR so the scheduler contract can be checked, or ignore this warning",
        )
        return
    if which == 0:
        report.ok("whichCluster=0: internal/local execution, no scheduler needed")
        return
    tool = "sbatch" if which == 1 else "sbatch/qsub"
    if shutil.which("sbatch"):
        report.ok("whichCluster=%d and sbatch is available" % which)
    else:
        report.error(
            "whichCluster=%d needs a batch submission command (%s) on PATH" % (which, tool),
            "load the scheduler module or set whichCluster=0 for a local pilot",
        )
    for name in ("submitJob_local.py", "checkStatus_local.py") if which == 1 else (
        "submitJob_remote.py",
        "checkStatus_remote.py",
    ):
        if calc_dir and not (calc_dir / "Submission" / name).is_file():
            report.error(
                "Submission/%s is missing for whichCluster=%d" % (name, which),
                "copy the Submission directory from the shipped example and adapt it to this site",
            )


def _preflight_report(report: Report, calc_dir: Path, version: str) -> None:
    here = Path(__file__).resolve().parent
    script = here / "preflight_uspex.py"
    if not script.is_file():
        report.warn("preflight_uspex.py not found next to this script", "run it separately")
        return
    code, text = _run([sys.executable, str(script), str(calc_dir), "--uspex-version", version, "--json"])
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        report.warn("static preflight produced unparsable output", text.strip()[:400])
        return
    if payload.get("errors"):
        report.error(
            "static preflight reports %d error(s) for %s" % (payload["errors"], calc_dir),
            "fix every ERROR before launching; run scripts/preflight_uspex.py directly for detail",
        )
    else:
        report.ok("static preflight passes for %s (%d warning(s))" % (calc_dir, payload.get("warnings", 0)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", nargs="?", type=Path, help="optional calculation directory to preflight")
    parser.add_argument("--uspex-version", choices=("10.5", "10.6"), default="10.5")
    parser.add_argument("--uspex-path", type=Path, help="override for USPEXPATH")
    parser.add_argument("--python", default=None, help="interpreter to check (default: python3 on PATH)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.uspex_path and not os.environ.get("USPEXPATH"):
        os.environ["USPEXPATH"] = str(args.uspex_path)

    report = Report()
    archive = Path(os.environ["USPEXPATH"]).parent if os.environ.get("USPEXPATH") else None

    _installation_report(report, archive)
    python = args.python or shutil.which("python3") or ""
    tags = _shipped_python_tags(archive) if archive else []
    abi3 = _has_abi3_module(archive) if archive else False
    _interpreter_report(report, python, tags, abi3=abi3)
    if archive and python:
        _topology_import_report(report, python, archive)
    _scheduler_report(report, args.run_dir)
    if args.run_dir:
        if not args.run_dir.is_dir():
            report.error("run directory does not exist: %s" % args.run_dir, "create it or fix the path")
        else:
            _preflight_report(report, args.run_dir, args.uspex_version)

    errors, warnings = report.counts()
    if args.json:
        print(json.dumps(
            {
                "uspex_version": args.uspex_version,
                "uspexpath": os.environ.get("USPEXPATH"),
                "mcrroot": os.environ.get("MCRROOT"),
                "python": python,
                "supported_python_abis": (tags + ["abi3"]) if abi3 else tags,
                "errors": errors,
                "warnings": warnings,
                "findings": [
                    {"level": level, "title": title, "remedy": remedy}
                    for level, title, remedy in report.findings
                ],
            },
            indent=2,
            ensure_ascii=False,
        ))
    else:
        for level, title, remedy in report.findings:
            print("[%s] %s" % (level, title))
            if remedy:
                for line in remedy.splitlines():
                    print("       -> %s" % line)
        print("PASS: %d error(s), %d warning(s)" % (errors, warnings) if errors == 0
              else "FAIL: %d error(s), %d warning(s)" % (errors, warnings))
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
