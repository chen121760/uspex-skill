#!/usr/bin/env python3
"""Unit tests for extract_uspex_candidates.py; no USPEX installation required.

Fixtures mirror the real artefacts: bracket-padded Composition/KPOINTS columns,
column-0 ``EA<id>`` block headers carrying ``Sym.group``, a Pareto_ranking table
with a different enthalpy unit than Individuals, an extended_convex_hull file
with four comment lines, and CalcFoldTemp/Jobs.history.
"""

from __future__ import annotations

import contextlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from extract_uspex_candidates import (
    EXIT_EMPTY,
    EXIT_OK,
    EXIT_USAGE,
    ParseFailure,
    describe_fields,
    evaluate,
    load_run,
    main,
    parse_poscar_blocks,
    parse_predicate,
    parse_table,
    render_predicate,
    select,
    tokenize_row,
)

GATHERED = """\
EA1     5.076  2.538  2.537 90.00 90.00 90.00 Sym.group:  221
1.0
    5.075848     0.000000    -0.000000
    0.000000     2.537655    -0.000000
   -0.000000    -0.000000     2.537405
  Li F
   1 1
Direct
    1.000000     0.500000     0.500000
    0.500000     0.500000     0.500000
EA2     6.000  6.000  6.000 90.00 90.00 90.00 Sym.group:  191
1.0
    6.000000     0.000000     0.000000
    0.000000     6.000000     0.000000
    0.000000     0.000000     6.000000
  Li Mg F
   1 1 1
Direct
    0.000000     0.000000     0.000000
    0.500000     0.500000     0.500000
    0.250000     0.250000     0.250000
EA3     4.000  4.000  4.000 90.00 90.00 90.00 Sym.group:    1
1.0
    4.000000     0.000000     0.000000
    0.000000     4.000000     0.000000
    0.000000     0.000000     4.000000
  Mg F
   2 2
Direct
    0.000000     0.000000     0.000000
    0.500000     0.500000     0.500000
    0.250000     0.250000     0.250000
    0.750000     0.750000     0.750000
"""

GATHERED_UNRELAXED = """\
EA1     5.500  2.500  2.500 90.00 90.00 90.00 Sym.group:    0
1.0
    5.500000     0.000000     0.000000
    0.000000     2.500000     0.000000
    0.000000     0.000000     2.500000
  Li F
   1 1
Direct
    1.000000     0.500000     0.500000
    0.500000     0.500000     0.500000
"""

PARETO = """\
Pareto  ID   Origin     Composition     Enthalpy   Volume  Density    Property_X   ConvexHull  KPOINTS   SYMM  Q_entr A_order S_order
front                                   eV/atom    (A^3)  (g/cm^3)
  1      1    Random    [      2    ]    -5.087    32.684   2.854      221.000        0.073    [ 1  1  1] 221   -0.000  4.149  4.153
  1      3    Random    [      4    ]    -5.123    30.178   3.091      191.000        0.000    [ 1  1  1] 191   -0.000  3.440  3.445
  2      2    Heredity  [      3    ]    -4.953    34.100   2.700      139.000        0.120    [ 1  1  1] 139   -0.000  3.100  3.200
"""

INDIVIDUALS = """\
Gen   ID    Origin   Composition    Enthalpy   Volume  Density  Property_X    KPOINTS  SYMM  Q_entr A_order S_order
                                      (eV)     (A^3)  (g/cm^3)
  1    1   Random    [      2    ]   -10.174    32.684   2.854   -221.000  [ 1  1  1] 221 -0.000  4.149  4.153
  2    1   Random    [      2    ]    -9.500    31.000   2.900   -200.000  [ 1  1  1] 200 -0.000  4.000  4.000
  1    3   Random    [      4    ]   -20.492    30.178   3.091   -191.000  [ 1  1  1] 191 -0.000  3.440  3.445
"""

