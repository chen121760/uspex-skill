# Validation and analysis

> Load this when: checking a run before submission, deciding whether results are usable, or analyzing hulls, Pareto fronts, 2D/surface searches, and shortlisted structures.

## Objective direction and not-joined tables

For a larger-is-better custom property, the callback returns the **raw measured value**; the
driver keeps a negated copy in `Individuals` purely so that every column shares one
"smaller is better" orientation. Judge the convention from `optType`, an independent
calculation of the callback value, and `results1/ParetoRankingInGeneration`. Do not “repair”
an apparently inverted objective by negating the callback value — that selects the worst
candidates.

Per-generation output files are written by different parts of the driver and their
per-individual IDs do **not** necessarily line up: one probe showed the value raw in
`MLProperties`/`ParetoRankingInGeneration` and sign-flipped in `Individuals`, with rows
attributable to different structures. Therefore:

1. take the direction and ranking from `ParetoRankingInGeneration` plus the `optType` block
   in `Parameters.txt`;
2. independently recompute custom properties for shortlisted structures from their relaxed
   geometries; use per-structure manifests/logs when the project preserves them;
3. treat `Individuals` only as a per-generation listing, never as a join key.

## Preflight gate

Run:

```bash
python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.6
```

It checks the installation, interpreter, dependencies and scheduler contract, then delegates to
the static preflight below. Then manually verify:

- release matches INPUT syntax and copied example;
- `numSpecies` rows match `atomType` width and intended chemical formulas;
- objective directions, units, and staged activation are written in the project README;
- every engine stage maps to the intended command and input file;
- VASP species order matches `POTCAR_<element>` and all comparable runs use one method;
- scheduler resources × `numParallelCalcs` fit queue/account limits;
- surface substrate or 2D vacuum geometry is visualized before launch;
- one-structure end-to-end smoke test passed.
- for code-99 geometry-derived objectives, callback provenance identifies the current
  `geom.in` and the relaxed structure used for `x`/`y`; no numbered property file is assumed.

## Runtime evidence

Start with the status tool rather than a hand-built shell probe:

```bash
python scripts/uspex_status.py RUN --stale-minutes 30     # 0 done, 1 running, 2 intervene, 3 unknown
```

It reads the markers, `results*/OUTPUT.txt`, the driver log, `CalcFoldTemp/Jobs.history`, the
per-structure outputs and the scheduler, then decides between done/running/stalled/failed/unknown
and attributes jobs to *this* run by workdir (job names are site-defined and unreliable). Two
behaviours matter when reading its output: a results copy without `USPEX_IS_DONE` is still
reported as done when `OUTPUT.txt` records the finish, and a value it cannot establish is reported
as missing evidence instead of a zero.

Its `--stale-minutes` is a fixed constant, and a search whose generation cycle takes hours is
legitimately silent for far longer than that: after the `Calculating fitness` line a healthy run can
spend ~20 min evolving the next generation without touching disk, and the observed cycle across
sibling runs of one project ranged from 1.5 h to 3.7 h. So before acting on `stalled`, run

```bash
python scripts/check_run_liveness.py RUN --sample-seconds 90    # 0 done, 1 working, 2 stuck, 3 stopped, 4 unknown
```

which measures this run's own cadence from `results*/generation*` and samples the engine's I/O, and
read `references/liveness.md` for the false-positive traps (pinned core, idle `matlab_helper`,
leftover `still_running`, buffered driver log) and for the recovery branches to offer the user.

Inspect throughout the run:

- `OUTPUT.txt`: generation progress, operator yields, best structures (it does **not** record
  per-structure warnings or failures);
- `Parameters.txt`: a copy of the `INPUT.txt` that produced this `results<N>` folder — useful for
  attributing a folder to a run, not for revealing parser defaults;
- `Individuals` and `BESTIndividuals`: objective values and rankings;
- `gatheredPOSCARS` / `BESTgatheredPOSCARS`: relaxed structures;
- `Warnings` or `results*/Warnings` (run level), plus `CalcFoldTemp/Jobs.history` and
  `ERROR-OUTCAR-Gen*-Ind*-Step*` (the per-structure stage/failure evidence) — failures are
  recorded only here, never in `OUTPUT.txt` or `Individuals`;
- scheduler stdout/stderr and per-structure engine logs;
- `origin`: parent/offspring provenance;
- `Pareto_ranking` and `MLProperties` when those routes are active;
- convex-hull or surface-phase-diagram files for variable-composition searches.

Read the interpreted values from `OUTPUT.txt` (system, dimensionality, variable composition) and
the per-structure failure evidence from `Warnings`, not from `Parameters.txt`: that file is a copy
of the authored `INPUT.txt` and exposes no parser defaults.

## Fixed-composition 3D

Track best enthalpy versus generation, structural duplicates, space-group changes,
and operator diversity. A flat best curve is meaningful only when failed jobs are
not masquerading as high-energy candidates. Re-relax multiple low-energy,
structurally distinct candidates at stricter, identical VASP settings.

## Variable-composition 3D

- Build the convex hull with reference energies computed consistently.
- Examine near-hull metastable phases, not only vertices.
- For ternary/quaternary searches, inspect relevant binary/ternary faces and
  projections; a 2D projection can hide an unstable point.
- Repeat promising composition ranges with tighter bounds or fixed-composition
  refinement when the global search undersamples them.
