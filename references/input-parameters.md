# INPUT.txt parameter map

> Load this when: authoring, auditing, or cost-planning `INPUT.txt`; when a keyword's default,
> units, or legal values matter; or when USPEX stops earlier/later than expected.

## The installation is the dictionary — do not copy it

Every packaged tree ships the **complete per-parameter manual source** next to the executable:

```text
<install>/application/archive/doc/uspex_keywords.xml   # 10.5.0: 106 parameters
<install>/application/archive/doc/uspex_manual_english.pdf
<install>/application/archive/doc/uspex_manual_chinese.pdf
<install>/application/archive/examples/all_keywords.txt
```

This file follows the **10.6 manual's section order** for the keywords it lists, so a row can be
checked against the matching page of the release in hand. Two cautions:

- USPEX renumbered its input chapters between 10.5 and 10.6. `AutoFrac` is the last entry of
  manual chapter 4.4 in 10.6, while the same keyword sits near the end of the operator chapter
  in 10.5; always locate a keyword by name in the installed `uspex_keywords.xml` rather than by
  section number.
- Values that differ between 10.5 and 10.6 are marked as such. When only the 10.6 page defines a
  keyword (for example `fracDB`), the installed 10.5 dictionary is the authority on whether it
  exists at all.

`uspex_keywords.xml` records `name`, `description`, `possible values`, `default`, `example`
and `notes` for each parameter. Extract the authoritative list for the installed release
rather than trusting this file or reproducing manual prose:

```bash
python3 - <<'PY'
import re
text = open("<install>/application/archive/doc/uspex_keywords.xml", encoding="utf-8",
            errors="replace").read()
starts = [(m.start(), m.group(1)) for m in
          re.finditer(r'<tocitem target="name">\\paramacro\{([^}]+)\}</tocitem>', text)]
for i, (pos, name) in enumerate(starts):
    block = text[pos:starts[i + 1][0] if i + 1 < len(starts) else len(text)]
    def field(tag):
        m = re.search(r'<tocitem target="%s">(.*?)(?=<tocitem target=|\Z)' % re.escape(tag),
                      block, re.S)
        return re.sub(r"\s+", " ", re.sub(r"\\[a-zA-Z]+", " ", m.group(1))).strip() if m else ""
    print(name, "| default:", field("default"))
PY
```

Never paste manual text into the skill, a repository, or a chat answer. Read it locally, quote
at most a phrase, and cite the installed release.

**The scope below is deliberate.** This file carries the parameters that change a decision, a
cost estimate, or a failure diagnosis in the workflows this skill supports. Anything not
listed is either an advanced operator knob, or belongs to a calculation method the skill does
not cover (`META`, `VCNEB`/`TPS`, `PSO`, `MINHOP`, molecular crystals) — the closing
"Out of scope" section names the families, and the install is the dictionary for them.

## Core setup

| Parameter | Default | What it decides |
|---|---|---|
| `calculationMethod` | `USPEX` | Manual choices: `USPEX`, `META`, `VCNEB`, `PSO`, `TPS`, `MINHOP`, `COPEX`. This skill covers `USPEX` only; the others change the meaning of many keywords. `PSO` is documented in manual 5.11 and is not the same as a USPEX run |
| `calculationType` | `300` | Three indices — dimensionality (`3` bulk, `2` surface, `-2` 2D crystal, `1` polymer, `0` nanoparticle), molecularity (`0`/`1`), composition variability (`0` fixed / `1` variable) — plus an optional magnetic flag `s`/`S` (written before or after, e.g. `s300`, `s201`). Manual options: `300 301 310 311 000 200 201 -200 -201 110`, each also valid with the magnetic flag |
| `AutoFrac` | `0` | `0` = USPEX uses your explicit `frac*` (it still normalises them, see below); `1` = USPEX evolves the operator mix itself (manual: up to ~2× faster) and your explicit `frac*` no longer fixes it. The 10.6 sample INPUT (manual 9.3) ships `1 : AutoFrac`; a plain authored file gets `0` unless you set it |
| `atomType` | none — required | The global species order. Every other per-species list follows it |
| `numSpecies` | none — required | One row per compositional block, in `atomType` order |
| `minAt` / `maxAt` | **none — required for `301`/`201`** | Atom-count bounds (molecule counts for the molecular `311`); without them a variable-composition run has no search space. Manual: keep them within a factor of about 2, not 2–3 |
| `ExternalPressure` | `0` (GPa) | Belongs in `INPUT.txt`, not in `INCAR_i`; must be identical for every energy entering one ranking or hull |
| `symmetries` | 3D crystals `2-230`; 2D crystals (layer groups) `2-80`; surfaces (plane groups) `2-17`; clusters point groups | Restricts the groups used for random generation; a wrong range silently changes the sampling. Do not collapse 2D and surface into one range — they are different group families (manual 4.4, 5.8) |
| `doSpaceGroup` | `1` for `3**`, else `0` | Also writes `*.CIF`; space groups can be under-determined for near-symmetric cells |
| `symmetrize` | `0` | Snaps every structure to a standard symmetry-adapted setting — useful for reporting, dangerous if it suppresses the distortion you are looking for |
| `SymTolerance` | `medium` (= `0.08` Å) | Symmetry-finder precision, number or `high`/`medium`/`low` = `0.04`/`0.08`/`0.15` |
| `IonDistances` | estimated `0.2·(V_A^(1/3)+V_B^(1/3))` | Hard lower bound on interatomic distances; the matrix block ends with `% EndDistances`, **not** `% EndIonDistances` |
| `goodBonds` | estimated; printed in `OUTPUT.txt` | Minimum bond valences for hardness/connectivity criteria |
| `checkMolecules` | `1` | Post-relaxation check that molecules are intact (molecular crystals) |
| `Latticevalues` | automatic | Initial cell volume / known lattice parameters; leave unset unless the cell must be pinned |

