#!/usr/bin/env python3
"""Unit tests for uspex_status.py; no cluster, USPEX installation, or scheduler needed.

The fixtures reproduce the shapes observed in real 10.5/10.6 runs: a banner inside
results<N>/OUTPUT.txt, an Individuals table whose objective column differs per
release, a Pareto_ranking table with a different enthalpy unit, a driver log, and
CalcFoldTemp/Jobs.history.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from uspex_status import (
    EXIT_DONE,
    EXIT_INTERVENE,
    EXIT_RUNNING,
    EXIT_UNKNOWN,
    assess,
    attribute_jobs,
    classify_writers,
    parse_driver_log,
    parse_individuals,
    parse_jobs_history,
    parse_output_txt,
    parse_pareto_ranking,
)

OUTPUT_TXT_DONE = """\
*------------------------------------------------------------------------------*
|                           version 10.6 (03/06/2026)                          |
*------------------------------------------------------------------------------*
            Job Starts at                 28-Sep-2026 16:05:11
----------------------- Block for evolutionary algorithm -----------------------
                 Number of Generations  :      2
               Initial Population Size  :     4
               General Population Size  :     4
    There are 2 types of atoms in the system:    Li   F
*  External Pressure is : 0.0000  GPa*
*  There are  1 local relaxation steps for each individual structure  *
  1 parallel calculations are performed simutaneously
  ID   Origin     Composition  Enthalpy(eV)  Volume(A^3)  KPOINTS  SYMMETRY
---------------------------- SUMMARY of Generation 1 ---------------------------
---------------------------- SUMMARY of Generation 2 ---------------------------
Maximum Generation number achieved: 2
This calculation runs    9 structural relaxations
The lowest target fitness is -10.2466 eV
            Job Finished at                 28-Sep-2026 16:25:50
"""

OUTPUT_TXT_RUNNING = """\
|                           version 10.6 (03/06/2026)                          |
            Job Starts at                 28-Sep-2026 17:35:31
                 Number of Generations  :      45
               Initial Population Size  :     240
               General Population Size  :     120
    There are 4 types of atoms in the system:    Li   Mg    F   Cl
*  External Pressure is : 100.0000  GPa*
  30 parallel calculations are performed simutaneously
  ID   Origin     Composition  Enthalpy(eV)  Volume(A^3)  KPOINTS  SYMMETRY
"""

INDIVIDUALS_106 = """\
Gen   ID    Origin   Composition    Enthalpy   Volume  Density  Property_X    KPOINTS  SYMM  Q_entr A_order S_order
                                      (eV)     (A^3)  (g/cm^3)
  1    1   Random    [      2    ]   -10.174    32.684   2.854   -221.000  [ 1  1  1] 221 -0.000  4.149  4.153
  1    3   Random    [      2    ]   -10.247    30.178   3.091   -191.000  [ 1  1  1] 191 -0.000  3.440  3.445
  2    5 Heredity    [      2    ]    -9.085    60.488   1.541   -123.000  [ 1  1  1] 123  0.100  1.733  1.356
"""

INDIVIDUALS_105 = """\
Gen   ID    Origin   Composition    Enthalpy   Volume  Density  ML_Bulk_Modul    KPOINTS  SYMM  Q_entr A_order S_order
                                      (eV)     (A^3)  (g/cm^3)
  1    1   Random    [     3 13  ]   -62.815    87.783   2.964    -16.001  [ 1  1  1]   1  0.188  1.733  1.356
"""

INDIVIDUALS_FITNESS = """\
Gen   ID    Origin   Composition    Enthalpy   Volume  Density   Fitness   KPOINTS  SYMM  Q_entr A_order S_order Magmoment-Type
                                      (eV)     (A^3)   (g/cm^3)
"""

PARETO_RANKING = """\
Pareto  ID   Origin     Composition     Enthalpy   Volume  Density    Property_X   ConvexHull  KPOINTS   SYMM  Q_entr A_order S_order
front                                   eV/atom    (A^3)  (g/cm^3)
  1      1    Random    [      2    ]    -5.087    32.684   2.854      221.000        0.073    [ 1  1  1] 221   -0.000  4.149  4.153
  1      3    Random    [      2    ]    -5.123    30.178   3.091      191.000        0.000    [ 1  1  1] 191   -0.000  3.440  3.445
  2      4    Random    [      2    ]    -4.953    34.100   2.700      139.000        0.120    [ 1  1  1] 139   -0.000  3.100  3.200