HULL = """\
# It contains the all the information in extendedConvexHull.pdf
# X axis: Composition 
# Y axis: Formation energy relative to the substance
# Fitness: its distance to the convex hull (eV/block)
  ID   Compositions    Enthalpies     Volumes     Fitness   SYMM    X        Y     
                       (eV/atom)    (A^3/atom)   (eV/block)              (eV/atom) 
   1  [     2    ]       -5.0870       13.3420      0.0730    221   1.000  -0.2000
   3  [     4    ]       -5.1230       7.5445      0.0000    191   1.000  -0.1800
"""

JOBS_HISTORY = """\
Generation 1 Step 1 of Structure 1 at Calcfold1 : JOBID 1452242  Submitted Sep28-17:44:16
Generation 1 Step 1 of Structure 3 at Calcfold3 : JOBID 1452244  Submitted Sep28-17:44:17
"""


def build_run(root: Path) -> Path:
    results = root / "results1"
    results.mkdir(parents=True)
    (results / "gatheredPOSCARS").write_text(GATHERED, encoding="utf-8")
    (results / "gatheredPOSCARS_unrelaxed").write_text(GATHERED_UNRELAXED, encoding="utf-8")
    (results / "Pareto_ranking").write_text(PARETO, encoding="utf-8")
    (results / "Individuals").write_text(INDIVIDUALS, encoding="utf-8")
    (results / "extended_convex_hull").write_text(HULL, encoding="utf-8")
    temp = root / "CalcFoldTemp"
    temp.mkdir()
    (temp / "Jobs.history").write_text(JOBS_HISTORY, encoding="utf-8")
    (root / "CalcFold1").mkdir()
    (root / "CalcFold1" / "energy.txt").write_text("-10.174\n", encoding="utf-8")
    (root / "CalcFold3").mkdir()
    (root / "CalcFold3" / "energy.txt").write_text("999999\n", encoding="utf-8")
    return results


def run_main(args: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = main(args)
    return code, buffer.getvalue()


class TokenizerTests(unittest.TestCase):
    def test_bracket_columns_stay_one_token(self) -> None:
        row = "  1    1   Random    [      2    ]   -10.174    32.684   2.854   -221.000  [ 1  1  1] 221"
        tokens = tokenize_row(row)
        self.assertEqual(tokens[:5], ["1", "1", "Random", "[      2    ]", "-10.174"])
        self.assertIn("[ 1  1  1]", tokens)

    def test_table_columns_align_with_bracket_awareness(self) -> None:
        table = parse_table(PARETO, "pareto", Path("Pareto_ranking"), 2)
        self.assertEqual(table.columns[0], "Pareto")
        self.assertEqual(table.columns[7], "Property_X")
        self.assertEqual(len(table.rows), 3)
        row = table.by_id()[1]
        self.assertEqual(row["Origin"], "Random")
        self.assertEqual(row["Property_X"], "221.000")
        self.assertEqual(row["ConvexHull"], "0.073")

    def test_hull_table_skips_four_comment_lines(self) -> None:
        table = parse_table(HULL, "hull", Path("extended_convex_hull"), 2)
        self.assertEqual(table.columns[:3], ["ID", "Compositions", "Enthalpies"])
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.by_id()[1]["Fitness"], "0.0730")