## Population, stopping, and operators

| Parameter | Default | What it decides |
|---|---|---|
| `populationSize` | `2N` rounded to the nearest 10, where `N` = atoms/cell or `maxAt`; **cap 60** | Generation size, and therefore cost |
| `initialPopSize` | equal to `populationSize` | Generation 1. Larger is usual for variable composition or unfamiliar chemistry |
| `numGenerations` | `100` | Hard generation ceiling. The search normally stops earlier |
| `stopCrit` | total atom count (fixed composition) / `maxAt` (variable composition) | For variable composition the manual gives it a different meaning (manual 5.1): the maximum number of generations during which a structure may reappear as a parent, suggested `20` — not "generations without a new best". State which reading you rely on |
| `stopFitness` | none | Absolute fitness target; stops the run when reached |
| `bestFrac` | `0.7` | Fraction of the generation eligible to become parents; manual suggests `0.5`–`0.8` |
| `keepBestHM` | `0.15 × populationSize` | How many best structures survive into the next generation. For variable composition the manual redefines it (manual 5.1): all convex-hull structures survive plus the nearest metastable ones, up to this total |
| `firstGeneMax` | `11` | Distinct compositions sampled in generation 1 (variable composition); `0` means `initialPopSize/4`. Manual recommends `11` for binaries and about `30` for ternaries |
| `fracGene` | `0.5` | Share of heredity (crossover) offspring |
| `fracRand` | `0.2` | Share of random (space-group) structures |
| `fracTopRand` | `0.2` | Share of topological-random structures; requires the helper Python to import the shipped compiled `randomTopology` module. A copied example may set another value |
| `fracAtomsMut` | `0.1` | Share from softmutation/coormutation |
| `fracTrans` | `0.1` | Share from transmutation (variable-composition runs) |
| `fracLatMut` | `0` fixed-cell, `0.1` otherwise | Share from lattice mutation. The manual authors advise `fracLatMut = 0` even where `0.1` applies, because the operator perturbs the cell strongly; enable it deliberately |
| `fracPerm` | `0.1` if more than one species, else `0` | Share from permutation |
| `fracRotMut` | `0.1` for molecular crystals, else `0` | Share from molecular-orientation mutation. Manual restricts it to molecular-crystal prediction (`calculationType = 310`, `311`) |
| `fracSpinMut` | `0.1` | Share from spin mutation; used only for magnetic structure optimization (`calculationType` with the `s`/`S` flag). Every spin mutation changes the magnetic state — FM-L never returns to FM-L — and the ratio of new states is set by `magRatio` (manual 4.4) |
| `splitInto` | `1` | Number of identical subcells/pseudosubcells per cell; introduces translational (pseudo)symmetry. Manual: use splitting only for systems above ~25–30 atoms/cell. Keyblock is `% splitInto` … `% EndSplitInto` |
| `howManySwaps` | `0.5 ×` (maximum possible swaps) | Permutation strength: the number of pairwise swaps is drawn uniformly from `1..howManySwaps`; the maximum is `min(Na,Nb) + min(Nc,Nd)` over swappable pairs. Manual recommends keeping the default in most cases |
| `specificSwaps` | blank = all species permutable | Restricts which atom types may swap. Keyblock is `% specificSwaps` … **`% EndSpecific`** — the asymmetric terminator is a real parse trap |
| `fracDB` | 10.6 only, absent from the 10.5 dictionary; manual default `0.0` | DAICS database share, a variation operator like the `frac*` family (EX37 ships `0.10 : fracDB`). It can be enabled in a multicomponent 300/301 search, but imported candidates are unary/binary; higher-order compositions come from other operators |
| `reoptOld` | `0` | Re-relax previously relaxed structures |
| `repeatForStatistics` | `1` | Automatically repeats the **whole search** this many times — it multiplies walltime and interacts with the driver loop and `USPEX_IS_DONE` |

