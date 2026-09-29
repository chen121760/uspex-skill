# Designing and running a USPEX search

> Load this when: choosing a calculation type, building `INPUT.txt`, defining a composition space, or arranging VASP stages.

## Route by release first

| Need | USPEX 10.5 | USPEX 10.6 |
|---|---|---|
| Standard enthalpy/property optimization | Supported | Supported |
| Official ML elastic objectives `1201`-`1207` | Supported | Supported; confirm installed manual |
| Arbitrary property X/Y | No native generic interface | Native `x`/`y`; use EX37 |
| Arbitrary external DFT/MD/ML engine as code `99` | Only through a deliberate local patch/interface | Native; use EX38 |
| DAICS database fraction `fracDB` | Not available | May be enabled in 3D 300/301 multicomponent searches, but the imported candidates themselves are unary/binary only |

Record the full patch-level version when possible. Never combine a 10.6-only INPUT
keyword with a 10.5 executable and then debug it as a scheduler problem.

Code `99` is the arbitrary-external-engine stage. On stock 10.5 the slot exists
but requires a patched installation (see [`custom-objectives.md`](custom-objectives.md)).
On 10.6 it is a native, ordinary entry in `abinitioCode`: one entry per stage, one
matching `commandExecutable` line, and the value USPEX reads back comes from
`Specific/user_property_calculator.py`, not from USPEX parsing the engine's output.

A relaxation followed by a property evaluation is **one** 10.6 stage, not two: the
wrapper named in `commandExecutable` calls both calculators in order inside the same
structure directory. Chaining code-99 stages and passing `geom.out`/`energy.txt`
between them is the 10.5 pattern and silently loses the objective on 10.6 — see
[`custom-objectives.md`](custom-objectives.md) for the mechanism, the reproduced
symptoms, and a working adapter.

## Environment and runtime expectations

Before designing the chemistry, confirm the runtime can actually execute a search:

- The `python3` that USPEX uses — the first one on `PATH` in the shell that launches `USPEX -r`
  — must import `numpy`, `scipy` and `spglib`, and must be able to load the shipped topology
  module. This 10.5.0 package ships `randomTopology.cpython-<tag>.so` for CPython **3.6–3.9**
  only (no 3.10+ build); the 10.6 update replaces them with one stable-ABI
  `randomTopology.abi3.so` that carries no minor pin. A version that has no usable shipped
  module means the topology generator silently degrades:

  ```text
  Unable to import numpy and scipy, Random Topology will be skipped!
  ModuleNotFoundError: No module named 'numpy'      # or
  ImportError: randomTopology
  ```

  The structures are then built from space groups instead of the topology database, so a run
  that "works" can be scientifically weaker than intended. `python scripts/check_uspex_env.py`
  reports the interpreter version, the shipped tags, and the imports, and names the helper
  scripts that need each missing package.
- `USPEX -r` performs **one batch of work and exits**; a search completes by repeating it until
  `USPEX_IS_DONE` exists. Never read a single exit 0 as completion, and never leave the run to
  an interactive shell that an SSH disconnection will kill.
- If a per-run driver script is not supplied, create one (see `assets/run_uspex_loop.sh`) and
  start it detached only after the foreground pass proved parsing, submission, status checking
  and result ingestion.

Details and the symptom table live in [`environment.md`](environment.md).

## Start from the nearest installed example

```bash
USPEX -e
mkdir my-search && cd my-search
USPEX -c 18   # example only; select the number printed by this installation
```

Keep the copied example's directory structure while replacing every scientific
and site-specific choice. The most useful examples are indexed in
`examples/README.md`. Defaults, legal values, and the two switches that most often
change a run's cost or length (`populationSize`, `stopCrit`) are tabulated in
[`input-parameters.md`](input-parameters.md).

## Calculation type matrix

