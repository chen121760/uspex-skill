# Custom properties and external optimizers

> Load this when: defining multiple objectives, using USPEX code 99, connecting an ML potential/predictor, or reproducing the group USPEX 10.5 custom method.

## Choose the shortest supported route

| Installed release | Preferred route |
|---|---|
| 10.6 | Native arbitrary properties `x`/`y` (EX37) and native external code `99` (EX38) |
| Unmodified 10.5 | Prefer 10.6; if 10.5 must be retained, convert an isolated copy using the procedure below before writing the production INPUT |
| Deliberately patched 10.5 that must be preserved | Isolated clean-room code-99/property-channel interface described below |

Do not patch 10.5 merely because an old INPUT contains `99` or `1201`. First prove
which executable and source tree produced the working result.

## Native USPEX 10.6 X/Y properties

USPEX 10.6 supports up to two arbitrary properties. Activate them in `optType` and
implement `Specific/user_property_calculator.py`. The direction is explicit:

```text
% optType
min_x max_y
% EndOptType
```

In multi-objective mode enthalpy is included by the USPEX rules described in the
10.6 manual; do not add or remove it by guesswork. Copy EX37 and preserve the exact
function signature and return convention used by that release.

`Specific/user_property_calculator.py` is the native 10.6 interface. It is called with a
numeric flag and must print **exactly one** line, `<CALLRESULT> <value>`:

| Flag | Request | Convention |
|---|---|---|
| `0` | has this stage converged? | return nonzero only when the stage's own outputs are complete and finite |
| `1` | energy / enthalpy | minimization |
| `101` | property `x` | **maximization by default** |
| `102` | property `y` | **maximization by default** |

The direction prefixes in `optType` are `min_x`/`min_y` to minimize and `max_x`/`max_y` (or the
bare `x`/`y`) to maximize; EX37 ships `1 min_x`. A maximized property is therefore written as
the raw measured value, consistent with the sign convention below. 10.6 records the results in
`results1/Properties` with `Property_X`/`Property_Y` columns and ranks them in
`results1/Pareto_ranking`.

It is not the file that implements the audited patched-10.5 route below. Do not copy its name or
calling convention into a 10.5 installation.

For each property, record:

- scientific definition and units;
- whether larger or smaller is better;
- geometry/engine stage from which it is evaluated;
- model/version/checkpoint and descriptor settings;
- valid numerical domain and failure policy;
- whether values are comparable across composition and cell size.

Return one finite scalar per requested property. `NaN`, empty files, implicit zeros,
and direction changes invalidate Pareto ranking.

## Native USPEX 10.6 external code 99

Copy EX38 before implementing the adapter. For each structure and code-99 stage,
USPEX 10.6 provides:

- `geom.in`: structure in POSCAR format;
- `parameters.json`: geometry and calculation metadata — keys include `symg`, `count`, `Step`,
  `lattice`, `coor`, `numIons`, `varcomp`, `atomType`, `dimension`, `abinitioCode`, `spin`,
  `pressure`, `Kpoints`;
- `userInput_${step}` copied to `usercode.in` for that step.

The external script's *filename* is arbitrary (EX38's is `user_external_code.py`); what binds it
to a stage is the command named in that stage's `commandExecutable`. When stages are
heterogeneous give one line per stage — EX38 lists four GULP lines and a fifth
`python user_external_code.py > usercode.out`, which is the `99` stage. Two files then have
distinct jobs:

- the **external engine** named in `commandExecutable` runs the physics and leaves its own output
  (EX38's stage command redirects stdout to `usercode.out`);
- `Specific/user_property_calculator.py` is the **only** file USPEX reads back. It is invoked
  with `flag` and `Step`, runs inside the structure's calculation directory, and must print
  exactly one `<CALLRESULT> <value>` line: flag `0` = converged (return `1`/`0`), `1` =
  energy/enthalpy, `101`/`102` = properties x/y. Its own documentation states that the
  convergence and energy branches are activated when `abinitioCode = 99`.

On 10.6 the contract is therefore *the callback*, not a fixed set of geometry/energy files:
nothing requires `geom.out`/`energy.txt` unless your callback reads them. EX38's example callback
returns `1` unconditionally for convergence, returns the documented placeholder `5555` when no
enthalpy is available, and parses a `Fitness:` line from `usercode.out` for property x. Replace
the bodies of those functions, never the `main()` signature or the `<CALLRESULT>` printing.

A generic adapter still has this shape:

```text
read geom.in + parameters.json + usercode.in
validate species, cell, pressure, and requested task
run optimizer or predictor
leave whatever your callback will read (geometry, energy, property files)
make the callback strict: flag 0 only when this individual's outputs are fresh and parseable
exit nonzero on infrastructure failure, and never let a stale file look like success
```

Writing a real geometry and a matching energy back is still the right behaviour whenever a later
stage or a VASP re-relaxation consumes the structure. The 10.5 patched route below **requires**
it, because there `userCalcFitness.py` reads `geom.out` and `energy.txt` directly; do not carry
that requirement into the native 10.6 route by reflex.

Before putting the adapter behind USPEX, validate one CalcFold-like directory with the supplied
contract checker. For a composition-dependent property, use a geometry containing every species
class needed by the formula rather than accepting a unary penalty sentinel as the only test:

```bash
python scripts/smoke_code99.py SMOKE_DIR \
  --command '/path/to/python run_external.py' \
  --callback user_property_calculator.py \
  --python /path/to/callback-python \
  --property-flags 101 --penalty -10 \
  --consume-geom-out --preserved relaxed_geom.out \
  --required-file geom.out --required-file energy.txt --required-file P1.txt \
  --require-fresh --min-species 2
```

The checker invokes the callback with the native positional contract `FLAG STEP` and requires
exactly one finite `<CALLRESULT> <value>` line for flags `0`, `1`, and the requested property
flags. Two checks are opt-in and both exist because each one hid a real failure:

- `--penalty VALUE` (repeatable) makes a smoke run fail when a property flag returns one of the
  project's documented penalty sentinels. Without it a unary structure can return the "no valid
  cation site" penalty and the smoke still passes green even though the real property path was
  never exercised.
- `--consume-geom-out` reproduces the native 10.6 hazard that USPEX consumes `geom.out` *before*
  the arbitrary-property callback runs. The geometry is moved aside after the freshness check and
  must still be readable by the callback from the preserved copy, which is what turns the wrapper
  pattern into a regression test instead of a convention.

Otherwise the checker reports values without deciding whether a finite penalty is scientifically
appropriate.

Distinguish two failure classes. An expected scientifically invalid candidate may
be assigned an explicit direction-aware penalty sentinel if the project documents
and tests that policy. Missing models, scheduler/filesystem failures, malformed
inputs, and unhandled exceptions should return nonzero and must not reuse stale
success files.

## Objective direction and the sign convention

The custom property value you write is the **real, larger-is-better quantity** for a
larger-is-better property. The driver keeps two internally monotone views of it:

```text
P1.txt / MLProperties          the value as printed by the positional reader (e.g. space group 129)
Individuals (ML_* column)      the negated copy (e.g. -129)
ParetoRankingInGeneration      ranking fitness, derived from the negated copy
```

The negation in `Individuals` exists so that one objective's "smaller is better" internal
orientation (like the enthalpy column) holds for every column. It is a display/internal
convention, not an instruction to negate your scientific value. Therefore:

- **larger is better** (modulus, space-group number, gap, hardness): write the raw positive
  value, exactly as measured. Do **not** negate it.
- **smaller is better** (a cost, a deviation, a lattice mismatch): write the quantity as
  measured, or its sign-flipped form if you also want the displayed column to read as a
  larger-is-better score — state which convention you chose.
- record the direction next to the value: name, units, larger/smaller-is-better, the sign
  actually written, and the file that carries it.

Verify the realised convention on the exact tree instead of assuming it, with a controlled
probe: a read-only stage that writes a fixed, distinct value per individual (1000, 100, 900,
10, ...), one generation, then read the outputs. Observed on 10.5.0 (08/07/2021) with a custom
`1201` reader that printed the value unchanged:

| File | Value for a probe individual written as 1000 |
|---|---|
| per-structure `P1.txt` | `1000` |
| `results1/MLProperties` | `1000.0` |
| `results1/Individuals` (`ML_*` column) | `-1000.000` |
| `results1/ParetoRankingInGeneration` | `1000.000` (the negated magnitude, used for ranking) |

So: judge the sign convention from `P1.txt`/`MLProperties` versus the `Individuals` column,
and read the ranking from `ParetoRankingInGeneration`. Never conclude "the driver inverted my
property" from the `Individuals` column alone, and never "fix" it by negating a
larger-is-better value — that drives the search to the worst candidates.

### Channel value bookkeeping is scrambled across output files

Filenames such as `Individuals`, `ParetoRankingInGeneration` and `MLProperties` are written by
different parts of the driver and their **per-individual IDs do not line up**. In a 14-relaxation
probe the `Individuals` rows and the stage calls for the same ID disagreed. Never join these
files row-by-row to attribute a property value to a structure. Recover the mapping from the
per-structure `CalcFold*` directories (the stage's own log, `geom.in`/`geom.out`, energy and
property files are all per-individual there), and prefer those directories as the authoritative
source for analysis.

## Reproducible USPEX 10.5 custom pattern

The group method used two narrow extension points in an isolated 10.5 source tree:

1. `FunctionFolder/Tool/userCalcFitness.py` adapted every code-99 stage to the
   geometry/energy handshake;
2. `FunctionFolder/calcElasticML.py` redirected the seven stock ML-elastic slots
   `1201`-`1207` to seven positional custom-property values.

These are installation-level files, not ordinary files under `Specific/`. Modify
only an isolated copy of the exact installation and record the original and
modified checksums. Replacing them changes every run that uses that executable.

Observed file-level contract:

```text
USPEX writes geom.in
external command relaxes/evaluates it
external command writes geom.out and energy.txt
external command also writes the custom scalar file(s)
userCalcFitness.py reports completion/enthalpy for the code-99 stage
calcElasticML.py reads the custom files and emits seven positional values
```

### Who provides which file

A code-99 stage directory (`CalcFold<ID>`) is prepared **by USPEX**. An instrumented callback run
on 10.5.0 recorded exactly these files present when the callback was called:
`geom.in` (POSCAR, title `EA<ID> ... Sym.group: N`), `parameters.json` (the same metadata keys
documented for 10.6: `symg`, `count`, `Step`, `lattice`, `coor`, `numIons`, `varcomp`,
`atomType`, `dimension`, `abinitioCode`, `spin`, `pressure`, `Kpoints`), the Perl helper
`getStuff`, and an empty `input`. Everything else in that directory is the project's
responsibility.

- **Energy returns through exactly one channel: the callback's stdout.** USPEX runs
  `python3 -W ignore <install>/.../Tool/userCalcFitness.py <FLAG> <STEP>` with the stage directory
  as cwd and reads `<CALLRESULT> <value>` (flag `0` = finished, `1` = energy/enthalpy, and the
  property flags `2`–`11` with `-1`/`-2`/`-5` as collect-all variants).
- **The relaxed geometry has no documented return filename.** `geom.out` and `energy.txt` occur
  **nowhere** in the shipped 10.5 tree or manual — they are this project's own convention and work
  only because the patched callback reads them. What USPEX itself does is re-derive the structure
  and pass a symmetrized copy **as a path** to the second, positional callback:
  `<install>/.../calcElasticML.py <STRUCTURE> <MODELPATH>` → `<CALLRESULT> v1 ... v7` for
  `1201`–`1207`. The observed structure argument was `MLPOSCAR` (same cell, title line
  renumbered by symmetry), not `geom.out`.
- `abinitioCode = 99` is undocumented in the manual's code list (which stops at `12`) but is
  functional: `OUTPUT.txt` labels the stage `USERcode`.
- On a **stock** tree the stage "runs" and yields placeholders: the stock callback answers
  `flag 0 → 1`, `flag 1 → 5555`, and a random number for every other flag.

For remote execution of a 99 stage, see the transport limitation in
[`submission.md`](submission.md).

### What to change in `userCalcFitness.py`

Preserve the installed script's command-line interface: `flag` and `Step` are the
first two arguments, and every response uses `<CALLRESULT>` followed by the value.
Change only the branches needed for the external code:

- `flag == 0`: return `1` only when the current stage has a nonempty, parseable
  `geom.out` and a nonempty, finite `energy.txt`; otherwise return `0`. Checking
  mere file existence is insufficient when stale files are possible.
- `flag == 1`: read one finite energy/enthalpy from `energy.txt`, return it, and
  remove or archive that file only after successful parsing if the installed
  workflow expects consumption. Do not return a fabricated zero.
- Preserve stock behavior for unrelated flags unless the isolated patch explicitly
  assigns them. The custom `1201`-`1207` values are supplied by
  `calcElasticML.py`, not by the catch-all branch of `userCalcFitness.py`.

The observed two-stage ML workflow needs a carry-forward file because the first
stage's `energy.txt` is consumed by `flag == 1`:

| Stage | Required fresh outputs | Purpose |
|---|---|---|
| ML-potential relaxation | `geom.out`, `energy.txt`, durable energy backup | relaxed geometry and matching enthalpy |
| property predictor | carried `geom.in -> geom.out`, restored/recomputed `energy.txt`, `P1.txt`... | retain the structure/enthalpy contract while adding objectives |

Name the durable backup project-locally (for example `energy-old.txt`) and verify
that it belongs to the same structure. Never restore a backup from a previous
individual or retry.

### What to change in `calcElasticML.py`

The stock file predicts seven elastic quantities and prints them in the order used
by `1201`-`1207`. The isolated patch keeps that invocation and arity but replaces
the model evaluation with strict reading of the custom result files:

```text
structure_path = argv[1]
calculation_directory = parent directory of absolute structure_path
values = finite values read in the documented P1 ... P7 order
emit "<CALLRESULT> v1 v2 v3 v4 v5 v6 v7"
```

Keep an optional second model-path argument accepted if the installed caller passes
one, even when the custom reader does not use it. Map `P1` to `1201`, `P2` to
`1202`, and so on; `optType 1201` therefore consumes the first emitted value.
Document each position's scientific name, units, optimization direction, valid
domain, and penalty sentinel. Validate finiteness before printing. Never silently
turn a missing or malformed property into `0.0`; an explicit sentinel is allowed
only when it is directionally unfavorable and the run records that policy.

This is the 10.5 counterpart of a property calculator. Calling it
`user_property_calculator.py` would conflate it with the different native 10.6
interface.

Typical INPUT pattern:

```text
% optType
1 1201 [15]
% EndOptType

% abinitioCode
1 1 1 1 1 99
% ENDabinit
```

This means “use enthalpy first, then enthalpy plus the custom property” only in the
documented patched installation. On stock 10.5, `1201` means the official ML bulk
modulus objective. The bracketed switch generation must be verified in
`OUTPUT.txt`/`Parameters.txt`.

### The switch generation must be later than generation 1

On 10.5.0 (08/07/2021) a switch generation of `1` — the second objective active inside the
first generation — makes the driver abort at the end of generation 1:

```text
Undefined function or variable 'multiFit'.
Error in extractStructures (line 56)
```

This reproduces with the **stock** ML channel as well, so it is a driver defect, not an artifact
of the custom patch. Use `[N]` with `N >= 2` (for example `1 1201 [2]` with
`numGenerations >= 2`): generation 1 runs as a single-objective phase while the property is
still computed and recorded, and the multi-objective ranking is produced from generation 2 on.
Verify from a toy run that `results1/MLProperties` is populated even in the generations before
the switch, and that `ParetoRankingInGeneration` appears once the switch is reached.

Some legacy projects set `[N]` larger than `numGenerations` to calculate and record
the property channel without activating it as a Pareto objective during the planned
run. Treat that as an explicit data-collection mode and prove from a toy run that
the property is still evaluated. A remapped `1201` result can also retain an
official-looking `MLProperties` header such as bulk modulus; record the semantic
alias and units in project metadata instead of trusting that legacy column label.

### Convert an unmodified 10.5 installation

Start by proving that the installation is stock. Record the exact `USPEX` launcher,
`VERSION`, and checksums of `FunctionFolder/calcElasticML.py` and
`FunctionFolder/Tool/userCalcFitness.py`. A stock 10.5 tree normally still uses the
elastic-model implementation for `1201`-`1207`; its sample `userCalcFitness.py`
may report unconditional completion, a placeholder energy, or a random fitness.
Any of those behaviors means code 99 is not ready for production custom objectives.

1. Copy the complete 10.5 installation to a separately named custom installation.
   Keep the stock installation immutable and available for ordinary calculations.
2. Point a clean shell explicitly at the copied launcher and confirm that its
   `FunctionFolder` is the copied one. Do not assume that changing a source file
   changed the executable selected by `PATH`. Run
   `python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.5`: it fails when the
   `USPEX` on `PATH` does not resolve to the tree `USPEXPATH` points at, and confirms
   the helper interpreter can import numpy/scipy/spglib and the shipped topology
   extension.
3. Preserve `calcElasticML.py` and `Tool/userCalcFitness.py` as versioned originals,
   then make the two independent changes described above: geometry/energy handling
   in `userCalcFitness.py`, and positional property mapping in `calcElasticML.py`.
4. Implement the external stage command separately. It must read `geom.in`, write
   fresh `geom.out` and `energy.txt`, and write the custom property files needed by
   `calcElasticML.py`. A predictor that does not relax still copies the current
   stage input geometry forward.
5. Build a one-structure harness that exercises: not done, success, non-finite
   energy/property, missing `geom.out`, stale backup, engine failure, and retry.
6. Verify directly that `userCalcFitness.py 0 STEP` changes from `0` to `1` only
   after valid outputs exist, that flag `1` returns the expected energy, and that
   `calcElasticML.py STRUCTURE [MODEL]` returns the intended seven positions.
7. Run a one-structure code-99 calculation with `numParallelCalcs = 1`; inspect the
   callback output and `Parameters.txt` before testing multi-objective selection.
8. Run a tiny population and prove that `MLProperties`, `Individuals`, and
   `Pareto_ranking` carry the intended values and direction.
9. Archive the original/modified checksums, patch diff, interface specification,
   test outputs, units, sentinel policy, and model identity with the project. Never
   publish private teaching/model implementation.

If a copied installation ignores edits to these Python files, stop: that package
may embed or compile the FunctionFolder. Rebuild it only by the release's supported
method, or use the working archive-based distribution; do not keep editing files
that the selected launcher never imports.

This is enough for a user to implement an equivalent adapter without receiving the
private code.

## Useful stage compositions

### VASP relaxation followed by one property

```text
VASP coarse → VASP medium → VASP fine → VASP final → code 99 property
```

The code-99 stage should preserve the final VASP geometry in its required output
geometry while writing the property result.

### Screen, refine, evaluate

```text
VASP coarse/medium → code 99 cheap screen → VASP fine → code 99 final property
```

Use this only when every selected individual receives a well-defined objective.
Record the screening rule; do not silently assign a favorable property to skipped
or failed candidates.

### ML relaxation and learned-property objective

```text
code 99 MatterSim/Prophet-like relaxation → code 99 descriptor/property predictor
```

The relaxation stage reads `geom.in`, optimizes cell and positions under the stated
pressure convention, and writes a validated `geom.out` plus `energy.txt`. The
second stage carries `geom.in` to `geom.out`, restores or recomputes the matching
`energy.txt`, and writes the feature-derived scalar files. `calcElasticML.py` then
maps those files into the selected `1201`-`1207` positions. Record model versions
and validate shortlisted structures with consistent VASP settings.

## Adapter acceptance tests

- Species order and atom counts are preserved.
- Cell determinant is positive and volume/shortest-distance checks pass.
- Geometry and result files are written by rename from temporary files.
- Energy/property values are finite, unit-labeled in logs, and direction-tested.
- A deliberate infrastructure/model crash produces a nonzero exit and no stale
  success files; a scientifically invalid candidate receives only the documented
  unfavorable penalty sentinel.
- `userCalcFitness.py` rejects a missing/empty/non-finite energy and does not report
  completion from files left by a previous attempt.
- `calcElasticML.py` maps known `P1`...`P7` values to `1201`...`1207` in order.
- Re-running the same structure is deterministic within the model's tolerance.
- A two-structure toy Pareto test ranks a manually predictable trade-off correctly.
