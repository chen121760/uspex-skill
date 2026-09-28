# Seeds, restarts, and recovery

> Load this when: introducing known structures, steering composition sampling, resuming an interrupted run, or moving a calculation directory.

## Seed structure format

Place concatenated VASP5 POSCAR records under `Seeds/`. Every record must contain
element symbols, counts, lattice, coordinate mode, and coordinates. Species order
must agree with `atomType`; do not rely on filenames to correct a mismatched order.

Common release/example conventions are:

```text
Seeds/POSCARS       # consume in the next eligible generation
Seeds/POSCARS_1     # seed a named generation/example-specific startup
Seeds/POSCARS_N     # schedule for generation N when supported by that release
Seeds/Seeds_history # USPEX record of consumed seeds
```

Copy the naming convention from an example shipped with the exact executable.
When adding seeds to a running calculation, write a complete temporary file and
rename it into place so USPEX never reads a partial POSCAR stream.

## Composition seeds

For variable-composition searches, `Seeds/compositions` can bias or introduce
specific block combinations. Interpret each row in the same basis as
`numSpecies`. With compound blocks, block coefficients are not direct atom counts.

Before use, map every composition row to an explicit chemical formula and total
atom count. Reject rows outside `minAt`/`maxAt` or outside the intended simplex.

## Seed quality gates

- no duplicate structures under the working symmetry/tolerance;
- physically plausible cell and shortest distances;
- correct dimensionality, vacuum, and substrate convention;
- no mixed selective-dynamics convention unless the engine stage expects it;
- provenance recorded: literature/database/project, transformation, and license;
- known low-energy phases do not crowd out exploration—seed fraction remains a
  scientific choice, not an accidental file-size effect.

## Restarting

Always run `USPEX -r` from the original run directory. State lives in the `*.mat` files
(`USPEX.mat`, `POOL.mat`, `Current_*.mat`, `ANTISEEDS.mat`). Decide which situation you are in
before touching anything.

### The gate is the source generation, not the end of the run

A pick-up restart has exactly one precondition: **the generation it starts from must itself be
completely finished**. Whether the previous attempt ever reached `USPEX_IS_DONE` is irrelevant —
`USPEX --clean` removes that marker as part of the restart anyway. So before restarting, verify in
the source folder `results<f>`:

- `results<f>/generation<g>/` exists and is not empty;
- `results<f>/OUTPUT.txt` contains a completed `SUMMARY of Generation <g>` block;
- `results<f>/Individuals` lists generation `<g>`;
- no driver loop and no scheduler job of this run is still writing into that folder.

If generation `<g>` is incomplete there is no valid parent population to restart from: name an
earlier completed generation, or start over. A run may also have been killed mid-generation, in
which case its last complete generation is the only usable restart point.

Confirm the source folder is the run you intend, as well. A run directory accumulates one
`results<N>` per launch, and older folders can belong to a different chemistry, pressure or
`calculationType`. `results<f>/Parameters.txt` is a copy of the `INPUT.txt` that produced it, so
compare it with the current `INPUT.txt`, and check `results<f>/OUTPUT.txt` for the system and
dimensionality you expect, before using that folder as a restart source.

### A. Continue the same run in place (no `pickUp`)

The `*.mat` state already records the last completed generation, so this needs no `INPUT.txt`
change; the run continues from where it stopped. This is the documented 10.5 behaviour: *"you do
not need to change any settings (all information will be stored in the \*.mat files) ... it will be
sufficient to remove the file `still_reading` and run USPEX again."*

```bash
# only once no job or driver of that run is still active
rm -f still_reading
USPEX -r
```

`still_reading` is written while an instance is reading the state; removing it while a driver or
scheduler job is alive makes two processes fight over the same `*.mat` files.

### B. Pick-up restart from a chosen generation (`pickUpYN = 1`)

Use this when you deliberately want to re-launch from a named generation of a named `results`
folder, rather than continue the same state as in mode A. `USPEX --clean` is the normal
preparation for it, not a last resort: it clears the stale state and the done marker while keeping
every `results<N>/` folder, including your restart source.

```bash
ls -d results*            # note what is already present
cat results2/Warnings     # --clean deletes Warnings: read the failure record first
# only when no job or driver of this run is still active:
USPEX --clean
```

In the shipped 10.5 package `USPEX --clean` runs the installation's `clean` script:

```bash
rm -rf Calc* *.mat still* get* *POSCAR NOT_YET POSCAR_order log *_IS_DONE multiple_runs *backup PDB MAKE Warnings
```

