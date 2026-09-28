#!/usr/bin/env python3
"""Unit tests for check_run_liveness.py; no cluster, USPEX installation, or scheduler needed.

The fixtures reproduce the shapes observed in real 10.5 runs: `results<N>/generation<G>/`
directories whose mtimes give the generation cadence, a `results<N>/OUTPUT.txt` with completed
generation blocks, a `log` that the driver truncates every invocation, and
`CalcFoldTemp/Jobs.history` submission stamps without a year.

`decide()` is tested directly with synthetic evidence, because it must separate four cases that a
filesystem fixture cannot all express: a healthy run inside a long quiet phase, a run whose engine
is spinning with frozen I/O, a run with nothing alive, and a run with too little evidence.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from check_run_liveness import (
    EXIT_DONE,
    EXIT_STOPPED,
    EXIT_STUCK,
    EXIT_UNKNOWN,
    EXIT_WORKING,
    attribute_jobs,
    collect,
    decide,
    detect_finished,
    generation_cadence,
    main,
    newest_activity,
    parse_jobs_history_submissions,
    sample_window,
)

OUTPUT_TXT = """\
*------------------------------------------------------------------------------*
|                           version 10.5 (08/07/2021)                          |
*------------------------------------------------------------------------------*
            Job Starts at                 27-Sep-2026 11:39:05
