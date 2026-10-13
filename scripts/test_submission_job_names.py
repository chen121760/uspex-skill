"""Offline checks for project names and scheduler-safe index preservation."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SubmissionJobNameTests(unittest.TestCase):
    def test_both_templates_keep_project_and_index_within_site_limit(self):
        for family in ("slurm", "pbs"):
            with self.subTest(family=family):
                path = Path(__file__).resolve().parents[1] / "assets/submission" / family / "submitJob_local.py"
                spec = importlib.util.spec_from_file_location(family + "_naming", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                with tempfile.TemporaryDirectory() as tmp:
                    fold = Path(tmp) / "LiAlZrCl-A" / "CalcFold7"
                    fold.mkdir(parents=True)
                    previous = Path.cwd()
                    try:
                        os.chdir(fold)
                        self.assertEqual(module._job_name(12), "LiAlZrCl-A-12")
                        with patch.object(module, "JOB_NAME_PREFIX", "LiAlZrCl-B"):
                            self.assertEqual(module._job_name(12), "LiAlZrCl-B-12")
                        with patch.object(module, "JOB_NAME_PREFIX", "9 project/long label"), patch.object(module, "JOB_NAME_MAX_LENGTH", 15):
                            name = module._job_name(123456)
                            self.assertLessEqual(len(name), 15)
                            self.assertRegex(name, r"^[A-Za-z][A-Za-z0-9_-]*-123456$")
                        with patch.object(module, "JOB_NAME_MAX_LENGTH", 7):
                            with self.assertRaises(RuntimeError):
                                module._job_name(123456)
                    finally:
                        os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