It deletes the `*.mat` state (population, pool, antiseeds, current generation), the `CalcFold*`
working directories, `Warnings`, the root `POSCAR`/`log`, and `USPEX_IS_DONE`. It does **not**
touch `results<N>/`, so the source folder survives. Then add the restart block and launch:

```text
1 : pickUpYN        # required switch: without it the next two are ignored (default 0)
8 : pickUpGen       # generation to restart from; 0 means "start a new calculation"
2 : pickUpFolder    # source folder number: 1 = results1, 2 = results2, ...
```

```bash
USPEX -r            # run the first invocation in the foreground
```

The launch writes the new attempt into a **new** `results<N>` folder — with `results1`…`results4`
already present, that is `results5` — and leaves the source folder untouched. `pickUpFolder` keeps
naming the source folder, so it stays `2` after that. Before re-attaching the driver loop, check
the new folder's `OUTPUT.txt` for the generation numbering and its `Job Starts at` line, and its
`Individuals` for the generations actually present.

| Parameter | Meaning | Default |
|---|---|---|
| `pickUpYN` | enables a pick-up restart; **required** — `pickUpGen`/`pickUpFolder` alone do nothing | `0` |
| `pickUpGen` | generation to restart from; **`0` starts a new calculation** | `0` |
| `pickUpFolder` | `results` folder to restart from: `1` = `results1`, `2` = `results2`, ... | `0` |

All three default to `0`, so a plain `INPUT.txt` never restarts by accident. The packaged
10.5 template `test/templates/restart_GULP_301/INPUT_restart.txt` is the reference example —
its only difference from the neighboring `INPUT.txt` is the restart block:

```text
1    : pickUpYN
2    : pickUpGen
1    : pickUpFolder
```

The official 10.5 manual example — restart from generation 10 in `results5` — is
`10 : pickUpGen` plus `5 : pickUpFolder` with `pickUpYN = 1`.

### C. `USPEX --clean` without `pickUp`: a deliberate fresh start

`--clean` on its own discards the state, so the next launch begins a new search at generation 1 in
a new `results<N>` folder. That is a legitimate way to abandon a bad attempt while keeping all
earlier `results*/` folders for the record, but it is destructive: use it only when no job of that
run is still active and a rebuild is intended, and re-run the static preflight and one foreground
`USPEX -r` afterwards.

### When the run will not restart

A plain `USPEX -r` re-enters the completion path whenever `USPEX_IS_DONE` is present: it prints
`USPEX IS DONE!` and exits without doing any work — verified on 10.5.0 (08/07/2021), including when
`pickUp` scalars are present in `INPUT.txt`. That output means the done marker and stale state were
not cleared (the `--clean` step of mode B was skipped); it is not a scheduler or `INPUT.txt` fault.

### Restart checklist

1. confirm no other driver loop and no scheduler job of this run is still active;
2. record what is present: `USPEX_IS_DONE`, `OUTPUT.txt`, `results*/`, `still_reading`, `*.mat`;
3. confirm the source `results<f>/generation<g>/` is complete and that `results<f>` is this run;
4. inspect `results<f>/Warnings` and the root `POSCAR`/`log` **before** `--clean` deletes them;
5. pick the mode: A (continue in place → remove `still_reading`), B (`--clean`, then
   `pickUpYN = 1` with `pickUpGen`/`pickUpFolder`), C (`--clean` alone for a fresh start);
6. verify the executable release and `Submission/` scripts have not changed;
7. preserve the current `INPUT.txt`, `Specific/`, and any patch/model fingerprints;
8. run one foreground `USPEX -r`; `USPEX IS DONE!` means the done marker/state was not cleared;
9. confirm the new `results<N>` folder's generation numbering and contents, then re-attach the
   driver loop.

Moving a run is safest only when all engine jobs are terminal. Remote modes and
wrappers may embed absolute paths even if core USPEX state is movable.

## Recovery decision tree

```text
USPEX runner stopped
├─ scheduler jobs still active → restart only the driver; do not resubmit jobs
├─ jobs terminal, outputs valid → run USPEX -r once (removing still_reading) and verify ingestion
├─ jobs terminal, outputs missing → diagnose engine/submission; preserve failed dirs
├─ state/input mismatch → clone the run and repair with a documented migration
└─ want to re-launch from a named generation/folder, source generation complete
      → USPEX --clean, then pickUpYN=1 with pickUpGen/pickUpFolder, then one foreground USPEX -r
```

Never hand-delete `CalcFold*`, scheduler logs, or state files to force a stuck run forward while
another process may still be reading them. Decide first whether the structure is queued, running,
completed, failed, or already ingested; an intended re-launch goes through the `--clean` sequence
of mode B or C, never through ad-hoc `rm`.