--------------------------- SUMMARY of Generation 11 ---------------------------
--------------------------- SUMMARY of Generation 12 ---------------------------
"""

OUTPUT_TXT_DONE = OUTPUT_TXT + "            Job Finished at                28-Sep-2026 09:00:00\n"

JOBS_HISTORY = """\
Generation 30 Step 2 of Structure 269 at Calcfold7 : JOBID 3534008  Submitted Sep28-21:31:26
Generation 30 Step 2 of Structure 270 at Calcfold8 : JOBID 3534009  Submitted Sep28-21:31:27
Generation 30 Step 2 of Structure 271 at Calcfold9 : JOBID 3534010  Submitted Sep28-21:31:27
Generation 31 Step 1 of Structure 1 at Calcfold1 : JOBID 3534130  Submitted Sep28-23:01:26
Generation 31 Step 1 of Structure 2 at Calcfold2 : JOBID 3534131  Submitted Sep28-23:01:27
"""


def write(path: Path, text: str, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def build_run(root: Path, *, generations: int = 4, cadence: float = 3600.0,
              newest_age: float = 600.0, now: float | None = None, done: bool = False) -> float:
    """A minimal but realistic run directory; returns `now`."""
    now = now if now is not None else 1_800_000_000.0
    write(root / "INPUT.txt", "301 : calculationType\n", now - 10 * cadence)
    newest_generation = now - newest_age
    for index in range(1, generations + 1):
        gen = root / "results1" / f"generation{index}"
        gen.mkdir(parents=True, exist_ok=True)
        stamp = newest_generation - (generations - index) * cadence
        os.utime(gen, (stamp, stamp))
    write(root / "results1" / "OUTPUT.txt", OUTPUT_TXT_DONE if done else OUTPUT_TXT,
          now - newest_age)
    write(root / "log", "Mon Sep 28 08:31:12 PM CST 2026\n", now - newest_age)
    write(root / "CalcFoldTemp" / "Jobs.history", JOBS_HISTORY, now - newest_age)
    if done:
        write(root / "USPEX_IS_DONE", "", now - 60)
    return now


def evidence(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "finished": False,
        "activity_age_seconds": 600.0,
        "activity_path": "log",
        "file_moved": False,
        "cadence_seconds": 3600.0,
        "cadence_source": "test",
        "jobs_active": 0,
        "engine_count": 1,
        "engine_checked": True,
        "driver_count": 1,
        "helper_count": 1,
        "helpers_in_pipe_read": 1,
        "io_delta_bytes": 0,
        "cpu_busy_fraction": 1.0,
        "sample_seconds": 60.0,
        "sampled": True,
    }
    base.update(overrides)
    return base


class CadenceTests(unittest.TestCase):
    def test_median_cadence_from_generation_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = build_run(root, generations=5, cadence=5400.0)
            cadence = generation_cadence(root)
            self.assertEqual(cadence["generations_seen"], 5)
            self.assertAlmostEqual(float(cadence["median_seconds"]), 5400.0, delta=1.0)
            self.assertEqual(cadence["last_generation"], "results1/generation5")
            self.assertLessEqual(float(cadence["last_generation_mtime"]), now)

    def test_cadence_never_spans_two_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in (1, 2):
                gen = root / "results1" / f"generation{index}"
                gen.mkdir(parents=True, exist_ok=True)
                os.utime(gen, (1000.0 + index * 60,) * 2)
            for index in (1, 2):
                gen = root / "results2" / f"generation{index}"
                gen.mkdir(parents=True, exist_ok=True)
                os.utime(gen, (900_000.0 + index * 60,) * 2)
            cadence = generation_cadence(root)
            self.assertEqual(cadence["intervals_seconds"], [60.0, 60.0])
            self.assertAlmostEqual(float(cadence["median_seconds"]), 60.0)

    def test_jobs_history_gives_first_submission_per_generation(self) -> None:
        parsed = parse_jobs_history_submissions(JOBS_HISTORY, now=datetime(2026, 9, 28, 23, 30))
        self.assertEqual(parsed["submissions"], 5)
        self.assertEqual(parsed["generations_seen"], 2)
        self.assertAlmostEqual(float(parsed["median_seconds"]), 5400.0, delta=1.0)
        self.assertEqual(parsed["last_submission"], "2026-09-28T23:01:26")

    def test_jobs_history_without_submitted_stamps_has_no_cadence(self) -> None:
        text = "Generation 1 Step 1 of Structure 1 at Calcfold1 : JOBID 1\n"
        parsed = parse_jobs_history_submissions(text, now=datetime(2026, 9, 28, 23, 30))
        self.assertEqual(parsed["submissions"], 1)
        self.assertIsNone(parsed["median_seconds"])


class ActivityTests(unittest.TestCase):
    def test_newest_activity_prefers_the_latest_artefact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = 1_800_000_000.0
            write(root / "log", "old\n", now - 9000)
            write(root / "results1" / "OUTPUT.txt", OUTPUT_TXT, now - 500)
            write(root / "CalcFold1" / "OSZICAR", "newest\n", now - 30)
            activity = newest_activity(root, root / "results1", now=now)
            self.assertTrue(str(activity["path"]).endswith("CalcFold1/OSZICAR"))
            self.assertAlmostEqual(float(activity["age_seconds"]), 30.0, delta=1.0)

    def test_newest_activity_is_none_for_an_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activity = newest_activity(Path(tmp), None, now=1.0)
            self.assertIsNone(activity["age_seconds"])

    def test_future_dated_artefact_is_clamped_and_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = 1_000_000.0
            write(root / "log", "x\n", now + 5_000.0)
            activity = newest_activity(root, None, now=now)
            self.assertEqual(activity["age_seconds"], 0.0)
            self.assertTrue(activity["future_dated"])

    def test_finished_from_marker_or_output_txt(self) -> None:
        self.assertTrue(detect_finished({"USPEX_IS_DONE": True}, None))
        with tempfile.TemporaryDirectory() as tmp:
            results = Path(tmp, "results1")
            write(results / "OUTPUT.txt", OUTPUT_TXT_DONE)
            self.assertTrue(detect_finished({"USPEX_IS_DONE": False}, results))
            write(results / "OUTPUT.txt", OUTPUT_TXT)
            self.assertFalse(detect_finished({"USPEX_IS_DONE": False}, results))


class SampleWindowTests(unittest.TestCase):
    def test_io_and_cpu_deltas_are_read_from_proc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp, "proc")
            entry = proc / "4242"
            entry.mkdir(parents=True)
            io_text = "rchar: 100\nwchar: 50\nread_bytes: 0\nwrite_bytes: 0\n"
            stat_text = f"4242 (uspex.x) R 1 1 1 0 -1 4194304 0 0 0 0 100 20 0 0 20 0 1 0 0 0 0 0"
            write(entry / "io", io_text)
            write(entry / "stat", stat_text)

            def bump(_seconds: float) -> None:
                write(entry / "io", "rchar: 400\nwchar: 150\n")
                write(entry / "stat",
                      "4242 (uspex.x) R 1 1 1 0 -1 4194304 0 0 0 0 160 40 0 0 20 0 1 0 0 0 0 0")

            result = sample_window([4242], 1.0, proc_root=str(proc), sleep_fn=bump)
            self.assertTrue(result["sampled"])
            self.assertEqual(result["io_delta_bytes"], 400)
            self.assertAlmostEqual(float(result["cpu_busy_fraction"]), 0.8, places=3)

    def test_sampling_is_skipped_without_a_window(self) -> None:
        result = sample_window([1], 0.0, sleep_fn=lambda _seconds: None)
        self.assertFalse(result["sampled"])
        self.assertIsNone(result["io_delta_bytes"])


class SchedulerTests(unittest.TestCase):
    def test_jobs_are_attributed_by_workdir_not_name(self) -> None:
        run = Path("/runs/LiCaIBr")
        dump = "\n".join([
            "RUNNING|LiCaIBr-3|/runs/LiCaIBr/CalcFold3",
            "PENDING|USPEX-7|/runs/LiCaIBr",
            "RUNNING|LiZnIBr-1|/runs/LiZnIBr/CalcFold1",
            "COMPLETED|LiCaIBr-9|/runs/LiCaIBr/CalcFold9",
        ])
        attributed = attribute_jobs(dump, run)
        self.assertEqual(attributed["total"], 3)
        self.assertEqual(attributed["active"], 2)
        self.assertEqual(attributed["other_jobs_on_account"], 1)


class DecideTests(unittest.TestCase):
    def test_finished_wins(self) -> None:
        result = decide(evidence(finished=True, jobs_active=5))
        self.assertEqual((result["state"], result["exit_code"]), ("done", EXIT_DONE))

    def test_active_job_of_this_run_means_working(self) -> None:
        result = decide(evidence(jobs_active=12))
        self.assertEqual((result["state"], result["exit_code"]), ("working", EXIT_WORKING))

    def test_io_movement_means_working_even_when_very_old(self) -> None:
        result = decide(evidence(activity_age_seconds=400_000.0, io_delta_bytes=5_000_000))
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["exit_code"], EXIT_WORKING)

    def test_file_movement_means_working(self) -> None:
        result = decide(evidence(activity_age_seconds=400_000.0, file_moved=True))
        self.assertEqual(result["state"], "working")

    def test_quiet_within_one_cadence_is_working_not_stuck(self) -> None:
        # The measured trap: a healthy run can be silent for ~21 min and still be evolving.
        result = decide(evidence(activity_age_seconds=21 * 60.0, cadence_seconds=3600.0,
                                 io_delta_bytes=0, file_moved=False))
        self.assertEqual(result["state"], "working")
        self.assertEqual(result["warnings"], [])

    def test_quiet_past_one_cadence_warns_but_does_not_condemn(self) -> None:
        result = decide(evidence(activity_age_seconds=2 * 3600.0, cadence_seconds=3600.0))
        self.assertEqual(result["state"], "working")
        self.assertTrue(result["warnings"])

    def test_spin_with_frozen_io_far_past_cadence_is_stuck(self) -> None:
        result = decide(evidence(activity_age_seconds=34 * 3600.0, cadence_seconds=1.9 * 3600.0,
                                 io_delta_bytes=0, cpu_busy_fraction=1.0))
        self.assertEqual((result["state"], result["exit_code"]), ("stuck", EXIT_STUCK))
        joined = " ".join(result["next_steps"])
        self.assertIn("Preserve evidence", joined)
        self.assertIn("pickUp", joined)
        self.assertIn("still_running", joined)

    def test_no_engine_and_no_driver_is_stopped(self) -> None:
        result = decide(evidence(engine_count=0, driver_count=0, helper_count=0,
                                 activity_age_seconds=10 * 3600.0))
        self.assertEqual((result["state"], result["exit_code"]), ("stopped", EXIT_STOPPED))

    def test_without_process_inspection_it_never_claims_nothing_is_alive(self) -> None:
        result = decide(evidence(engine_count=0, driver_count=0, engine_checked=False,
                                 activity_age_seconds=10 * 3600.0))
        self.assertNotEqual(result["state"], "stopped")
        self.assertTrue(any("--no-proc" in warning for warning in result["warnings"]))

    def test_idle_engine_is_reported_as_waiting(self) -> None:
        result = decide(evidence(cpu_busy_fraction=0.0))
        self.assertEqual(result["state"], "working")
        self.assertTrue(any("waiting rather than computing" in warning for warning in result["warnings"]))

    def test_no_evidence_is_unknown(self) -> None:
        result = decide(evidence(activity_age_seconds=None))
        self.assertEqual((result["state"], result["exit_code"]), ("unknown", EXIT_UNKNOWN))

    def test_future_dated_artefact_is_reported_as_clock_skew(self) -> None:
        result = decide(evidence(activity_future_dated=True))
        self.assertTrue(any("future" in warning for warning in result["warnings"]))


class CollectTests(unittest.TestCase):
    def test_very_old_artefacts_without_process_inspection_are_not_called_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = build_run(root, generations=4, cadence=3600.0, newest_age=10 * 3600.0)
            report = collect(root, now=now, sample_seconds=0.0, inspect_processes=False,
                             jobs_text="", jobs_source="test", cadence_override=3600.0)
            self.assertEqual(report["state"], "stuck")  # --no-proc must not claim nothing is alive
            self.assertEqual(report["exit_code"], EXIT_STUCK)
            self.assertTrue(report["evidence_missing"])

    def test_finished_fixture_is_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = build_run(root, done=True)
            report = collect(root, now=now, sample_seconds=0.0, inspect_processes=False,
                             jobs_text="", jobs_source="test")
            self.assertEqual(report["state"], "done")
            self.assertEqual(report["exit_code"], EXIT_DONE)

    def test_healthy_quiet_fixture_is_working(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = build_run(root, generations=4, cadence=12_600.0, newest_age=1260.0)
            report = collect(root, now=now, sample_seconds=0.0, inspect_processes=False,
                             jobs_text="", jobs_source="test", cadence_override=12_600.0)
            self.assertEqual(report["state"], "working")
            self.assertEqual(report["exit_code"], EXIT_WORKING)

    def test_cli_emits_json_and_matching_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_run(root, generations=3, cadence=3600.0, newest_age=1200.0)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main([str(root), "--no-proc", "--no-scheduler", "--json",
                             "--cadence-minutes", "60"])
            payload = json.loads(buffer.getvalue())
            self.assertEqual(code, payload["exit_code"])
            self.assertEqual(payload["state"], "working")
            self.assertIn("cadence", payload)

    def test_cli_rejects_a_missing_directory(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(Path(tempfile.gettempdir(), "definitely-not-a-uspex-run")),
                         "--no-proc", "--no-scheduler"])
        self.assertEqual(code, EXIT_UNKNOWN)
        self.assertIn("not a directory", err.getvalue())


if __name__ == "__main__":
    unittest.main()