Keep the `frac*` family **in the ratio you intend** — it does not have to sum to 1: USPEX
normalises the fractions when they do not (manual 4.4 NOTE), so `0.25/0.25/0.25/0.25` and
`1/1/1/1` describe the same mix. What matters is that no operator you rely on is left at 0.
`AutoFrac = 0` uses your explicit fractions; `AutoFrac = 1` replaces the ratio with one USPEX
evolves itself. Copy the mix from the nearest shipped example and record why it was changed.

## Ab initio stages and submission

| Parameter | Default | What it decides |
|---|---|---|
| `abinitioCode` | `1` for every step (VASP) | One entry per relaxation/property stage; `1` = VASP, `99` = external engine |
| `commandExecutable` | **none — must be specified** | One reusable command line or one line per stage |
| `KresolStart` / `Kresolend` | `0.2` down to `0.08`, linearly | k-point resolution (2π/Å) per stage; should become finer across stages |
| `numParallelCalcs` | `1` | Maximum structure calculations USPEX keeps in flight. With `whichCluster=1/2`, this includes queued jobs and need not equal available GPUs/nodes; it is not MPI ranks per job |
| `whichCluster` | **`0` = no job script** | `0` launches engines directly, `1` uses local `Submission/`, `2` remote submission |
| `remoteFolder` | none | Only active with `whichCluster = 2` |

## 2D and surface geometry

| Parameter | Default | Meaning |
|---|---|---|
| `thicknessS` | `2.0` Å | Surface region; adatoms are allowed only here |
| `thicknessB` | `3.0` Å | Buffer region, part of `POSCAR_SUBSTRATE`, allowed to relax |
| `vacuumSize` | `10` Å per relaxation step | Vacuum between periodic images (surfaces, 2D, nanoparticles) |
| `reconstruct` | `1` | Maximum multiplication of the surface cell, enabling complex reconstructions |
| `POSCAR_SUBSTRATE` | none | File, not a keyword; required for `200`/`201` |

## Restart

| Parameter | Default | Meaning |
|---|---|---|
| `pickUpYN` | `0` | **The enabling switch.** Without `pickUpYN = 1` the other two are ignored |
| `pickUpGen` | `0` | Generation to start from; `0` starts a new calculation |
| `pickUpFolder` | `0` | `results` folder number: `1` = `results1`, `2` = `results2`, … |

The shipped template `test/templates/restart_GULP_301/INPUT_restart.txt` is the reference
example: `1 : pickUpYN`, `2 : pickUpGen`, `1 : pickUpFolder`. A pick-up restart is normally
prepared with `USPEX --clean`, which clears the stale `*.mat` state and the done marker but keeps
every `results<N>/` folder; the earlier run need not have finished, but the named source
generation must be complete. Procedure, the `still_reading` case, and the new `results<N>` the
relaunch writes are in [`seeds-restarts.md`](seeds-restarts.md).

## Reporting and analysis switches

| Parameter | Default | Meaning |
|---|---|---|
| `PhaseDiagram` | `0` | `1` produces the crude built-in phase diagram for `300`/`301` — a first look, not a substitute for a properly referenced convex hull |
| `FormatType` | `2` (POSCAR) | Structure format written under `results1/PATH/` |

## 10.6 arbitrary properties and external code

The route is covered in [`custom-objectives.md`](custom-objectives.md). Three syntax facts
belong here:

- `optType` accepts `x` and `y` as objectives, with direction prefixes: bare `x`/`y` and
  `max_x`/`max_y` maximize, `min_x`/`min_y` minimize. EX37 ships `1 min_x`.
- The 10.6 `Specific/user_property_calculator.py` is called with a numeric flag:
  `0` = converged?, `1` = energy/enthalpy (minimization), `101` = property x and
  `102` = property y, **both maximization by default**. Print exactly one line,
  `<CALLRESULT> <value>`.
