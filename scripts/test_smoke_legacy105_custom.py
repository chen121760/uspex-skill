#!/usr/bin/env python3
"""Unit tests for smoke_legacy105_custom.py.

The full CLI is exercised through ``smoke.main`` so the exit codes, the JSON
report and the embedded adapters are covered end to end; the individual
classifiers are covered directly with small inputs. Full runs are cached by
argument tuple because each one spawns a real subprocess per case.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_legacy105_custom as smoke


# Cases whose contract behaviour is "fatal": non-zero exit, no <CALLRESULT>.
FATAL_CASES = (
    "missing_p1",
    "missing_middle_p4",
    "missing_p7",
    "empty_slot_file",
    "whitespace_only_slot_file",
    "nonnumeric_slot",
    "unit_suffix_slot",
    "nan_slot",
    "inf_slot",
    "negative_inf_slot",
    "overflow_slot",
    "two_tokens_in_slot",
    "header_line_in_slot",
)

# Cases that must be accepted (a finite negative value is a legal penalty).
# These pass in both the strict and the --compat-legacy run.
ACCEPTED_CASES = (
    "happy_path",
    "happy_path_with_model_arg",
    "scientific_notation_slot",
    "negative_penalty_slot",
    "negative_zero_slot",
    "extra_p8_ignored",
    "stale_property_files",
    "wrong_ordering",
    "properties_without_geom_out",
    "energy_txt_missing",
    "energy_txt_non_numeric",
    "probe_truncated_stdout",
    "probe_extra_trailing_token",
    "probe_missing_callresult",
    "probe_nonzero_exit_valid_line",
    "probe_nonfinite_value",
    "probe_nan_text",
)

# Missing geom.out is fatal for the strict adapter and is characterized for the
# legacy one, which answers with seven silent zeros instead of refusing.
GEOM_OUT_MISSING_CASE = "geom_out_missing"

PAYLOAD_KEYS = {
    "schema",
    "adapter",
    "adapter_resolved",
    "compat_legacy",
    "require_fresh",
    "python",
    "argv",
    "cases",
    "summary",
    "ok",
}

CASE_KEYS = {
    "name",
    "expected",
    "observed",
    "result",
    "detail",
    "exit_code",
    "stdout",
    "stderr",
    "argv",
}

SCHEMA = "uspex-legacy105-smoke/2"

RUNNER = Path(smoke.__file__).resolve()
REPO_ROOT = RUNNER.parent.parent

_RUN_CACHE = {}


def run_cli(*extra: str):
    """Run ``smoke.main`` once per argument tuple, returning (code, payload, out, err)."""
    key = tuple(extra)
    if key not in _RUN_CACHE:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = smoke.main(["--json", str(report_path), *extra])
            payload = None
            if report_path.exists():
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            _RUN_CACHE[key] = (code, payload, stdout.getvalue(), stderr.getvalue())
    return _RUN_CACHE[key]


def run_cli_uncached(*extra: str):
    """Same as :func:`run_cli` but never reuses a previous result (temp adapters)."""
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = smoke.main(["--json", str(report_path), *extra])
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    return code, payload, stdout.getvalue(), stderr.getvalue()


def case_by_name(payload: dict, name: str) -> dict:
    for entry in payload["cases"]:
        if entry["name"] == name:
            return entry
    raise AssertionError("case %r is missing from the report" % name)


class CaseCatalogueTests(unittest.TestCase):
    def test_case_list_is_nonempty_and_unique(self):
        names = list(smoke.CASE_NAMES)
        self.assertTrue(names)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), len({case.name for case in smoke.CASE_SPECS}))

    def test_list_cases_prints_one_name_per_line_and_exits_zero(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = smoke.main(["--list-cases"])
        self.assertEqual(code, 0)
        printed = stdout.getvalue().splitlines()
        self.assertEqual(printed, list(smoke.CASE_NAMES))

    def test_resolve_cases_rejects_unknown_names(self):
        with self.assertRaises(smoke.UsageError):
            smoke.resolve_cases(["definitely_not_a_case"])

    def test_resolve_cases_preserves_definition_order(self):
        selected = smoke.resolve_cases(["wrong_ordering", "happy_path", "wrong_ordering"])
        self.assertEqual([case.name for case in selected], ["happy_path", "wrong_ordering"])

    def test_every_case_declares_an_expected_string(self):
        for case in smoke.CASE_SPECS:
            self.assertTrue(case.expected, case.name)
            if case.kind == "values":
                self.assertIsNotNone(case.expected_values(), case.name)
            if case.kind in ("handshake", "fatal_and_handshake"):
                self.assertTrue(case.handshake_pattern, case.name)
            if case.kind == "detect_output_fault":
                self.assertTrue(case.problem_contains, case.name)


class StrictReferenceRunTests(unittest.TestCase):
    def setUp(self):
        self.code, self.payload, self.stdout, self.stderr = run_cli()

    def test_default_run_exits_zero_with_no_failures(self):
        self.assertEqual(self.code, 0)
        self.assertTrue(self.payload["ok"])
        self.assertEqual(self.payload["summary"]["failed"], 0)
        self.assertEqual(self.payload["summary"]["skipped"], 0)
        self.assertEqual(self.payload["summary"]["characterized"], 0)
        self.assertEqual(self.payload["summary"]["passed"], len(smoke.CASE_NAMES))
        self.assertEqual(self.payload["compat_legacy"], False)
        self.assertEqual(self.payload["require_fresh"], False)
        self.assertEqual(self.payload["adapter"], smoke.ADAPTER_LABEL_STRICT)

    def test_report_lists_every_case_exactly_once(self):
        names = [entry["name"] for entry in self.payload["cases"]]
        self.assertEqual(names, list(smoke.CASE_NAMES))

    def test_report_prints_the_case_table_and_one_line_summary(self):
        self.assertIn("CASE", self.stdout)
        self.assertIn("EXPECTED", self.stdout)
        self.assertIn("OBSERVED", self.stdout)
        self.assertIn("RESULT", self.stdout)
        self.assertIn(
            "SUMMARY: %d passed, 0 failed, 0 skipped" % len(smoke.CASE_NAMES), self.stdout
        )
        self.assertNotIn("FAIL:", self.stderr)

    def test_happy_path_emits_the_exact_expected_line(self):
        entry = case_by_name(self.payload, "happy_path")
        self.assertEqual(entry["result"], "pass")
        self.assertEqual(entry["exit_code"], 0)
        self.assertEqual(entry["observed"], "<CALLRESULT> 11 22 33 44 55 66 77")
        self.assertEqual(entry["stdout"].strip(), "<CALLRESULT> 11 22 33 44 55 66 77")

    def test_misconfiguration_cases_are_fatal_without_a_callresult_line(self):
        for name in FATAL_CASES:
            entry = case_by_name(self.payload, name)
            self.assertEqual(entry["result"], "pass", name)
            self.assertNotEqual(entry["exit_code"], 0, name)
            self.assertNotIn("<CALLRESULT>", entry["stdout"], name)
            self.assertTrue(entry["stderr"].strip(), name)

    def test_legal_values_and_handshake_detection_pass(self):
        for name in ACCEPTED_CASES:
            entry = case_by_name(self.payload, name)
            self.assertEqual(entry["result"], "pass", name)
        # geom.out missing is fatal AND a handshake rejection in strict mode.
        entry = case_by_name(self.payload, GEOM_OUT_MISSING_CASE)
        self.assertEqual(entry["result"], "pass")
        self.assertNotEqual(entry["exit_code"], 0)
        self.assertIn("geom.out", entry["detail"])

    def test_wrong_ordering_is_reported_as_a_mapping_mismatch(self):
        entry = case_by_name(self.payload, "wrong_ordering")
        self.assertEqual(entry["observed"], "<CALLRESULT> 22 11 33 44 55 66 77")
        self.assertIn("mapping mismatch", entry["detail"])
        self.assertIn("nominal", entry["detail"])

    def test_stale_files_warn_by_default(self):
        entry = case_by_name(self.payload, "stale_property_files")
        self.assertEqual(entry["result"], "pass")
        self.assertIn("WARN:", entry["detail"])
        self.assertIn("older than geom.in", entry["detail"])
        self.assertIn("WARN stale_property_files", self.stdout)

    def test_require_fresh_rejects_stale_files(self):
        code, payload, _, _ = run_cli(
            "--require-fresh", "--case", "stale_property_files", "--case", "happy_path"
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["require_fresh"])
        stale = case_by_name(payload, "stale_property_files")
        self.assertEqual(stale["result"], "pass")
        self.assertIn("older than geom.in", stale["expected"])
        self.assertIn("older than geom.in", stale["detail"])
        self.assertEqual(case_by_name(payload, "happy_path")["result"], "pass")

    def test_json_payload_schema_is_exact(self):
        self.assertEqual(set(self.payload), PAYLOAD_KEYS)
        self.assertEqual(self.payload["schema"], SCHEMA)
        self.assertEqual(set(self.payload["summary"]), {"passed", "failed", "skipped", "characterized"})
        for entry in self.payload["cases"]:
            self.assertEqual(set(entry), CASE_KEYS, entry["name"])

    def test_embedded_run_records_the_executed_paths_and_argv(self):
        # For the embedded adapters the label is the "as given" value and
        # adapter_resolved is the temp file written from the embedded source.
        self.assertEqual(self.payload["adapter"], smoke.ADAPTER_LABEL_STRICT)
        resolved = Path(self.payload["adapter_resolved"])
        self.assertTrue(resolved.is_absolute(), resolved)
        happy = case_by_name(self.payload, "happy_path")
        self.assertEqual(happy["argv"][1], self.payload["adapter_resolved"])
        self.assertEqual(happy["argv"][0], self.payload["python"])
        self.assertEqual(self.payload["argv"], happy["argv"])
        for entry in self.payload["cases"]:
            executed = Path(entry["argv"][1])
            self.assertTrue(executed.is_absolute(), entry["name"])
            self.assertTrue(Path(entry["argv"][2]).is_absolute(), entry["name"])
            if entry["name"].startswith("probe_"):
                # Harness self-check probes run their own embedded probe adapter.
                self.assertEqual(executed.name, "probe_%s.py" % entry["name"])
            else:
                self.assertEqual(entry["argv"][1], self.payload["adapter_resolved"], entry["name"])

    def test_run_smoke_api_returns_a_clean_payload(self):
        # Programmatic entry point: it owns its temp tree and returns the same
        # payload the CLI writes to --json.
        args = smoke.build_parser().parse_args(["--case", "happy_path", "--case", "missing_p1"])
        payload = smoke.run_smoke(args)
        self.assertEqual(set(payload), PAYLOAD_KEYS)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"], {
            "passed": 2, "failed": 0, "skipped": 0, "characterized": 0,
        })
        self.assertEqual(case_by_name(payload, "happy_path")["observed"],
                         "<CALLRESULT> 11 22 33 44 55 66 77")

    def test_json_report_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "report.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = smoke.main(["--json", str(target), "--case", "happy_path"])
            self.assertEqual(code, 0)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], SCHEMA)
            leftovers = [item.name for item in target.parent.iterdir() if item.name != target.name]
            self.assertEqual(leftovers, [])


class CompatLegacyRunTests(unittest.TestCase):
    def setUp(self):
        self.code, self.payload, self.stdout, self.stderr = run_cli("--compat-legacy")

    def test_compat_run_exits_zero_and_characterizes_the_fallback(self):
        self.assertEqual(self.code, 0)
        self.assertTrue(self.payload["ok"])
        self.assertEqual(self.payload["summary"]["failed"], 0)
        self.assertEqual(self.payload["summary"]["skipped"], 0)
        self.assertEqual(self.payload["adapter"], smoke.ADAPTER_LABEL_LEGACY)
        self.assertTrue(self.payload["compat_legacy"])
        self.assertTrue(Path(self.payload["adapter_resolved"]).is_absolute())

    def test_fatal_cases_become_characterized_not_contract_violations(self):
        for name in FATAL_CASES:
            entry = case_by_name(self.payload, name)
            self.assertEqual(entry["result"], "characterized", name)
            self.assertEqual(entry["exit_code"], 0, name)
            self.assertIn("<CALLRESULT>", entry["stdout"], name)
        self.assertEqual(
            case_by_name(self.payload, "geom_out_missing")["result"], "characterized"
        )

    def test_silent_zero_fallback_is_flagged_as_dangerous(self):
        # A missing or unreadable slot silently becoming 0.0 is the dangerous
        # legacy behaviour: 0.0 is the best possible value for a minimisation
        # objective, so a failed calculation looks like a perfect structure.
        for name in ("missing_p1", "missing_middle_p4", "missing_p7", "empty_slot_file",
                     "whitespace_only_slot_file", "nonnumeric_slot", "unit_suffix_slot",
                     "two_tokens_in_slot", "header_line_in_slot"):
            entry = case_by_name(self.payload, name)
            self.assertIn("DANGEROUS:", entry["detail"], name)
            self.assertIn("silently became 0.0", entry["detail"], name)
        missing = case_by_name(self.payload, "missing_p1")
        self.assertEqual(missing["observed"], "<CALLRESULT> 0 22 33 44 55 66 77")
        self.assertIn("DANGER missing_p1", self.stdout)

    def test_nonfinite_slot_is_reported_as_dangerous(self):
        for name in ("nan_slot", "inf_slot", "negative_inf_slot", "overflow_slot"):
            entry = case_by_name(self.payload, name)
            self.assertEqual(entry["result"], "characterized", name)
            self.assertIn("non-finite", entry["detail"], name)

    def test_legal_values_still_pass_in_compat_mode(self):
        for name in ACCEPTED_CASES:
            entry = case_by_name(self.payload, name)
            self.assertEqual(entry["result"], "pass", name)
        self.assertGreaterEqual(self.payload["summary"]["characterized"], len(FATAL_CASES))


class ValueClassifierTests(unittest.TestCase):
    ACCEPTED = ("1", "-1.5", "1e-3", "+2.", "12.3", "-0.0", "1.5e-3", " 11 \n")
    REJECTED = ("", " ", "abc", "nan", "NaN", "inf", "Infinity", "-inf", "1 2", "12.3 eV")

    def test_classifier_accepts_plain_finite_numbers(self):
        for text in self.ACCEPTED:
            self.assertTrue(smoke.is_strict_number(text), text)
            self.assertIsInstance(smoke.parse_property_value(text), float, text)

    def test_classifier_rejects_misconfiguration(self):
        for text in self.REJECTED:
            self.assertFalse(smoke.is_strict_number(text), text)
            with self.assertRaises(smoke.ContractViolation, msg=text):
                smoke.parse_property_value(text)

    def test_classifier_distinguishes_the_failure_kinds(self):
        self.assertEqual(smoke.classify_value("")[0], "empty")
        self.assertEqual(smoke.classify_value("  \t\n")[0], "empty")
        self.assertEqual(smoke.classify_value("abc")[0], "syntax")
        self.assertEqual(smoke.classify_value("NaN")[0], "syntax")
        self.assertEqual(smoke.classify_value("Inf")[0], "syntax")
        self.assertEqual(smoke.classify_value("1 2")[0], "syntax")
        # 1e999 matches the plain-number syntax but overflows to inf, so only a
        # finite check on the parsed value catches it.
        self.assertEqual(smoke.classify_value("1e999")[0], "nonfinite")
        kind, value, detail = smoke.classify_value("-42.5")
        self.assertEqual((kind, value, detail), ("ok", -42.5, None))

    def test_adversarial_texts_document_the_chosen_behaviour(self):
        # Documented hardening beyond Python's own float() parser:
        #   * float('1_000') == 1000.0, but an underscore separator is not a
        #     plain number and is rejected;
        #   * str.strip() does not remove a UTF-8 BOM, so a BOM-prefixed number
        #     is rejected instead of being silently accepted on one platform;
        #   * both are fatal, never a silent 0.0 fallback.
        for text in ("1_000", "\ufeff11", "1_0.5", "+ 1", "1e", "0x10"):
            kind, value, _ = smoke.classify_value(text)
            self.assertEqual(kind, "syntax", text)
            self.assertIsNone(value, text)
            self.assertFalse(smoke.is_strict_number(text), text)

    def test_read_property_slot_classifies_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "P1.txt"
            good.write_text("11\n", encoding="utf-8")
            empty = root / "P2.txt"
            empty.write_text("", encoding="utf-8")
            self.assertEqual(smoke.read_property_slot(good)[:2], ("ok", 11.0))
            self.assertEqual(smoke.read_property_slot(empty)[0], "empty")
            self.assertEqual(smoke.read_property_slot(root / "P3.txt")[0], "absent")
            underscored = root / "P4.txt"
            underscored.write_text("1_000\n", encoding="utf-8")
            self.assertEqual(smoke.read_property_slot(underscored)[0], "syntax")
            bom = root / "P5.txt"
            bom.write_text("\ufeff11\n", encoding="utf-8")
            self.assertEqual(smoke.read_property_slot(bom)[0], "syntax")

    def test_read_property_slot_rejects_a_directory_instead_of_a_file(self):
        # A directory (or a symlink to one) in a slot must be fatal, not a
        # silent default: open() raises OSError on every platform here.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "P4.txt"
            target.mkdir()
            kind, value, detail = smoke.read_property_slot(target)
            self.assertEqual(kind, "unreadable")
            self.assertIsNone(value)
            self.assertTrue(detail)

    def test_read_property_slot_rejects_a_directory_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real_directory"
            target.mkdir()
            link = Path(tmp) / "P4.txt"
            try:
                os.symlink(str(target), str(link), target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError):
                self.skipTest("this platform cannot create directory symlinks")
            kind, value, _ = smoke.read_property_slot(link)
            self.assertEqual(kind, "unreadable")
            self.assertIsNone(value)
            # Path.is_file() follows the link and would call this "absent", which
            # is why read_property_slot opens the path instead of stat-ing it.
            self.assertFalse(link.is_file())


class HandshakeInspectionTests(unittest.TestCase):
    def make_complete(self, root: Path) -> None:
        (root / smoke.GEOM_IN).write_text(smoke.POSCAR_PLACEHOLDER, encoding="utf-8")
        (root / smoke.GEOM_OUT).write_text(smoke.POSCAR_PLACEHOLDER, encoding="utf-8")
        (root / smoke.ENERGY_TXT).write_text("-12.5\n", encoding="utf-8")
        for index in range(1, smoke.SLOT_COUNT + 1):
            (root / smoke.slot_name(index)).write_text("1\n", encoding="utf-8")

    def messages(self, findings):
        return " || ".join(finding.message for finding in findings)

    def test_complete_directory_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_complete(root)
            self.assertEqual(smoke.inspect_directory(root), [])

    def test_missing_geom_out_and_energy_txt_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / smoke.GEOM_IN).write_text(smoke.POSCAR_PLACEHOLDER, encoding="utf-8")
            findings = smoke.inspect_directory(root)
            self.assertTrue(all(finding.level == "error" for finding in findings))
            self.assertIn("geom.out", self.messages(findings))
            self.assertIn("energy.txt", self.messages(findings))

    def test_empty_geom_out_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_complete(root)
            (root / smoke.GEOM_OUT).write_text("", encoding="utf-8")
            findings = smoke.inspect_directory(root)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].level, "error")
            self.assertIn("geom.out", findings[0].message)

    def test_empty_energy_txt_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_complete(root)
            (root / smoke.ENERGY_TXT).write_text("", encoding="utf-8")
            findings = smoke.inspect_directory(root)
            self.assertEqual(len(findings), 1)
            self.assertIn("energy.txt", findings[0].message)

    def test_non_numeric_energy_txt_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_complete(root)
            (root / smoke.ENERGY_TXT).write_text("not-a-number\n", encoding="utf-8")
            findings = smoke.inspect_directory(root)
            self.assertEqual(len(findings), 1)
            self.assertIn("energy.txt", findings[0].message)
            self.assertIn("finite number", findings[0].message)

    def test_stale_slots_warn_by_default_and_fail_with_require_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_complete(root)
            stamp = root / smoke.GEOM_IN
            os.utime(str(stamp), ns=(smoke.BASE_MTIME_NS, smoke.BASE_MTIME_NS))
            for index in range(1, smoke.SLOT_COUNT + 1):
                path = root / smoke.slot_name(index)
                stale = smoke.BASE_MTIME_NS - 1
                os.utime(str(path), ns=(stale, stale))
            default = smoke.inspect_directory(root)
            self.assertEqual([finding.level for finding in default], ["warn"])
            self.assertIn("older than geom.in", default[0].message)
            fresh = smoke.inspect_directory(root, require_fresh=True)
            self.assertEqual([finding.level for finding in fresh], ["error"])
            self.assertIn("older than geom.in", fresh[0].message)


class EmbeddedAdapterTests(unittest.TestCase):
    def run_adapter(self, adapter_path: Path, slot_text):
        """Run one adapter in a scratch directory with a given P4.txt state."""
        workdir = Path(tempfile.mkdtemp(prefix="smoke-unit-"))
        try:
            case = smoke.CaseSpec(
                "adversarial", "fatal", smoke.Scenario(slots={4: slot_text}), "fatal"
            )
            return smoke.run_case(
                case, adapter_path, Path(sys.executable), workdir, 30.0, False
            )
        finally:
            shutil.rmtree(str(workdir), ignore_errors=True)

    def test_strict_adapter_agrees_with_the_value_classifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "reference_adapter.py"
            adapter.write_text(smoke.STRICT_ADAPTER, encoding="utf-8")
            for text in ("11\n", "-42.5\n", "1.5e-3\n", "1_000\n", "\ufeff11\n", "1e999\n",
                         "nan\n", " \n", "", None):
                observation = self.run_adapter(adapter, text)
                judgement = observation["judgement"]
                expected_ok = text is not None and smoke.is_strict_number(text)
                self.assertEqual(judgement["ok"], expected_ok, repr(text))
                self.assertEqual(judgement["has_callresult"], expected_ok, repr(text))
                if not expected_ok:
                    self.assertNotEqual(observation["exit_code"], 0, repr(text))
                    self.assertTrue(observation["stderr"].strip(), repr(text))

    def test_strict_adapter_ignores_extra_slot_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "reference_adapter.py"
            adapter.write_text(smoke.STRICT_ADAPTER, encoding="utf-8")
            workdir = Path(tempfile.mkdtemp(prefix="smoke-unit-"))
            try:
                case = smoke.CaseSpec(
                    "extra", "values", smoke.Scenario(extra_files={"P8.txt": "88\n"}),
                    "exactly seven values",
                )
                observation = smoke.run_case(
                    case, adapter, Path(sys.executable), workdir, 30.0, False
                )
                self.assertTrue(observation["judgement"]["ok"])
                self.assertEqual(len(observation["judgement"]["values"]), smoke.SLOT_COUNT)
                self.assertEqual(
                    observation["judgement"]["line"], "<CALLRESULT> 11 22 33 44 55 66 77"
                )
            finally:
                shutil.rmtree(str(workdir), ignore_errors=True)


class UsageAndSkipTests(unittest.TestCase):
    def test_missing_adapter_path_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no_such_adapter.py"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = smoke.main(["--adapter", str(missing)])
            self.assertEqual(code, 2)
            message = stderr.getvalue()
            self.assertIn("--adapter not found", message)
            self.assertIn("given", message)
            self.assertIn("resolved to", message)
            self.assertIn(str(missing), message)
            self.assertIn(str(missing.resolve()), message)

    def test_relative_missing_adapter_names_given_and_resolved_paths(self):
        # A relative --adapter that does not exist must be a usage error naming
        # both paths; it must never continue and let the fatal cases pass
        # vacuously.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = smoke.main(["--adapter", "no/such/relative_adapter.py"])
        self.assertEqual(code, 2)
        message = stderr.getvalue()
        given = str(Path("no") / "such" / "relative_adapter.py")
        self.assertIn("given '%s'" % given, message)
        self.assertIn("resolved to '%s'" % (Path.cwd() / "no" / "such" / "relative_adapter.py"), message)

    def test_unknown_case_name_is_a_usage_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = smoke.main(["--case", "no_such_case"])
        self.assertEqual(code, 2)
        self.assertIn("unknown --case", stderr.getvalue())

    def test_compat_legacy_with_adapter_characterizes_the_user_adapter(self):
        # --adapter --compat-legacy is a documented mode, not a usage error: the
        # user-owned adapter is kept and only the reporting changes, so a state
        # the strict contract rejects is characterised instead of failed.
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "legacy_adapter.py"
            adapter.write_text(smoke.LEGACY_ADAPTER, encoding="utf-8")
            code, payload, _, _ = run_cli_uncached(
                "--adapter", str(adapter), "--compat-legacy", "--allow-skip",
                "--case", "missing_p1", "--case", "happy_path",
            )
            self.assertEqual(payload["adapter"], str(adapter))
            self.assertTrue(payload["compat_legacy"])
            missing = case_by_name(payload, "missing_p1")
            self.assertEqual(missing["result"], "characterized")
            self.assertEqual(missing["exit_code"], 0)
            self.assertIn("did not meet the strict contract", missing["detail"])
            self.assertIn("silently became 0.0", missing["detail"])
            self.assertEqual(case_by_name(payload, "happy_path")["result"], "pass")
            self.assertEqual(payload["summary"]["failed"], 0)
            self.assertEqual(code, 0)

    def test_non_positive_timeout_is_a_usage_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = smoke.main(["--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--timeout", stderr.getvalue())

    def test_skip_makes_the_exit_code_non_zero_unless_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "adapter.py"
            adapter.write_text(smoke.STRICT_ADAPTER, encoding="utf-8")
            code, payload, _, stderr = run_cli_uncached(
                "--adapter", str(adapter), "--case", "happy_path",
                "--case", "probe_truncated_stdout",
            )
            self.assertEqual(case_by_name(payload, "happy_path")["result"], "pass")
            skipped = case_by_name(payload, "probe_truncated_stdout")
            self.assertEqual(skipped["result"], "skip")
            self.assertTrue(skipped["detail"])
            self.assertEqual(payload["summary"]["skipped"], 1)
            self.assertEqual(payload["summary"]["failed"], 0)
            self.assertFalse(payload["ok"])
            self.assertEqual(code, 1)
            self.assertIn("--allow-skip", stderr)
            allowed_code, allowed_payload, _, _ = run_cli_uncached(
                "--adapter", str(adapter), "--case", "happy_path",
                "--case", "probe_truncated_stdout", "--allow-skip",
            )
            self.assertEqual(allowed_code, 0)
            self.assertTrue(allowed_payload["ok"])

    def test_user_adapter_with_the_legacy_fallback_fails_the_contract(self):
        # Negative control: the same scenarios must catch an adapter that hides
        # read failures behind 0.0 instead of refusing them.
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "broken_adapter.py"
            adapter.write_text(smoke.LEGACY_ADAPTER, encoding="utf-8")
            code, payload, _, stderr = run_cli_uncached(
                "--adapter", str(adapter), "--case", "happy_path",
                "--case", "missing_p1", "--case", "nonnumeric_slot",
            )
            self.assertEqual(case_by_name(payload, "happy_path")["result"], "pass")
            for name in ("missing_p1", "nonnumeric_slot"):
                entry = case_by_name(payload, name)
                self.assertEqual(entry["result"], "fail", name)
                self.assertIn("silently became 0.0", entry["detail"], name)
            self.assertGreaterEqual(payload["summary"]["failed"], 2)
            self.assertEqual(code, 1)
            self.assertIn("FAIL:", stderr)

    def test_timeout_is_reported_as_a_case_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "hanging_adapter.py"
            adapter.write_text("import time\n\ntime.sleep(30)\n", encoding="utf-8")
            code, payload, _, _ = run_cli_uncached(
                "--adapter", str(adapter), "--case", "happy_path", "--timeout", "0.5",
            )
            entry = case_by_name(payload, "happy_path")
            self.assertEqual(entry["result"], "fail")
            self.assertEqual(entry["exit_code"], -1)
            self.assertIn("TIMEOUT", entry["observed"])
            self.assertEqual(code, 1)

    def test_keep_tmp_reports_the_scratch_tree(self):
        import shutil

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = smoke.main(["--case", "happy_path", "--keep-tmp"])
        self.assertEqual(code, 0)
        kept = None
        for line in stdout.getvalue().splitlines():
            if line.startswith("kept temp workdir: "):
                kept = Path(line.split(": ", 1)[1].strip())
        self.assertIsNotNone(kept, stdout.getvalue())
        try:
            self.assertTrue(kept.is_dir())
            self.assertTrue((kept / "cases" / "happy_path").is_dir())
        finally:
            shutil.rmtree(str(kept), ignore_errors=True)


class RelativeAdapterPathTests(unittest.TestCase):
    """Regression tests for the relative ``--adapter`` defect.

    Every case runs the adapter with its own scratch directory as the subprocess
    working directory, so an unresolved relative ``--adapter`` used to be looked
    up inside that scratch directory: the fatal-expectation cases then passed
    vacuously (no adapter ran at all) and the value cases failed with
    ``can't open file``. These tests run the real CLI as a subprocess with the
    working directory set away from the repository root and ``--adapter`` given
    as a relative path, which is exactly how the defect was reported.
    """

    # Cases the harness itself documents as not applicable to a user adapter.
    APPLICABLE = tuple(case.name for case in smoke.CASE_SPECS if not case.skip_with_adapter)

    def run_runner(self, cwd: Path, *extra: str):
        """Run the CLI as a subprocess from ``cwd`` and return (proc, payload)."""
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--json", str(report), *extra],
                cwd=str(cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=600,
                check=False,
            )
            payload = None
            if report.exists():
                payload = json.loads(report.read_text(encoding="utf-8"))
            return proc, payload

    def assert_adapter_run_is_clean(self, proc, payload, relative, adapter_path):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIsNotNone(payload, proc.stderr)
        self.assertEqual(payload["summary"]["failed"], 0, proc.stdout)
        self.assertTrue(payload["ok"])
        # The path as given is recorded verbatim, and the resolved absolute path
        # is what every case actually executed.
        self.assertEqual(payload["adapter"], relative)
        resolved = Path(payload["adapter_resolved"])
        self.assertTrue(resolved.is_absolute(), resolved)
        self.assertEqual(resolved, Path(adapter_path).resolve())
        self.assertEqual(payload["argv"][1], str(resolved))
        for entry in payload["cases"]:
            if entry["name"] in self.APPLICABLE:
                self.assertEqual(entry["result"], "pass", entry["name"])
                self.assertNotEqual(entry["result"], "skip", entry["name"])
                self.assertEqual(entry["argv"][1], str(resolved), entry["name"])
                self.assertTrue(Path(entry["argv"][2]).is_absolute(), entry["name"])
            else:
                self.assertEqual(entry["result"], "skip", entry["name"])
                self.assertIsNone(entry["argv"], entry["name"])
        self.assertEqual(payload["summary"]["passed"], len(self.APPLICABLE))

    def test_relative_adapter_from_a_non_repo_cwd_runs_every_contract_case(self):
        # Self-contained layout: the caller's working directory and the adapter
        # only need to share a drive, so os.path.relpath is never asked to span
        # mount points (which raises on Windows).
        with tempfile.TemporaryDirectory(prefix="smoke-relative-") as tmp:
            root = Path(tmp)
            cwd = root / "work"
            adapters = root / "adapters"
            cwd.mkdir()
            adapters.mkdir()
            adapter = adapters / "reference_adapter.py"
            adapter.write_text(smoke.STRICT_ADAPTER, encoding="utf-8")
            relative = os.path.join(os.pardir, "adapters", "reference_adapter.py")
            self.assertFalse(os.path.isabs(relative), relative)
            self.assertNotEqual(cwd, REPO_ROOT)
            proc, payload = self.run_runner(cwd, "--adapter", relative, "--allow-skip")
            self.assert_adapter_run_is_clean(proc, payload, relative, adapter)

    def test_repo_relative_shipped_template_path(self):
        # The user-facing reproducer: a repository-relative --adapter path while
        # the working directory is a subdirectory of the repository. Skipped only
        # when that file does not exist yet; this platform is exactly where the
        # defect appeared, so the test must never be skipped merely for Windows.
        template = REPO_ROOT / "assets" / "legacy105" / "calcElasticML_adapter_template.py"
        if not template.is_file():
            self.skipTest("no shipped adapter template at %s yet" % template)
        cwd = REPO_ROOT / "scripts"
        relative = os.path.relpath(str(template), str(cwd))
        self.assertFalse(os.path.isabs(relative), relative)
        proc, payload = self.run_runner(cwd, "--adapter", relative, "--allow-skip")
        self.assert_adapter_run_is_clean(proc, payload, relative, template)

    def test_relative_adapter_is_not_found_in_the_scratch_directory(self):
        # The unresolved-path failure mode must be impossible: even when the
        # adapter only exists relative to the caller's directory, no case may
        # report the adapter as unreachable and no fatal case may pass by
        # accident with an empty run.
        with tempfile.TemporaryDirectory(prefix="smoke-relative-") as tmp:
            root = Path(tmp)
            cwd = root / "work"
            adapters = root / "adapters"
            cwd.mkdir()
            adapters.mkdir()
            adapter = adapters / "reference_adapter.py"
            adapter.write_text(smoke.STRICT_ADAPTER, encoding="utf-8")
            relative = os.path.join(os.pardir, "adapters", "reference_adapter.py")
            proc, payload = self.run_runner(
                cwd, "--adapter", relative, "--allow-skip", "--case", "happy_path",
                "--case", "missing_p1",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(case_by_name(payload, "happy_path")["result"], "pass")
            self.assertEqual(case_by_name(payload, "missing_p1")["result"], "pass")
            for name in ("happy_path", "missing_p1"):
                entry = case_by_name(payload, name)
                self.assertEqual(entry["argv"][1], str(adapter.resolve()), name)
                self.assertNotIn("can't open file", entry["stderr"], name)
                self.assertNotIn("No such file", entry["stderr"], name)

    def test_missing_relative_adapter_exits_2_as_a_subprocess(self):
        cwd = REPO_ROOT / "scripts"
        relative = str(Path("no") / "such" / "adapter.py")
        proc, payload = self.run_runner(cwd, "--adapter", relative)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIsNone(payload)
        self.assertIn("given '%s'" % relative, proc.stderr)
        self.assertIn("resolved to '%s'" % (cwd / "no" / "such" / "adapter.py"), proc.stderr)


class AdapterProcessContractTests(unittest.TestCase):
    def test_judge_rejects_malformed_output(self):
        cases = [
            (0, "", "no <CALLRESULT> line"),
            (0, "<CALLRESULT> 1 2 3\n", "expected exactly 7 values"),
            (0, "<CALLRESULT> 1 2 3 4 5 6 7 8\n", "found 8"),
            (0, "log\n<CALLRESULT> 1 2 3 4 5 6 7\n<CALLRESULT> 1 2 3 4 5 6 7\n", "exactly one"),
            (3, "<CALLRESULT> 1 2 3 4 5 6 7\n", "exited 3"),
            (0, "<CALLRESULT> 1 2 3 4 5 6 1e999\n", "nonfinite"),
            (0, "<CALLRESULT> 1 2 3 4 5 6 nan\n", "syntax"),
            (0, "prefix <CALLRESULT> 1 2 3 4 5 6 7\n", "must start the line"),
        ]
        for exit_code, stdout, fragment in cases:
            judgement = smoke.judge_adapter_output(exit_code, stdout)
            self.assertFalse(judgement["ok"], stdout)
            self.assertIn(fragment, judgement["problem"], stdout)
        good = smoke.judge_adapter_output(0, "<CALLRESULT> 11 22 33 44 55 66 77\n")
        self.assertTrue(good["ok"])
        self.assertEqual(good["values"], [11.0, 22.0, 33.0, 44.0, 55.0, 66.0, 77.0])

    def test_danger_notes_only_fire_for_broken_slots(self):
        self.assertEqual(smoke.danger_notes({}, [0.0] * smoke.SLOT_COUNT), [])
        notes = smoke.danger_notes({2: "absent"}, [11.0, 0.0] + [33.0] * 5)
        self.assertEqual(len(notes), 1)
        self.assertIn("P2.txt (missing)", notes[0])
        self.assertIn("silently became 0.0", notes[0])
        nonfinite = smoke.danger_notes({3: "nonfinite"}, [11.0, 22.0, float("inf")] + [44.0] * 4)
        self.assertEqual(len(nonfinite), 1)
        self.assertIn("non-finite", nonfinite[0])

    def test_resolve_python_accepts_a_bare_interpreter_name(self):
        try:
            resolved = smoke.resolve_python("python")
        except smoke.UsageError:
            self.skipTest("no 'python' executable on PATH")
        self.assertTrue(Path(resolved).is_file())

    def test_resolve_python_rejects_a_missing_interpreter(self):
        with self.assertRaises(smoke.UsageError):
            smoke.resolve_python("definitely-not-an-interpreter-9f3a")

    def test_relative_python_path_resolves_against_the_caller_directory(self):
        # A relative --python must be anchored to the directory the command was
        # started in, exactly like --adapter, because the adapter subprocess runs
        # with a scratch working directory.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake_interpreter"
            fake.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            resolved = smoke.resolve_python("fake_interpreter", root)
            self.assertTrue(resolved.is_absolute(), resolved)
            self.assertEqual(resolved, fake.resolve())
            # A path that exists nowhere is rejected with both paths named.
            with self.assertRaisesRegex(smoke.UsageError, "resolved to"):
                smoke.resolve_python(str(Path("missing") / "interpreter"), root)

    def test_relative_adapter_and_python_are_resolved_by_main(self):
        # End to end at the CLI boundary: the resolved adapter and interpreter
        # recorded in the run are the ones main() resolved against the caller's
        # directory, not the scratch directory of any case.
        args = smoke.build_parser().parse_args(["--case", "happy_path"])
        args.base_dir = REPO_ROOT
        self.assertEqual(smoke.caller_base_dir(args), REPO_ROOT)
        self.assertEqual(
            smoke.resolve_input_path(args.base_dir, Path("scripts") / "smoke_legacy105_custom.py"),
            RUNNER,
        )
        payload = smoke.run_smoke(args)
        self.assertTrue(Path(payload["adapter_resolved"]).is_absolute())
        self.assertEqual(payload["cases"][0]["argv"][1], payload["adapter_resolved"])

    def test_module_has_no_third_party_dependency(self):
        # The adapters must stay stdlib-only so they can run inside a conda
        # python=3.9 USPEX environment with no numpy/scipy/pymatgen installed.
        source = Path(smoke.__file__).read_text(encoding="utf-8")
        for forbidden in ("import numpy", "import scipy", "import pymatgen", "import pytest"):
            self.assertNotIn(forbidden, source)

    def test_sources_parse_with_the_python_39_grammar(self):
        # The target USPEX 10.5 environment is conda python=3.9, so neither file
        # may use 3.10+ syntax (match statement, parenthesised context managers,
        # X | Y unions outside a postponed-annotation context, and so on).
        for path in (Path(smoke.__file__), Path(__file__)):
            source = path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(path), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