| System | Fixed/single-block | Variable composition | Required geometry extras |
|---|---:|---:|---|
| 3D bulk atomic crystal | `300` | `301` | none |
| 2D atomic crystal | `-200` | `-201` | `thicknessS`, `vacuumSize` |
| Surface reconstruction | `200` | `201` | `POSCAR_SUBSTRATE`, `thicknessS`, `thicknessB`, `reconstruct` |

Use 3D space groups for bulk, layer groups `2-80` for 2D searches, and plane
groups `2-17` for surface reconstruction. A 2D search is not a surface search:
it has no substrate and no surface reference-energy bookkeeping.

The 10.5/10.6 manuals and shipped examples document 2D and surface
variable-composition workflows primarily for unary/binary systems. Do not assume
that a ternary or quaternary 2D/surface search is supported just because 3D `301`
supports it; verify the installed release with a reduced smoke test.

## Composition blocks

`atomType` fixes the global species order. Every row of `numSpecies` is a
compositional building block in that same order.

Fixed 3D A2B3:

```text
300 : calculationType
% atomType
A B
% EndAtomType
% numSpecies
2 3
% EndNumSpecies
```

Full binary, ternary, and quaternary variable-composition spaces use identity
blocks:

```text
# binary A-B
% numSpecies
1 0
0 1
% EndNumSpecies

# ternary A-B-C
% numSpecies
1 0 0
0 1 0
0 0 1
% EndNumSpecies

# quaternary A-B-C-D
% numSpecies
1 0 0 0
0 1 0 0
0 0 1 0
0 0 0 1
% EndNumSpecies
```

Use chemically meaningful compound blocks to restrict a section through
composition space. For an AO-BO2 pseudobinary, for example:

```text
% atomType
A B O
% EndAtomType
% numSpecies
1 0 1
0 1 2
% EndNumSpecies
```

The row count is the number of independent blocks, not the number of objectives.
Check that the block matrix spans the intended compositions and that `minAt` /
`maxAt` admit the desired formula sizes, and keep the two bounds within a factor
of about two of each other (manual 5.1). Quaternary searches need substantially
more diversity and validation than binary searches; choose population size and
cell bounds from a reduced pilot rather than a copied constant.

## Fitness block

Enthalpy is objective `1` in the common bulk workflow:

```text
% optType
1
% EndOptType
```

A staged Pareto pattern such as the following uses enthalpy alone through an
initial generation window and then activates the second objective:

```text
% optType
1 1201 [10]
% EndOptType
```

Confirm `[N]` semantics in the installed manual and outputs. The group workflows
use it as the generation at which the multi-objective phase begins. Never use
`1201` as a custom property on an unpatched 10.5 installation; its official
meaning is an ML elastic property. Arbitrary 10.6 properties are covered in
`references/custom-objectives.md`; a patched 10.5 custom-property interface is
described in `references/custom-objectives-105.md`.

## Variation-operator mix

The operator fractions are the search's sampling policy, so set them deliberately
rather than inheriting them from an unrelated example. Manual chapter 4.4 documents
`fracGene`, `fracRand`, `fracTopRand`, `fracPerm`, `fracAtomsMut`, `fracRotMut`,
`fracLatMut`, `fracSpinMut`, `AutoFrac`, `symmetries`, `splitInto`, `howManySwaps`
and `specificSwaps`; defaults, the units of each, and the 2D/surface group ranges
are tabulated in [`input-parameters.md`](input-parameters.md).

Three facts that change how the mix behaves:

- the explicit `frac*` values do **not** have to sum to 1 — USPEX normalises them, so
  what matters is the ratio (manual 4.4 NOTE);
- `AutoFrac = 0` uses your explicit ratio, while `AutoFrac = 1` lets USPEX evolve the
  mix itself and supersedes that ratio; only examples that ship `1 : AutoFrac` are
  self-tuning;
- `symmetries` takes a different group family per dimensionality — 3D space groups
  `2-230`, 2D layer groups `2-80`, surface plane groups `2-17` — and `splitInto`
  (subcells) is meant for cells above roughly 25–30 atoms.