class BlockParsingTests(unittest.TestCase):
    def test_blocks_round_trip(self) -> None:
        blocks, problems = parse_poscar_blocks(GATHERED, "relaxed")
        self.assertEqual(problems, [])
        self.assertEqual([block.id for block in blocks], [1, 2, 3])
        self.assertEqual(blocks[0].formula, "LiF")
        self.assertEqual(blocks[0].natoms, 2)
        self.assertEqual(blocks[1].formula, "LiMgF")
        self.assertEqual(blocks[2].natoms, 4)
        self.assertEqual(blocks[0].spacegroup, 221)
        self.assertAlmostEqual(blocks[0].volume, 5.075848 * 2.537655 * 2.537405, places=4)
        self.assertEqual(blocks[0].boundary, "header")
        self.assertEqual(blocks[2].boundary, "eof")

    def test_declared_atoms_beyond_block_is_rejected(self) -> None:
        broken = GATHERED.replace("   2 2\nDirect\n    0.000000     0.000000     0.000000\n"
                                  "    0.500000     0.500000     0.500000\n"
                                  "    0.250000     0.250000     0.250000\n"
                                  "    0.750000     0.750000     0.750000\n", "   5 5\nDirect\n")
        with self.assertRaises(ParseFailure) as ctx:
            parse_poscar_blocks(broken, "relaxed", strict=True)
        self.assertIn("coordinate lines", str(ctx.exception))

    def test_arithmetic_boundary_must_land_on_next_header(self) -> None:
        broken = GATHERED.replace("  Li Mg F\n   1 1 1\nDirect\n", "  Li Mg F\n   1 1 1\nDirect\ntrailing junk\n")
        with self.assertRaises(ParseFailure) as ctx:
            parse_poscar_blocks(broken, "relaxed", strict=True)
        self.assertIn("does not land on the next header", str(ctx.exception))

    def test_duplicate_ids_in_one_source_are_rejected(self) -> None:
        duplicated = GATHERED + GATHERED[: GATHERED.index("EA2")]
        with self.assertRaises(ParseFailure) as ctx:
            parse_poscar_blocks(duplicated, "relaxed", strict=True)
        self.assertIn("duplicate EA id", str(ctx.exception))

    def test_indented_header_is_not_a_block(self) -> None:
        blocks, _ = parse_poscar_blocks("  EA1  1 2 3 4 5 6 Sym.group: 1\n", "relaxed", strict=False)
        self.assertEqual(blocks, [])


class PredicateTests(unittest.TestCase):
    def test_parser_and_renderer_round_trip(self) -> None:
        node = parse_predicate("front=1 and natoms=4..20")
        self.assertEqual(render_predicate(node), "(front = 1 and natoms = 4..20)")

    def test_unknown_field_is_reported_with_the_vocabulary(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_predicate("nonsense=1")
        self.assertIn("unknown field", str(ctx.exception))
        self.assertIn("spacegroup", str(ctx.exception))

    def test_operators_are_split_from_names_and_values(self) -> None:
        node = parse_predicate("spacegroup>=190")
        self.assertEqual(node.field, "spacegroup")
        self.assertEqual(node.values, ["190"])

    def test_unbalanced_parentheses_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_predicate("(front=1")

    def test_boolean_and_range_and_in(self) -> None:
        values = {"nelements": 2, "spacegroup": 191, "element": ["Li", "Mg", "F"]}
        self.assertTrue(evaluate(parse_predicate("nelements=2"), values))
        self.assertTrue(evaluate(parse_predicate("nelements=1..2"), values))
        self.assertFalse(evaluate(parse_predicate("nelements=3..4"), values))
        self.assertTrue(evaluate(parse_predicate("element=Mg"), values))
        self.assertTrue(evaluate(parse_predicate("element in Cl,Mg"), values))
        self.assertFalse(evaluate(parse_predicate("element=Cl"), values))
        self.assertTrue(evaluate(parse_predicate("not (spacegroup<190)"), values))
        self.assertTrue(evaluate(parse_predicate("nelements=2 and spacegroup=191"), values))
        self.assertTrue(evaluate(parse_predicate("nelements=5 or spacegroup=191"), values))

    def test_missing_field_evaluates_to_none_not_false(self) -> None:
        self.assertIsNone(evaluate(parse_predicate("front=1"), {}))
        self.assertIsNone(evaluate(parse_predicate("front=1 and natoms=2"), {"natoms": 2}))
        self.assertFalse(evaluate(parse_predicate("front=1 or natoms=9"), {"natoms": 2}))

    def test_numeric_field_with_text_value_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(parse_predicate("natoms=abc"), {"natoms": 2})


class SelectionTests(unittest.TestCase):
    def test_missing_evidence_excludes_and_counts_instead_of_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            # The hull file only lists ids 1 and 3, so id 2 has no hull distance.
            selection = select(run, [parse_predicate("hull_block<=0.2")], None, None, False)
            self.assertEqual(selection.counts["matched"], 2)
            self.assertEqual(selection.counts["missing_field"], 1)
            self.assertTrue(any("missing evidence" in note for note in selection.notes))

    def test_condition_with_full_coverage_reports_no_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            selection = select(run, [parse_predicate("front=1")], None, None, False)
            self.assertEqual(selection.counts["matched"], 2)
            self.assertEqual(selection.counts["missing_field"], 0)
            self.assertEqual(selection.notes, [])

    def test_geometry_only_condition_matches_every_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            selection = select(run, [parse_predicate("nelements>=1")], None, None, False)
            self.assertEqual(selection.counts["matched"], 3)

    def test_both_sources_keep_their_own_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results, sources=("relaxed", "unrelaxed"))
            selection = select(run, [parse_predicate("source=unrelaxed")], None, None, False)
            self.assertEqual(selection.counts["matched"], 1)
            self.assertEqual(selection.matched[0][0].id, 1)

    def test_sort_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            selection = select(run, [parse_predicate("natoms>=1")], 2, "natoms", True)
            self.assertEqual([block.id for block, _ in selection.matched], [3, 2])

    def test_calcfold_evidence_separates_sentinel_from_converged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results, needs_calcfold=True)
            converged = select(run, [parse_predicate("converged=true")], None, None, False)
            self.assertEqual([block.id for block, _ in converged.matched], [1])
            missing = select(run, [parse_predicate("energy_cell<=-10")], None, None, False)
            self.assertEqual([block.id for block, _ in missing.matched], [1])

    def test_fields_include_pareto_individuals_and_hull_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            # Individuals repeats ids across generations, so the generation must be pinned.
            values = run.fields_for(run.blocks[0], {1})
            self.assertEqual(values["front"], 1)
            self.assertAlmostEqual(values["enthalpy_atom"], -5.087)
            self.assertAlmostEqual(values["hull"], 0.073)
            self.assertAlmostEqual(values["hull_block"], 0.073)
            self.assertAlmostEqual(values["enthalpy_cell"], -10.174)
            self.assertEqual(values["generation"], 1)
            self.assertAlmostEqual(values["objective"], 221.0)
            self.assertAlmostEqual(values["objective_ind"], -221.0)

    def test_ambiguous_generation_leaves_individuals_fields_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            values = run.fields_for(run.blocks[0])
            self.assertNotIn("enthalpy_cell", values)
            self.assertNotIn("generation", values)


class CliTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, output = run_main([str(results), "--select", "spacegroup>=190", "--dry-run"])
            self.assertEqual(code, EXIT_OK)
            payload = json.loads(output)
            self.assertEqual(payload["counts"]["matched"], 2)
            self.assertFalse(list(results.glob("EA*")))

    def test_output_manifest_csv_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = build_run(root)
            out = root / "picked"
            archive = root / "picked.tar.gz"
            code, output = run_main(
                [
                    str(results),
                    "--select",
                    "front=1",
                    "--out",
                    str(out),
                    "--archive",
                    str(archive),
                ]
            )
            self.assertEqual(code, EXIT_OK)
            payload = json.loads(output)
            self.assertEqual(payload["structures"], 2)
            files = sorted(path.name for path in out.glob("*.vasp"))
            self.assertEqual(len(files), 2)
            self.assertTrue(any("SG221" in name for name in files))
            manifest = json.loads((out / "selection_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["conditions"], ["front = 1"])
            self.assertEqual(manifest["fields_used"]["front"]["source"], "pareto")
            self.assertIn("Pareto_ranking", manifest["source_files"])
            self.assertTrue(manifest["source_files"]["Pareto_ranking"])
            evidence = manifest["structures"][0]["evidence"]
            self.assertIn("front", evidence)
            self.assertTrue((out / "candidates.csv").is_file())
            with tarfile.open(archive) as tar:
                names = tar.getnames()
            self.assertTrue(any(name.endswith(".vasp") for name in names))
            self.assertIn("selection_manifest.json", names)

    def test_written_poscar_is_loadable_by_our_own_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = build_run(root)
            out = root / "picked"
            code, _ = run_main([str(results), "--select", "id=1", "--out", str(out)])
            self.assertEqual(code, EXIT_OK)
            written = next(out.glob("*.vasp")).read_text(encoding="utf-8")
            blocks, problems = parse_poscar_blocks(written, "relaxed")
            self.assertEqual(problems, [])
            self.assertEqual(blocks[0].formula, "LiF")
            self.assertEqual(blocks[0].spacegroup, 221)

    def test_zero_matches_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, output = run_main([str(results), "--select", "spacegroup=999", "--dry-run"])
            self.assertEqual(code, EXIT_EMPTY)
            self.assertEqual(json.loads(output)["counts"]["matched"], 0)

    def test_empty_selection_is_a_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, _ = run_main([str(results)])
            self.assertEqual(code, EXIT_USAGE)

    def test_individuals_condition_without_generation_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, output = run_main([str(results), "--select", "enthalpy_cell<=-9", "--dry-run"])
            self.assertEqual(code, EXIT_USAGE)
            self.assertIn("repeat across generations", output)

    def test_individuals_condition_with_generation_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, output = run_main(
                [str(results), "--select", "generation=1", "--select", "enthalpy_cell<=-9", "--dry-run"]
            )
            self.assertEqual(code, EXIT_OK)
            self.assertEqual(json.loads(output)["counts"]["matched"], 2)

    def test_list_fields_reports_availability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            code, output = run_main([str(results), "--list-fields"])
            self.assertEqual(code, EXIT_OK)
            fields = json.loads(output)["fields"]
            self.assertTrue(fields["spacegroup"]["available_in_this_run"])
            self.assertTrue(fields["front"]["available_in_this_run"])
            self.assertEqual(fields["front"]["source"], "pareto")

    def test_skip_invalid_reports_problems_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            broken = GATHERED.replace("  Li Mg F\n   1 1 1\nDirect\n", "  Li Mg F\n   1 1 1\nDirect\ntrailing junk\n")
            (results / "gatheredPOSCARS").write_text(broken, encoding="utf-8")
            code, _ = run_main([str(results), "--select", "nelements>=1", "--dry-run"])
            self.assertEqual(code, EXIT_USAGE)
            code, output = run_main(
                [str(results), "--select", "nelements>=1", "--dry-run", "--skip-invalid"]
            )
            self.assertEqual(code, EXIT_OK)
            payload = json.loads(output)
            self.assertTrue(any("does not land" in problem for problem in payload["notes"]))

    def test_name_template_field_error_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = build_run(root)
            code, output = run_main(
                [str(results), "--select", "id=1", "--out", str(root / "picked"), "--name-template", "{nope}"]
            )
            self.assertEqual(code, EXIT_USAGE)
            self.assertIn("unknown name template field", output)

    def test_run_root_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_run(root)
            code, output = run_main([str(root), "--select", "id=1", "--dry-run"])
            self.assertEqual(code, EXIT_OK)
            self.assertIn("results1", json.loads(output)["results_dir"])


class VocabularyTests(unittest.TestCase):
    def test_every_field_has_a_declared_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            run = load_run(results)
            described = describe_fields(run)
            sources = {entry["source"] for entry in described.values()}
            self.assertTrue(
                sources <= {"geometry", "pareto", "individuals", "hull", "calcfold"},
                msg=f"unexpected sources: {sources}",
            )


class StabilityTests(unittest.TestCase):
    def test_source_growing_during_the_read_is_reported(self) -> None:
        """gatheredPOSCARS is appended to while a run is alive."""
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            path = results / "gatheredPOSCARS"
            original = Path.read_text

            def read_then_grow(self, *args, **kwargs):
                text = original(self, *args, **kwargs)
                if self == path:
                    path.write_text(text + text[text.index("EA3"):].replace("EA3", "EA4"), encoding="utf-8")
                return text

            with mock.patch.object(Path, "read_text", read_then_grow):
                run = load_run(results)
            self.assertEqual(len(run.blocks), 3)
            self.assertTrue(any("changed while it was being read" in item for item in run.warnings))

    def test_quiet_run_reports_no_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = build_run(Path(tmp))
            self.assertEqual(load_run(results).warnings, [])


if __name__ == "__main__":
    unittest.main()
