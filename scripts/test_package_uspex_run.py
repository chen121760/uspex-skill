#!/usr/bin/env python3
"""Unit tests for package_uspex_run.py."""

from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from package_uspex_run import create_archive, validate


INPUT = """\
USPEX : calculationMethod
301 : calculationType
0 : whichCluster
1 : numParallelCalcs
% optType
1 max_x
% EndOptType
% atomType
Li Mg F Cl
% EndAtomType
% numSpecies
1 0 0 0
0 1 0 0
0 0 1 0
0 0 0 1
% EndNumSpecies
% abinitioCode
99
% ENDabinit
% commandExecutable
python engine.py
% EndExecutable
"""


class PackageRunTests(unittest.TestCase):
    def test_archive_is_clean_and_self_checking(self) -> None:
        skill_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run = base / "example-run"
            run.mkdir()
            (run / "INPUT.txt").write_text(INPUT, encoding="utf-8")
            specific = run / "Specific"
            specific.mkdir()
            (specific / "userInput_1").write_text("test\n", encoding="utf-8")
            (specific / "user_property_calculator.py").write_text("print('<CALLRESULT> 1')\n", encoding="utf-8")
            (run / "results1").mkdir()
            (run / "results1" / "large-output").write_text("exclude me", encoding="utf-8")

            validate(run, "10.6", skill_root / "scripts" / "preflight_uspex.py")
            output = base / "run.tar.gz"
            result = create_archive(run, output, "10.6", skill_root)
            self.assertEqual(result["archive"], str(output))

            with tarfile.open(output, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertIn("INPUT.txt", names)
                self.assertIn("run_uspex_loop.sh", names)
                self.assertIn("scripts/check_uspex_env.py", names)
                self.assertIn("scripts/preflight_uspex.py", names)
                self.assertIn("scripts/smoke_code99.py", names)
                self.assertIn("RUN_MANIFEST.json", names)
                self.assertFalse(any(name.startswith("results1") for name in names))
                manifest = json.load(archive.extractfile("RUN_MANIFEST.json"))
                self.assertEqual(manifest["uspex_version"], "10.6")
                self.assertIn("INPUT.txt", manifest["files"])


if __name__ == "__main__":
    unittest.main()