- Report hull distance, pressure, functional, potentials, and magnetic policy.

USPEX Analyzer is particularly useful for interactive binary/ternary/quaternary
hulls and merging fixed-composition refinements into a common hull.

## 2D crystals

Visualize every finalist with periodic images. Verify layer thickness, vacuum,
out-of-plane drift, broken fragments, and whether cell relaxation preserved the
intended 2D setting. Recompute with converged in-plane k sampling and sufficient
vacuum; use dipole corrections where the asymmetric physics requires them.

## Surface reconstruction

Validate the substrate orientation, fixed/buffer/surface regions, reconstruction
matrix, atom exchange convention, and chemical potentials. Analyze the surface
phase diagram rather than ranking raw slab energies across different compositions.
Rebuild shortlisted slabs independently and reconverge slab/vacuum/thickness and
k sampling.

## Multi-objective searches

For each objective, verify direction and units with a two-structure hand test.
Then analyze:

- Pareto front and dominance rank;
- property distributions by generation;
- whether the front still moves with generation;
- hypervolume or another direction-aware convergence measure;
- structural/compositional diversity along the front;
- correlation introduced by shared model errors;
- the effect of the `[N]` enthalpy-only warm-up.

Never collapse the Pareto front to one “best” structure without declaring an
external decision rule. Recalculate representatives across the front with the same
high-fidelity method.

## VASP acceptance gate

For every structure used in a figure, hull, or claim:

- electronic and ionic convergence reached the stated tolerances;
- no fatal warnings or pathological cell/atom overlap;
- final energy is from the intended stage and normalized correctly;
- functional, POTCAR identities, ENCUT, k density, smearing, spin/U, and pressure match;
- symmetry was not imposed in a way that suppresses the target distortion;
- final geometry is preserved with OUTCAR/CONTCAR/vasprun metadata;
- dynamical/mechanical stability is computed when the scientific claim requires it.

ML-potential searches generate candidates; they do not replace DFT validation of
the final energetic or property claims unless the claim explicitly concerns the ML model.

## Packaging the structures a user asked for

"Give me the structures that ..." is a selection, not an export of everything:

```bash
python scripts/extract_uspex_candidates.py RESULTS_DIR --list-fields
python scripts/extract_uspex_candidates.py RESULTS_DIR \
  --select 'nelements<=2' --select 'natoms=4..20' --dry-run
python scripts/extract_uspex_candidates.py RESULTS_DIR \
  --select 'front=1' --select 'hull<=0.05' --sort enthalpy_atom --limit 30 \
  --out picked/ --archive picked.tar.gz
```

Conditions combine with `and`, `or`, `not` and parentheses, and may use `=`, `!=`, `<`, `<=`,
`>`, `>=`, `~` (regex), `in`, and ranges `a..b`. Every field is bound to one source, and the
manifest records that binding, so a selection can be explained later:

| Field group | Source | Examples |
|---|---|---|
| geometry | `gatheredPOSCARS[_unrelaxed]` block and its `Sym.group` header | `id`, `formula`, `natoms`, `nelements`, `element`, `spacegroup`, `volume`, `a`..`gamma`, `source` |
| table | `Pareto_ranking` | `front`, `enthalpy_atom` (eV/atom), `hull`, `objective`, `origin` |
| table | `Individuals` (needs `generation=`) | `generation`, `enthalpy_cell` (eV/cell), `density`, `objective_ind` |
| table | `extended_convex_hull` | `hull_block`, `enthalpy_atom_hull`, `y_axis` |
| per structure | `CalcFold*` via `CalcFoldTemp/Jobs.history` | `converged`, `energy_cell` (current generation only) |

Three rules keep this honest, and each one comes from an observed failure: the two enthalpy
columns are different units, so they are separate field names; a structure whose referenced field
has no value is excluded and counted as `missing_field` rather than treated as zero; and a
condition on `Individuals` must name a generation because its ids repeat across generations.
Note that `hull` (from `Pareto_ranking`) covers only the structures that reached the released
front, which is why large `missing_field` counts are normal on a long variable-composition run.

## USPEX Analyzer handoff

Project: <https://github.com/chen121760/USPEX-Analyzer>

For 10.5/10.6-style results, collect at least the run's `Individuals`, `origin`,
`Parameters.txt`, and `gatheredPOSCARS`. Add convex-hull, `Pareto_ranking`, and
`MLProperties` files when generated. Use the Analyzer to explore and export; retain
the original USPEX outputs as provenance.

```bash
python scripts/uspex_status.py RUN --emit-bundle results.tar.gz --bundle-require-core
```

The bundle carries those files plus `USPEX_RESULTS_MANIFEST.json` with per-file sizes and
SHA-256 values, and it deliberately excludes `results*/generation*`, `CalcFold*`, `*.mat` and
logs. `--bundle-require-core` refuses to produce an archive when `Individuals`,
`Parameters.txt` or `gatheredPOSCARS` is absent or empty rather than shipping a silent partial
handoff.

## Reproducibility record

Archive a small manifest containing:

```text
USPEX version/checksum and copied example number
INPUT.txt and the `results<N>/Parameters.txt` copy it produced
Specific file checksums; POTCAR TITEL metadata only
Submission scripts and scheduler/resource settings
external engine/model versions and checkpoints
seed provenance and composition basis
start/end dates, pressure, objective definitions/directions
post-relaxation method and acceptance criteria
```
