#!/usr/bin/env python3
"""Unit tests for preflight_uspex.py; no USPEX installation required."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from preflight_uspex import run_check


VALID_INPUT = """\
USPEX : calculationMethod
301 : calculationType
0.0 : ExternalPressure
1 : whichCluster
30 : numParallelCalcs
1 : AutoFrac
0.25 : fracGene
0.20 : fracRand
0.10 : fracTopRand
0.15 : fracAtomsMut
0.10 : fracTrans
0.05 : fracLatMut
0.05 : fracPerm
0.10 : fracDB
% optType
min_x max_y
% EndOptType
% atomType
Li Zn I Br
% EndAtomType
% numSpecies
1 0 0 0
0 1 0 0
0 0 1 0
0 0 0 1
% EndNumSpecies
% abinitioCode
1 99
% ENDabinit
% commandExecutable
srun vasp_std > vasp.log
python external.py
% EndExecutable
% KresolStart
0.12 0.08
% Kresolend
"""


class PreflightTests(unittest.TestCase):
    def make_valid_tree(self, root: Path) -> None:
        (root / "INPUT.txt").write_text(VALID_INPUT, encoding="utf-8")
        specific = root / "Specific"
        specific.mkdir()
        for name in (
            "INCAR_1",
            "POTCAR_Li",
            "POTCAR_Zn",
            "POTCAR_I",
            "POTCAR_Br",
            "userInput_2",
            "user_property_calculator.py",
        ):
            (specific / name).write_text("test\n", encoding="utf-8")
        submission = root / "Submission"
        submission.mkdir()
        callback = "print('<CALLRESULT>')\nprint(1)\n"
        (submission / "submitJob_local.py").write_text(callback, encoding="utf-8")
        (submission / "checkStatus_local.py").write_text(callback, encoding="utf-8")

    def test_valid_10_6_tree_has_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            findings = run_check(root, "10.6")
            self.assertFalse([item for item in findings if item.level == "ERROR"])

    def test_xy_is_rejected_for_stock_10_5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            findings = run_check(root, "10.5")
            messages = [item.message for item in findings if item.level == "ERROR"]
            self.assertTrue(any("native to 10.6" in message for message in messages))

    def test_num_species_width_mismatch_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            path.write_text(VALID_INPUT.replace("1 0 0 0\n", "1 0 0\n", 1), encoding="utf-8")
            findings = run_check(root, "10.6")
            messages = [item.message for item in findings if item.level == "ERROR"]
            self.assertTrue(any("atomType has 4" in message for message in messages))

    def test_quaternary_frac_db_and_topology_emit_actionable_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            findings = run_check(root, "10.6")
            messages = [item.message for item in findings if item.level == "INFO"]
            self.assertTrue(any("unary/binary candidates" in message for message in messages))
            self.assertTrue(any("randomTopology" in message for message in messages))
            self.assertTrue(any("in-flight structure-job cap" in message for message in messages))

    def test_variation_fraction_sum_is_reported_as_normalisation_not_a_defect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            path.write_text(VALID_INPUT.replace("0.25 : fracGene", "0.55 : fracGene"), encoding="utf-8")
            findings = run_check(root, "10.6")
            info = [item.message for item in findings if item.level == "INFO"]
            warn = [item.message for item in findings if item.level == "WARN"]
            self.assertTrue(any("normalises them" in message for message in info))
            self.assertFalse(any("variation fractions sum" in message for message in warn))

    def test_partial_variation_override_does_not_sum_against_missing_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            lines = [
                line for line in VALID_INPUT.splitlines()
                if not line.strip().endswith((
                    ": fracGene", ": fracRand", ": fracTopRand", ": fracAtomsMut",
                    ": fracTrans", ": fracLatMut", ": fracPerm",
                ))
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            findings = run_check(root, "10.6")
            messages = [item.message for item in findings]
            self.assertFalse(any("variation fractions sum" in message for message in messages))

    def test_frac_spin_mut_without_magnetic_type_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            path.write_text(
                VALID_INPUT.replace("1 : AutoFrac", "1 : AutoFrac\n0.1 : fracSpinMut"),
                encoding="utf-8",
            )
            findings = run_check(root, "10.6")
            warn = [item.message for item in findings if item.level == "WARN"]
            self.assertTrue(any("fracSpinMut" in message for message in warn))

    def test_magnetic_suffix_is_recognised_as_in_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            path.write_text(VALID_INPUT.replace("301 : calculationType", "s301 : calculationType"),
                            encoding="utf-8")
            findings = run_check(root, "10.6")
            warn = [item.message for item in findings if item.level == "WARN"]
            info = [item.message for item in findings if item.level == "INFO"]
            self.assertFalse(any("outside this skill's audited" in message for message in warn))
            self.assertTrue(any("magnetic suffix" in message for message in info))

    def test_all_zero_fractions_do_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            path = root / "INPUT.txt"
            text = VALID_INPUT.replace("0.25 : fracGene", "0.0 : fracGene")
            for name in ("fracRand", "fracTopRand", "fracAtomsMut", "fracTrans", "fracLatMut",
                         "fracPerm", "fracDB"):
                text = text.replace(f"0.20 : {name}", f"0.0 : {name}")
                text = text.replace(f"0.10 : {name}", f"0.0 : {name}")
                text = text.replace(f"0.15 : {name}", f"0.0 : {name}")
                text = text.replace(f"0.05 : {name}", f"0.0 : {name}")
            path.write_text(text, encoding="utf-8")
            findings = run_check(root, "10.6")
            self.assertTrue(findings)

    def test_frac_db_is_rejected_for_10_5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            findings = run_check(root, "10.5")
            messages = [item.message for item in findings if item.level == "ERROR"]
            self.assertTrue(any("fracDB is a USPEX 10.6 feature" in message for message in messages))

    def test_configured_queue_is_reported_as_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            (root / "Submission" / "submitJob_local.py").write_text(
                'QUEUE = "workq"\nRESOURCE_LINE = "nodes=1:ppn=8"\nprint("<CALLRESULT>")\n',
                encoding="utf-8",
            )
            findings = run_check(root, "10.6")
            info = [item.message for item in findings if item.level == "INFO"]
            self.assertTrue(any("PBS queue workq" in message for message in info))

    def test_undeclared_queue_is_warned_about(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_valid_tree(root)
            for name in ("submitJob_local.py", "checkStatus_local.py"):
                (root / "Submission" / name).write_text(
                    'print("<CALLRESULT>")\nprint(1)\n', encoding="utf-8"
                )
            findings = run_check(root, "10.6")
            warn = [item.message for item in findings if item.level == "WARN"]
            self.assertTrue(any("does not visibly declare a queue" in message for message in warn))


if __name__ == "__main__":
    unittest.main()