"""

JOBS_HISTORY = """\
Generation 1 Step 1 of Structure 11 at Calcfold11 : JOBID 1452242  Submitted Sep28-17:44:16
Generation 1 Step 1 of Structure 12 at Calcfold12 : JOBID 1452243  Submitted Sep28-17:44:16
Generation 1 Step 1 of Structure 30 at Calcfold30 : JOBID 1452261  Submitted Sep28-20:44:20
"""

DRIVER_LOG_OK = """\
[2026-09-28T17:35:18+08:00] driver start; poll=30s
[2026-09-28T17:35:18+08:00] USPEX -r start
[2026-09-28T17:40:00+08:00] USPEX -r exit=0
[2026-09-28T17:40:30+08:00] USPEX -r start
[2026-09-28T17:45:00+08:00] USPEX -r exit=0
"""

DRIVER_LOG_FAILING = """\
[2026-09-28T17:35:18+08:00] driver start; poll=30s
[2026-09-28T17:35:18+08:00] USPEX -r start
[2026-09-28T17:36:00+08:00] USPEX -r exit=1
[2026-09-28T17:36:30+08:00] USPEX -r start
[2026-09-28T17:37:00+08:00] USPEX -r exit=1
[2026-09-28T17:37:01+08:00] stopping after 2 consecutive failures
"""


def touch(path: Path, text: str, age_seconds: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_seconds:
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))


def make_run(
    root: Path,
    *,
    output_txt: str | None = OUTPUT_TXT_DONE,
    marker: bool = False,
    driver_log: str | None = None,
    individuals: str | None = None,
    pareto: str | None = None,
    jobs_history: str | None = None,
    age_seconds: float = 0.0,
    results_name: str = "results1",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    touch(root / "INPUT.txt", "USPEX : calculationMethod\n", age_seconds)
    if output_txt is not None:
        touch(root / results_name / "OUTPUT.txt", output_txt, age_seconds)
    if individuals is not None:
        touch(root / results_name / "Individuals", individuals, age_seconds)
    if pareto is not None:
        touch(root / results_name / "Pareto_ranking", pareto, age_seconds)
    if marker:
        touch(root / "USPEX_IS_DONE", "1\n", age_seconds)
    if driver_log is not None:
        touch(root / "uspex-driver.log", driver_log, age_seconds)
    if jobs_history is not None:
        touch(root / "CalcFoldTemp" / "Jobs.history", jobs_history, age_seconds)
    return root


class OutputParsingTests(unittest.TestCase):
    def test_version_and_contract_are_read(self) -> None:
        info = parse_output_txt(OUTPUT_TXT_DONE)
        self.assertEqual(info["uspex_version"], "10.6")
        self.assertEqual(info["uspex_release_date"], "03/06/2026")
        self.assertEqual(info["configured_generations"], 2)
        self.assertEqual(info["initial_population_size"], 4)
        self.assertEqual(info["types_of_atoms"], 2)
        self.assertEqual(info["total_relaxations"], 9)
        self.assertEqual(info["completed_generations"], [1, 2])
        self.assertEqual(info["max_generation_achieved"], 2)
        self.assertEqual(info["job_finished_at"], "28-Sep-2026 16:25:50")
        self.assertIsNone(info["current_generation"])

    def test_running_run_has_no_completion_evidence(self) -> None:
        info = parse_output_txt(OUTPUT_TXT_RUNNING)
        self.assertEqual(info["configured_generations"], 45)
        self.assertEqual(info["initial_population_size"], 240)
        self.assertEqual(info["num_parallel_calcs"], 30)
        self.assertEqual(info["external_pressure_gpa"], 100.0)
        self.assertEqual(info["completed_generations"], [])
        self.assertIsNone(info["job_finished_at"])
        self.assertIsNone(info["max_generation_achieved"])

    def test_crlf_output_is_parsed(self) -> None:
        info = parse_output_txt(OUTPUT_TXT_DONE.replace("\n", "\r\n"))
        self.assertEqual(info["completed_generations"], [1, 2])


class TableParsingTests(unittest.TestCase):
    def test_individuals_objective_column_per_release(self) -> None:
        current = parse_individuals(INDIVIDUALS_106)
        self.assertEqual(current["rows"], 3)
        self.assertEqual(current["objective_columns"], ["Property_X"])
        self.assertEqual(current["generations"], [1, 2])

        legacy = parse_individuals(INDIVIDUALS_105)
        self.assertEqual(legacy["rows"], 1)
        self.assertEqual(legacy["objective_columns"], ["ML_Bulk_Modul"])

    def test_individuals_header_only_counts_zero_rows(self) -> None:
        info = parse_individuals(INDIVIDUALS_FITNESS)
        self.assertEqual(info["rows"], 0)
        self.assertEqual(info["objective_columns"], ["Fitness"])

    def test_pareto_ranking_rows_and_fronts(self) -> None:
        info = parse_pareto_ranking(PARETO_RANKING)
        self.assertEqual(info["rows"], 3)
        self.assertEqual(info["fronts"], [1, 2])
        self.assertEqual(info["ids"], 3)

    def test_driver_log_failure_count(self) -> None:
        info = parse_driver_log(DRIVER_LOG_FAILING)
        self.assertTrue(info["stopping"])
        self.assertEqual(info["consecutive_failures"], 2)
        self.assertEqual(info["last_exit_code"], 1)

        healthy = parse_driver_log(DRIVER_LOG_OK)
        self.assertFalse(healthy["stopping"])
        self.assertEqual(healthy["starts"], 2)
        self.assertEqual(healthy["last_exit_code"], 0)

    def test_jobs_history_last_entry(self) -> None:
        info = parse_jobs_history(JOBS_HISTORY)
        self.assertEqual(info["submissions"], 3)
        self.assertEqual(info["last"]["job_id"], "1452261")
        self.assertEqual(info["last"]["structure"], 30)
        self.assertEqual(info["last"]["generation"], 1)


class JobAttributionTests(unittest.TestCase):
    def test_attribution_uses_workdir_not_job_name(self) -> None:
        run_dir = Path("/home/researcher/work/run-a")
        jobs = [
            {"state": "PENDING", "name": "U106LiD-1", "workdir": "/home/researcher/work/run-a/CalcFold1"},
            {"state": "RUNNING", "name": "USPEX-9", "workdir": "/home/researcher/work/run-a/CalcFold2"},
            {"state": "PENDING", "name": "U106LiD-7", "workdir": "/home/researcher/work/other/CalcFold7"},
            {"state": "PENDING", "name": "USPEX-3", "workdir": ""},
        ]
        attributed = attribute_jobs(jobs, run_dir)
        self.assertEqual(attributed["total"], 2)
        self.assertEqual(attributed["by_state"], {"PENDING": 1, "RUNNING": 1})
        self.assertEqual(attributed["active"], 2)
        self.assertEqual(attributed["other_jobs_on_account"], 1)
        self.assertEqual(attributed["jobs_without_workdir"], 1)

    def test_foreign_failed_job_does_not_count_as_ours(self) -> None:
        run_dir = Path("/home/researcher/work/run-a")
        jobs = [{"state": "FAILED", "name": "USPEX-1", "workdir": "/home/researcher/work/other/CalcFold1"}]
        attributed = attribute_jobs(jobs, run_dir)
        self.assertEqual(attributed["total"], 0)
        self.assertEqual(attributed["failed"], 0)


class WriterTests(unittest.TestCase):
    def test_two_engines_raise_risk(self) -> None:
        run_dir = Path("/home/researcher/work/run-a")
        dump = "\n".join(
            [
                "111\t/home/researcher/work/run-a\t/usr/bin/USPEX -r",
                "222\t/home/researcher/work/run-a\t/usr/bin/USPEX -r",
            ]
        )
        from uspex_status import parse_process_dump

        writers = classify_writers(parse_process_dump(dump), run_dir)
        self.assertEqual(writers["engine_count"], 2)
        self.assertTrue(writers["risk"])

    def test_driver_plus_single_engine_is_normal(self) -> None:
        run_dir = Path("/home/researcher/work/run-a")
        dump = "\n".join(
            [
                "111\t/home/researcher/work/run-a\t/usr/bin/USPEX -r",
                "222\t/home/researcher/work/run-a\tbash ./run_uspex_loop.sh",
            ]
        )
        from uspex_status import parse_process_dump

        writers = classify_writers(parse_process_dump(dump), run_dir)
        self.assertEqual(writers["engine_count"], 1)
        self.assertEqual(writers["driver_count"], 1)
        self.assertFalse(writers["risk"])


class StateMachineTests(unittest.TestCase):
    def test_marker_present_is_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), marker=True)
            report = assess(root, now=time.time())
            self.assertEqual(report.state, "done")
            self.assertEqual(report.phase, "done")
            self.assertEqual(report.exit_code, EXIT_DONE)

    def test_completion_inferred_from_output_txt_without_marker(self) -> None:
        """Regression: a results bundle omits USPEX_IS_DONE but is finished."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), marker=False, age_seconds=7200)
            report = assess(root, now=time.time())
            self.assertEqual(report.state, "done")
            self.assertEqual(report.phase, "done_inferred")
            self.assertEqual(report.exit_code, EXIT_DONE)
            self.assertEqual(report.progress["completion_inferred"], True)
            self.assertTrue(any("USPEX_IS_DONE is absent" in item.message for item in report.warnings))

    def test_generation_ceiling_alone_counts_as_completion(self) -> None:
        text = OUTPUT_TXT_DONE.replace(
            "            Job Finished at                 28-Sep-2026 16:25:50\n", ""
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=text, age_seconds=7200)
            report = assess(root, now=time.time())
            self.assertEqual(report.state, "done")

    def test_running_with_pending_jobs_is_not_stalled(self) -> None:
        """Queue waiting must not be reported as a stalled calculation."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(
                Path(tmp),
                output_txt=OUTPUT_TXT_RUNNING,
                driver_log=DRIVER_LOG_OK,
                age_seconds=7200,
            )
            jobs = f"PENDING|U106LiD-1|{root}/CalcFold1\nRUNNING|U106LiD-2|{root}/CalcFold2\n"
            report = assess(root, jobs_text=jobs, jobs_source="test", now=time.time())
            self.assertEqual(report.state, "running")
            self.assertEqual(report.exit_code, EXIT_RUNNING)
            self.assertEqual(report.jobs["total"], 2)
            self.assertEqual(report.phase, "initial_population_in_progress")

    def test_next_generation_is_inferred_when_no_banner_is_printed(self) -> None:
        """Later USPEX -r batches print no '---- Generation N ----' banner."""
        text = OUTPUT_TXT_DONE.replace("Maximum Generation number achieved: 2\n", "").replace(
            "            Job Finished at                 28-Sep-2026 16:25:50\n", ""
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=text, driver_log=DRIVER_LOG_OK)
            touch(root / "CalcFold1" / "energy.txt", "-10.5\n")
            jobs = f"RUNNING|U106LiD-1|{root}/CalcFold1\n"
            report = assess(root, jobs_text=jobs, jobs_source="test", now=time.time())
            self.assertEqual(report.state, "running")
            self.assertEqual(report.phase, "generation_3_in_progress")

    def test_vasp_run_without_energy_txt_is_explained_not_zeroed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_OK)
            touch(root / "CalcFold1" / "geom.in", "x\n")
            report = assess(root, jobs_text="", jobs_source="test", now=time.time())
            self.assertEqual(report.structures["calc_folds"], 1)
            self.assertTrue(any("no energy.txt in this run" in item.message for item in report.warnings))

    def test_stalled_when_nothing_changes_and_no_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(
                Path(tmp),
                output_txt=OUTPUT_TXT_RUNNING,
                driver_log=DRIVER_LOG_OK,
                age_seconds=7200,
            )
            report = assess(root, jobs_text="", jobs_source="test", now=time.time())
            self.assertEqual(report.state, "stalled")
            self.assertEqual(report.exit_code, EXIT_INTERVENE)

    def test_fresh_activity_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_OK)
            report = assess(root, jobs_text="", jobs_source="test", now=time.time())
            self.assertEqual(report.state, "running")
            self.assertEqual(report.exit_code, EXIT_RUNNING)

    def test_empty_directory_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = assess(Path(tmp), now=time.time())
            self.assertEqual(report.state, "unknown")
            self.assertEqual(report.exit_code, EXIT_UNKNOWN)
            self.assertTrue(any("no run artefacts" in item.message for item in report.warnings))

    def test_input_only_directory_is_unknown_and_lists_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "INPUT.txt", "USPEX : calculationMethod\n")
            report = assess(root, now=time.time())
            self.assertEqual(report.exit_code, EXIT_UNKNOWN)
            self.assertIn("results*/OUTPUT.txt", report.evidence_missing)

    def test_driver_failure_is_intervention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_FAILING)
            report = assess(root, now=time.time())
            self.assertEqual(report.state, "failed")
            self.assertEqual(report.phase, "driver_failed")
            self.assertEqual(report.exit_code, EXIT_INTERVENE)

    def test_failed_jobs_without_active_jobs_is_intervention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_OK)
            jobs = f"FAILED|U106LiD-1|{root}/CalcFold1\n"
            report = assess(root, jobs_text=jobs, jobs_source="test", now=time.time())
            self.assertEqual(report.state, "failed")
            self.assertEqual(report.phase, "structure_jobs_failed")
            self.assertEqual(report.exit_code, EXIT_INTERVENE)

    def test_queued_jobs_not_attributed_to_this_run_do_not_mask_staleness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(
                Path(tmp),
                output_txt=OUTPUT_TXT_RUNNING,
                driver_log=DRIVER_LOG_OK,
                age_seconds=7200,
            )
            jobs = "PENDING|USPEX-9|/home/researcher/work/other/CalcFold9\n"
            report = assess(root, jobs_text=jobs, jobs_source="test", now=time.time())
            self.assertEqual(report.state, "stalled")
            self.assertEqual(report.jobs["total"], 0)


class StructureEvidenceTests(unittest.TestCase):
    def test_sentinel_energy_is_counted_and_warned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_OK)
            touch(root / "CalcFold1" / "geom.in", "x\n")
            touch(root / "CalcFold1" / "energy.txt", "999999\n")
            touch(root / "CalcFold2" / "energy.txt", "-10.5\n")
            touch(root / "CalcFold3" / "energy.txt", "not-a-number\n")
            # CalcFoldTemp exists in every real run and must never be counted as a structure.
            touch(root / "CalcFoldTemp" / "Jobs.history", "Generation 1 Step 1 of Structure 1\n")
            report = assess(root, jobs_text="", jobs_source="test", now=time.time())
            self.assertEqual(report.structures["calc_folds"], 3)
            self.assertEqual(report.structures["sentinel_energy"], 1)
            self.assertEqual(report.structures["unparsable_energy"], 1)
            self.assertEqual(report.structures["with_energy_txt"], 2)
            self.assertTrue(any("sentinel energy" in item.message for item in report.warnings))

    def test_missing_calc_folds_are_noted_not_silently_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(Path(tmp), output_txt=OUTPUT_TXT_RUNNING, driver_log=DRIVER_LOG_OK)
            report = assess(root, jobs_text="", jobs_source="test", now=time.time())
            self.assertTrue(any("no CalcFold* directories" in note for note in report.notes))

    def test_enthalpy_unit_note_when_both_tables_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(
                Path(tmp),
                output_txt=OUTPUT_TXT_DONE,
                individuals=INDIVIDUALS_106,
                pareto=PARETO_RANKING,
            )
            report = assess(root, now=time.time())
            self.assertTrue(any("eV/atom" in note for note in report.notes))
            self.assertEqual(report.individuals["objective_columns"], ["Property_X"])
            self.assertEqual(report.pareto["rows"], 3)

    def test_script_style_workdir_is_never_used_as_a_join_key(self) -> None:
        """Guard: the report must not claim per-structure attribution it cannot prove."""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_run(
                Path(tmp),
                output_txt=OUTPUT_TXT_DONE,
                individuals=INDIVIDUALS_106,
                pareto=PARETO_RANKING,
            )
            payload = json.dumps(assess(root, now=time.time()).to_dict())
            self.assertNotIn('"joined"', payload)
            self.assertIn("normalising by the composition", payload)


if __name__ == "__main__":
    unittest.main()
