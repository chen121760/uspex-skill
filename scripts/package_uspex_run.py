#!/usr/bin/env python3
"""Validate and package a clean USPEX run directory for remote transfer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT_FILES = ("INPUT.txt", "POSCAR_SUBSTRATE")
RUN_DIRECTORIES = ("Specific", "Submission", "Seeds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(run_dir: Path, version: str, preflight: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(preflight), str(run_dir), "--uspex-version", version],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError("static preflight failed; package was not created")


def copy_payload(run_dir: Path, stage: Path, skill_root: Path) -> None:
    for name in ROOT_FILES:
        source = run_dir / name
        if source.is_file():
            shutil.copy2(source, stage / name)
    if not (stage / "INPUT.txt").is_file():
        raise RuntimeError(f"missing {run_dir / 'INPUT.txt'}")

    for name in RUN_DIRECTORIES:
        source = run_dir / name
        if source.is_dir():
            shutil.copytree(source, stage / name)

    shutil.copy2(skill_root / "assets" / "run_uspex_loop.sh", stage / "run_uspex_loop.sh")
    scripts = stage / "scripts"
    scripts.mkdir()
    for name in (
        "check_uspex_env.py",
        "check_schedulers.py",
        "preflight_uspex.py",
        "smoke_code99.py",
        "uspex_status.py",
        "extract_uspex_candidates.py",
    ):
        shutil.copy2(skill_root / "scripts" / name, scripts / name)


def build_manifest(stage: Path, version: str, run_name: str) -> dict[str, object]:
    files = {}
    for path in sorted(item for item in stage.rglob("*") if item.is_file()):
        relative = path.relative_to(stage).as_posix()
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_name": run_name,
        "uspex_version": version,
        "files": files,
    }


def executable_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    name = info.name.removeprefix("./")
    if info.isfile() and (
        name == "run_uspex_loop.sh"
        or (name.startswith("scripts/") and name.endswith(".py"))
        or (name.startswith("Specific/") and name.endswith(".py"))
        or name.startswith("Submission/")
    ):
        info.mode = 0o755
    return info


def create_archive(run_dir: Path, output: Path, version: str, skill_root: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="uspex-package-") as tmp:
        stage = Path(tmp) / "run"
        stage.mkdir()
        copy_payload(run_dir, stage, skill_root)
        manifest = build_manifest(stage, version, run_dir.name)
        (stage / "RUN_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, "w:gz") as archive:
            for child in sorted(stage.iterdir()):
                archive.add(child, arcname=child.name, filter=executable_filter)
    return {"archive": str(output), "bytes": output.stat().st_size, "sha256": sha256(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--uspex-version", choices=("10.5", "10.6"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="replace an existing archive")
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    output = args.output.resolve()
    skill_root = Path(__file__).resolve().parent.parent
    if output.exists() and not args.force:
        parser.error(f"output already exists: {output}; pass --force to replace it")

    try:
        validate(run_dir, args.uspex_version, skill_root / "scripts" / "preflight_uspex.py")
        result = create_archive(run_dir, output, args.uspex_version, skill_root)
    except (OSError, RuntimeError, tarfile.TarError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
