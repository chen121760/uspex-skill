#!/usr/bin/env python3
"""Unit tests for check_uspex_env.py; no USPEX installation required."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from check_uspex_env import (
    Report,
    _compiled_module_report,
    _has_abi3_module,
    _interpreter_report,
    _minor_of,
    _shipped_python_tags,
    _scheduler_report,
)


def _fake_runner(version_line: str, importable: tuple[str, ...]):
    """Return a runner() replacement that answers -c probes without spawning processes."""

    def runner(cmd: list[str]) -> tuple[int, str]:
        code = cmd[-1]
        if code.startswith("import sys"):
            return 0, "%s\n%s\n" % (version_line, cmd[0])
        module = code.split()[1].split(";")[0].strip()
        if module in importable:
            return 0, "1.0\n"
        return 1, "ModuleNotFoundError: No module named '%s'\n" % module

    return runner


class ShippedTagTests(unittest.TestCase):
    def test_tags_read_from_bundled_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "src" / "FunctionFolder"
            folder.mkdir(parents=True)
            for tag in ("cpython-36m", "cpython-38", "cpython-39"):
                (folder / f"randomTopology.{tag}-x86_64-linux-gnu.so").write_bytes(b"")
            self.assertEqual(_shipped_python_tags(Path(tmp)), ["3.6", "3.8", "3.9"])

    def test_no_extensions_yields_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_shipped_python_tags(Path(tmp)), [])

    def test_minor_parsing(self) -> None:
        self.assertEqual(_minor_of("3.9.23"), "3.9")
        self.assertEqual(_minor_of("3.12.14 | packaged by conda-forge"), "3.12")
        self.assertIsNone(_minor_of("unknown"))


class StableAbiTests(unittest.TestCase):
    """The 10.6 update ships one abi3 module instead of version-tagged builds."""

    def _tree(self, root: Path, names: tuple[str, ...]) -> Path:
        folder = root / "src" / "FunctionFolder"
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(b"")
        return root

    def test_abi3_module_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), ("randomTopology.abi3.so",))
            self.assertTrue(_has_abi3_module(root))
            self.assertEqual(_shipped_python_tags(root), [])

    def test_abi3_accepts_unlisted_interpreter(self) -> None:
        report = Report()
        _interpreter_report(report, "/opt/py312/bin/python3", [],
                            abi3=True,
                            runner=_fake_runner("3.12.14", ("numpy", "scipy", "spglib")))
        errors = [item for item in report.findings if item[0] == "ERROR"]
        self.assertFalse(errors, errors)

    def test_tagged_only_still_rejects_unlisted_interpreter(self) -> None:
        report = Report()
        _interpreter_report(report, "/opt/py312/bin/python3", ["3.6", "3.9"],
                            abi3=False,
                            runner=_fake_runner("3.12.14", ("numpy", "scipy", "spglib")))
        titles = [title for level, title, _ in report.findings if level == "ERROR"]
        self.assertTrue(any("CPython 3.12" in title for title in titles), titles)

    def test_abi3_ignores_fallback_table(self) -> None:
        """Without tags and without abi3 the 10.5 fallback table still applies."""
        report = Report()
        _interpreter_report(report, "/opt/py312/bin/python3", [],
                            abi3=False,
                            runner=_fake_runner("3.12.14", ("numpy", "scipy", "spglib")))
        self.assertTrue(any(level == "ERROR" for level, _, _ in report.findings))

    def test_tree_shipping_both_warns_about_tag_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), (
                "randomTopology.cpython-39-x86_64-linux-gnu.so",
                "randomTopology.abi3.so",
            ))
            report = Report()
            _compiled_module_report(report, root)
            warnings = [title for level, title, _ in report.findings if level == "WARN"]
            self.assertTrue(any("prefers randomTopology.cpython-" in title for title in warnings), warnings)

    def test_abi3_only_tree_has_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), ("randomTopology.abi3.so",))
            report = Report()
            _compiled_module_report(report, root)
            self.assertFalse([item for item in report.findings if item[0] == "WARN"])

    def test_missing_modules_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), ("latConverter.py",))
            report = Report()
            _compiled_module_report(report, root)
            self.assertTrue(any(level == "WARN" for level, _, _ in report.findings))


class InterpreterTests(unittest.TestCase):
    def test_matching_interpreter_with_all_dependencies_passes(self) -> None:
        report = Report()
        _interpreter_report(report, "/opt/py39/bin/python3", ["3.6", "3.7", "3.8", "3.9"],
                            runner=_fake_runner("3.9.23", ("numpy", "scipy", "spglib")))
        errors = [item for item in report.findings if item[0] == "ERROR"]
        self.assertFalse(errors, errors)

    def test_version_outside_bundled_abi_is_an_error(self) -> None:
        report = Report()
        _interpreter_report(report, "/opt/py311/bin/python3", ["3.6", "3.7", "3.8", "3.9"],
                            runner=_fake_runner("3.11.15", ("numpy", "scipy", "spglib")))
        titles = [title for level, title, _ in report.findings if level == "ERROR"]
        self.assertTrue(any("randomTopology" in title or "CPython 3.11" in title for title in titles), titles)

    def test_missing_dependency_is_an_error_naming_the_consumer(self) -> None:
        report = Report()
        _interpreter_report(report, "/opt/py39/bin/python3", ["3.9"],
                            runner=_fake_runner("3.9.23", ("numpy",)))
        entries = [(title, remedy) for level, title, remedy in report.findings if level == "ERROR"]
        self.assertTrue(any("cannot import spglib" in title for title, _ in entries), entries)
        remedy = next(remedy for title, remedy in entries if "cannot import spglib" in title)
        self.assertIn("spacegroup", remedy)

    def test_missing_interpreter_is_an_error(self) -> None:
        report = Report()
        _interpreter_report(report, "", ["3.9"])
        self.assertTrue(any(level == "ERROR" for level, _, _ in report.findings))


class SchedulerTests(unittest.TestCase):
    def _tree(self, root: Path, which_cluster: int, callbacks: tuple[str, ...]) -> Path:
        (root / "INPUT.txt").write_text(
            "USPEX : calculationMethod\n300 : calculationType\n"
            "% atomType\nSi\n% EndAtomType\n% numSpecies\n8\n% EndNumSpecies\n"
            "% abinitioCode\n1\n% ENDabinit\n% commandExecutable\nvasp > log\n% EndExecutable\n"
            f"{which_cluster} : whichCluster\n",
            encoding="utf-8",
        )
        submission = root / "Submission"
        submission.mkdir()
        for name in callbacks:
            (submission / name).write_text("print('<CALLRESULT>')\n", encoding="utf-8")
        return root

    def test_local_submission_without_callbacks_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), 1, ())
            report = Report()
            _scheduler_report(report, root)
            titles = [title for level, title, _ in report.findings if level == "ERROR"]
            self.assertTrue(any("submitJob_local.py" in title for title in titles), titles)

    def test_no_job_script_mode_needs_no_scheduler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(Path(tmp), 0, ())
            report = Report()
            _scheduler_report(report, root)
            self.assertFalse([item for item in report.findings if item[0] == "ERROR"])


if __name__ == "__main__":
    unittest.main()