For a magnetic search (`calculationType` with the manual's `s`/`S` flag), the
spin-mutation share and `magRatio` participate in the mix, and every structure is
evaluated as NM/FM/AFM, so the energy ordering itself changes.

## VASP relaxation chain

Each entry in `abinitioCode` is one relaxation/property stage; code `1` is VASP.

```text
% abinitioCode
1 1 1 1
% ENDabinit

% KresolStart
0.16 0.12 0.09 0.06
% Kresolend
```

For a VASP stage at position `i`, provide `Specific/INCAR_i`. Provide
`Specific/POTCAR_<element>` for each `atomType`, in exactly the same species order.
Never place licensed POTCAR content in a repository or assistant response.

Use at least three progressively stricter relaxations for production searches:

1. robust coarse relaxation that tolerates poor generated structures;
2. cell/ion relaxation at intermediate precision;
3. strict final relaxation or static evaluation consistent across candidates.

Tune the actual number of stages to chemistry and cost. `KresolStart` should become
finer across stages and have one value per relevant stage according to the copied
example. Place `ExternalPressure` in `INPUT.txt`, not in individual relaxation
files. Keep functional, POTCAR choice, ENCUT, spin/U policy, smearing, and pressure
consistent for every energy entering one ranking or convex hull.

`commandExecutable` may contain one command reused for all compatible stages or
one line per stage. When engine types differ, provide one explicit command per
stage so the mapping can be audited.

## Population and parallelism

- `initialPopSize` should usually exceed `populationSize`; variable-composition
  searches benefit from a broader initial population.
- `populationSize` defaults to `2N` rounded to the nearest 10 (where `N` is atoms per cell, or
  `maxAt` for variable composition) and is capped at 60; do not assume your cell contents map
  linearly onto search cost.
- The run stops at `numGenerations` (default 100) **or** after `stopCrit` generations without a
  change in the best structure, whichever comes first — `stopCrit` defaults to an atom count,
  not to `numGenerations`. State which rule you expect to fire.
- `numParallelCalcs` is the maximum number of structure jobs USPEX keeps in flight,
  not MPI ranks per VASP job. Under Slurm/PBS, jobs waiting in the scheduler count toward
  this window, so it may be larger than the number of physical slots when deliberate queueing
  is desired.
- The potential demand ceiling is `numParallelCalcs × resources_per_engine_job`; actual
  simultaneous use is bounded by scheduler availability, association/QOS limits, and the
  per-job request. State both the in-flight window and expected active concurrency.
- Run a small end-to-end pilot before the production population, especially for
  quaternary spaces, surfaces, code-99 engines, and multi-objective searches.

## Surface reconstruction specifics

For `200/201`, `POSCAR_SUBSTRATE` describes the fixed bulk-derived substrate cell;
do not add an arbitrary vacuum layer as if it were a standalone slab. USPEX builds
the surface region using `thicknessS`, `thicknessB`, `reconstruct`, and
`vacuumSize`/the release-specific geometry rules. Start from EX19 (variable B(111))
or EX23 (fixed MgO) and preserve its lattice orientation and atom-order contract.

For `201`, define the composition/reference-energy terms required by the copied
example and validate the resulting surface phase diagram. A low raw cell energy is
not by itself a valid surface stability result.

## Minimal directory contract

```text
run/
  INPUT.txt
  Specific/
    INCAR_1 ... INCAR_N
    POTCAR_A ... POTCAR_Z
    userInput_i                 # code 99, when required
    user_property_calculator.py # 10.6 x/y, when required
  Submission/
    submitJob_local.py
    checkStatus_local.py
  Seeds/                        # optional
  POSCAR_SUBSTRATE              # surface modes only
```

Run `python scripts/preflight_uspex.py run --uspex-version 10.5|10.6` before the
first call to `USPEX -r`.
