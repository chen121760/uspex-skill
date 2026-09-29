#!/usr/bin/env python3
"""Unit tests for smoke_code99.py."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from smoke_code99 import SmokeFailure, main, run_smoke


MIXED_POSCAR = """mixed
1.0
5 0 0
0 5 0
0 0 5
Li F
1 1
Direct
0 0 0
0.5 0.5 0.5
"""


CALLBACK = """\
import sys
flag = int(sys.argv[1])
values = {0: 1, 1: -12.5, 101: -10.0, 102: 225.0}
print(f"<CALLRESULT> {values[flag]}")
"""


ENGINE_WRAPPER = """\
import shutil
from pathlib import Path

Path("geom.out").write_text(Path("geom.in").read_text(encoding="utf-8"), encoding="utf-8")
Path("energy.txt").write_text("-12.5\\n", encoding="utf-8")
shutil.copyfile("geom.out", "relaxed_for_property.vasp")
"""


def preserving_callback(preserved: str = "relaxed_for_property.vasp") -> str:
    """Callback wrapper that keeps a geometry copy for the property path.

    It also refuses to run while ``geom.out`` still exists, so a passing
    ``--consume-geom-out`` test proves USPEX's consumption was simulated.
    """
    return (
        "import os\n"
        "import sys\n"
        "P0SCAR = " + repr(MIXED_POSCAR) + "\n"
        "flag = int(sys.argv[1])\n"
        "if os.path.exists('geom.out'):\n"
        "    sys.exit(3)\n"
        "if not os.path.exists(" + repr(preserved) + "):\n"
        "    open(" + repr(preserved) + ", 'w', encoding='utf-8').write(P0SCAR)\n"
        "values = {0: 1, 1: -12.5, 101: -10.0, 102: 225.0}\n"
        "print(f'<CALLRESULT> {values[flag]}')\n"
    )


def arguments(
    root: Path,
    min_species: int = 2,
    callback: str = "user_property_calculator.py",
    penalty: list[float] | None = None,
    consume_geom_out: bool = False,
    preserved: str = "relaxed_for_property.vasp",
) -> argparse.Namespace:
    return argparse.Namespace(
        run_dir=root,
        command=None,
        callback=Path(callback),
        python=Path(sys.executable),
        step=1,
        property_flags=[101, 102],
        required_file=["geom.out", "energy.txt"],
        require_fresh=False,
        min_species=min_species,
        geom_input="geom.in",
        timeout=10.0,
        penalty=list(penalty or []),
        consume_geom_out=consume_geom_out,
        preserved=preserved,
    )


class SmokeCode99Tests(unittest.TestCase):
    def make_tree(self, root: Path) -> None:
        (root / "geom.in").write_text(MIXED_POSCAR, encoding="utf-8")
        (root / "geom.out").write_text(MIXED_POSCAR, encoding="utf-8")
        (root / "energy.txt").write_text("-12.5\n", encoding="utf-8")
        (root / "user_property_calculator.py").write_text(CALLBACK, encoding="utf-8")

    def write_callback(self, root: Path, source: str) -> None:
        (root / "user_property_calculator.py").write_text(source, encoding="utf-8")

    def assert_geom_out_restored(self, root: Path) -> None:
        self.assertEqual((root / "geom.out").read_text(encoding="utf-8"), MIXED_POSCAR)
        self.assertFalse((root / "geom.out.consumed-by-smoke").exists())

    def test_finite_penalty_is_reported_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_smoke(arguments(root))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["species_count"], 2)
            self.assertEqual(result["callback_values"]["101"], -10.0)

    def test_composition_dependent_smoke_rejects_unary_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            (root / "geom.in").write_text(MIXED_POSCAR.replace("Li F\n1 1", "Li\n2"), encoding="utf-8")
            with self.assertRaisesRegex(SmokeFailure, "at least 2"):
                run_smoke(arguments(root))

    def test_declared_penalty_fails_the_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            with self.assertRaises(SmokeFailure) as caught:
                run_smoke(arguments(root, penalty=[-10.0]))
            message = str(caught.exception)
            self.assertIn("101", message)
            self.assertIn("-10.0", message)
            self.assertIn("penalty sentinel", message)
            self.assertIn("not actually exercised", message)

    def test_declared_integer_penalty_matches_float_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            with self.assertRaisesRegex(SmokeFailure, "penalty sentinel"):
                run_smoke(arguments(root, penalty=[-10]))

    def test_near_miss_penalty_is_not_a_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_smoke(arguments(root, penalty=[-10.0000001]))
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["declared_penalties"], [-10.0000001])

    def test_penalty_is_never_applied_to_flags_zero_and_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            # Flag 0 returns 1 and flag 1 returns -12.5; neither is a property flag.
            result = run_smoke(arguments(root, penalty=[1.0, -12.5]))
            self.assertEqual(result["status"], "pass")

    def test_default_declares_no_penalties(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            result = run_smoke(arguments(root))
            self.assertEqual(result["declared_penalties"], [])
            self.assertEqual(result["property_values"], {"101": -10.0, "102": 225.0})
            self.assertIsNone(result["preserved_file"])

    def test_undeclared_penalty_keeps_passing_through_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            stdout = io.StringIO()
            with mock.patch.object(sys, "argv", [*self.main_argv(root)]), contextlib.redirect_stdout(stdout):
                self.assertEqual(main(), 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["callback_values"]["101"], -10.0)

    def test_main_exits_2_for_declared_penalty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            stderr = io.StringIO()
            argv = [*self.main_argv(root), "--penalty", "-10"]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(), 2)
            self.assertIn("FAIL:", stderr.getvalue())
            self.assertIn("penalty sentinel", stderr.getvalue())

    def test_consume_geom_out_passes_on_preserved_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            self.write_callback(root, preserving_callback())
            result = run_smoke(arguments(root, consume_geom_out=True))
            self.assertEqual(result["status"], "pass")
            self.assertTrue(result["consume_geom_out"])
            self.assertEqual(result["preserved_file"]["name"], "relaxed_for_property.vasp")
            self.assertEqual(result["preserved_file"]["species_count"], 2)
            self.assertEqual(result["callback_values"]["102"], 225.0)
            self.assert_geom_out_restored(root)

    def test_consume_geom_out_honours_custom_preserved_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            self.write_callback(root, preserving_callback("kept_geom.out"))
            result = run_smoke(arguments(root, consume_geom_out=True, preserved="kept_geom.out"))
            self.assertEqual(result["preserved_file"]["species_count"], 2)
            self.assert_geom_out_restored(root)

    def test_consume_geom_out_fails_without_preserved_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)  # plain CALLBACK never writes a preserved copy
            with self.assertRaisesRegex(SmokeFailure, "preserved geometry copy"):
                run_smoke(arguments(root, consume_geom_out=True))
            self.assert_geom_out_restored(root)

    def test_consume_geom_out_rejects_wrong_preserved_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            self.write_callback(root, preserving_callback("relaxed_for_property.vasp"))
            with self.assertRaisesRegex(SmokeFailure, "no preserved geometry copy"):
                run_smoke(arguments(root, consume_geom_out=True, preserved="other_geom.out"))
            self.assert_geom_out_restored(root)

    def test_geom_out_restored_after_failing_penalty_during_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            self.write_callback(root, preserving_callback())
            with self.assertRaisesRegex(SmokeFailure, "penalty sentinel"):
                run_smoke(arguments(root, consume_geom_out=True, penalty=[-10.0]))
            self.assert_geom_out_restored(root)

    def test_consume_geom_out_without_geom_out_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            (root / "geom.out").unlink()
            stderr = io.StringIO()
            argv = [*self.main_argv(root), "--consume-geom-out"]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    main()
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("--consume-geom-out requires an existing geom.out", stderr.getvalue())

    def test_consume_geom_out_needs_no_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            self.write_callback(root, preserving_callback())
            stdout = io.StringIO()
            argv = [*self.main_argv(root), "--consume-geom-out"]
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(stdout):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(stdout.getvalue())["preserved_file"]["species_count"], 2)
            self.assert_geom_out_restored(root)

    def test_consume_geom_out_after_command_created_geom_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_tree(root)
            (root / "geom.out").unlink()  # the external engine has not run yet
            self.write_callback(root, preserving_callback())
            (root / "engine_wrapper.py").write_text(ENGINE_WRAPPER, encoding="utf-8")
            args = arguments(root, consume_geom_out=True)
            args.command = f'"{sys.executable}" engine_wrapper.py'
            args.require_fresh = True
            result = run_smoke(args)
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["preserved_file"]["species_count"], 2)
            self.assert_geom_out_restored(root)

    def main_argv(self, root: Path, *extra: str) -> list[str]:
        return [
            "smoke_code99.py",
            str(root),
            "--min-species",
            "2",
            "--property-flags",
            "101",
            "102",
            "--required-file",
            "geom.out",
            "--required-file",
            "energy.txt",
            *extra,
        ]


if __name__ == "__main__":
    unittest.main()