- For code `99`, the external script's *filename* is arbitrary — what matters is the command
  named in the stage's `commandExecutable`. `abinitioCode` takes `99` as an ordinary stage on
  10.6 (EX38 ships `3 3 3 3 99` with one command line per stage); the callback
  `Specific/user_property_calculator.py` (flags `0`/`1`/`101`/`102`) is what USPEX reads back.
  USPEX hands the stage `geom.in` (POSCAR), `parameters.json` (keys include `symg`, `count`,
  `Step`, `lattice`, `coor`, `numIons`, `varcomp`, `atomType`, `dimension`, `abinitioCode`,
  `spin`, `pressure`, `Kpoints`) and copies `Specific/userInput_<step>` to `usercode.in`.

## Defaults that surprise people

Check these before blaming the scheduler or the engine:

1. `whichCluster = 0` — no job script, engines run in place.
2. `numParallelCalcs = 1` — one structure at a time.
3. `populationSize` caps at 60 and is derived from the cell contents, not from the composition space.
4. `numGenerations = 100`, and `stopCrit` defaults to an atom count in a **fixed-composition**
   run (manual 4.2). In a **variable-composition** run the manual redefines `stopCrit`
   (manual 5.1) as how many generations a structure may reappear as a parent; do not carry the
   fixed-composition reading into a `301`/`201` run.
5. `minAt`/`maxAt` have **no** default; a `301`/`201` run without them is not a search.
6. `pickUpYN = 0` — `pickUpGen`/`pickUpFolder` alone do nothing.
7. `optType` negative values flip direction (for example `-3` minimizes hardness), which is the
   built-in counterpart of the custom-property sign convention.
8. `AutoFrac = 0` — an authored `INPUT.txt` keeps your explicit `frac*` ratio; only an example
   that carries `1 : AutoFrac` (the 10.6 sample INPUT does) is self-tuning. Do not assume a
   copied file is self-tuning.
9. Explicit `frac*` values need not sum to 1 — USPEX normalises them (manual 4.4 NOTE). A sum of
   0.25 each is the same mix as 1 each.
10. `symmetries` defaults differ by dimensionality: 3D `2-230`, 2D crystals `2-80`,
    surfaces `2-17`. The 2D and surface group families are not interchangeable.
11. `calculationType` may carry the magnetic flag `s`/`S` (`s300`, `s201`), which switches on
    magnetic structure optimization and gives `fracSpinMut`/`magRatio` a meaning.
12. `howManyTrans` and `howManySwaps` are *fractions/upper bounds drawn from a distribution*
    (`howManyTrans` is a fraction of atoms, default `0.2`; `howManySwaps` is a swap-count ceiling,
    default `0.5 ×` the maximum), not fixed counts.

## Out of scope for this skill

META, VCNEB/TPS, PSO and MINHOP keywords (`Kconstant`, `K_min`/`K_max`, `numImages`, `numSteps`,
`dt`, `ConvThreshold`, `VarPathLength`, `vcnebType`, `optRelaxType`, `optFreezing`,
`optReadImages`, `optMethodCIDI`, `startCIDIStep`, `pickupImages`, `shiftRatio`,
`amplitudeShoot`, `magnitudeShoot`, `GaussianHeight`, `GaussianWidth`, `FullRelax`, `PSO_*`,
`orderParaType`, `trajectoryFile`, `MDrestartFile`, `cmdEnthalpyTempture`, `PrintStep`,
`fixRndSeed`, `collectForces`), molecular-crystal-only keywords (`MolCenters`, `speciesSymbol`,
plus the `310`/`311` workflows themselves), and the advanced operator knobs
(`dynamicalBestHM`, `manyParents`, `numberparents`, `maxDistHeredity`, `percSliceShift`,
`mutationRate`, `mutationDegree`, `DisplaceInLatmutation`, `constraint_enhancement`,
`specificTrans`, `valences`, `valenceElectr`, `mass`, `magRatio`). If a task needs one of these,
read the installed dictionary and the matching example instead of guessing.

Magnetic structure optimization is a boundary case: this skill documents the `s`/`S` flag,
`fracSpinMut`, and the failure modes it interacts with, but magnetic *analysis* (comparing
NM/FM/AFM energies, spin arrangements, and their effect on a ranking or hull) is out of scope.
Treat a magnetic run as a bulk run whose energy ordering depends on the spin policy, and record
that policy with the results.
