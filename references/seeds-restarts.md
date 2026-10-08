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

The `*.mat` state records the run's progress, so this needs no `INPUT.txt` change.
For a stalled invocation, follow **Branch A — stop the loop, preserve state, probe once** in
`references/liveness.md`: back up state/logs, stop the old loop and children, verify exit,
preserve/move actual stale markers, run one `USPEX -r` in the original environment, then
restore the original loop only after actual progress is confirmed.

Marker names depend on the installation. The user's current installation has `still_running`
and **no `still_reading`**. Older 10.5 documentation mentions `still_reading`; that historical
recipe does not mean it must exist everywhere. Do not create or wait for an absent marker.

```bash
# after backups and verification that the old loop, engine and helpers exited
# recovery_dir is the backup directory prepared in references/liveness.md
[ ! -e still_running ] || mv -- still_running "$recovery_dir/still_running.before-probe"
USPEX -r
```

If another installation actually creates `still_reading`, preserve and move that file only
after the same no-live-writer verification. Never clear a live instance's marker.

`still_running` is the **different** marker written when an invocation enters the MATLAB engine
phase and removed when it returns; its own text says it is present either because MATLAB is still
running or because MATLAB exited with an error. An invocation killed inside that phase leaves it
behind for good, so a stale copy is evidence to collect and then clear, not a lock and not proof
that the run is alive. Preserve and move this actual stale marker after writers exit.
Before choosing a branch at all, confirm the run really is dead rather than inside the long
silent phase every generation has — `references/liveness.md`, or
`python scripts/check_run_liveness.py RUN --sample-seconds 90`.

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

1. Establish the actual stall/stop from generation progress, logs, optimizer jobs and this run's
   cadence; manually check helper verdicts that count driver jobs or heartbeat I/O.
2. Preserve all root MAT state, logs, original input/environment/scripts and the latest completed
   generation before stopping; record the process tree and relevant scheduler job IDs.
3. Stop only the original loop and surviving children; verify exit on the execution node, then
   save a frozen state snapshot. Resolve outstanding optimizer jobs without duplicate submission.
4. Choose A (move actual stale markers aside, keep state), B (`--clean` plus `pickUp*`), or C
   (deliberate fresh start). B requires a verified completed source generation and preservation
   of `Warnings` before cleaning. `still_reading` is absent on the user's installation.
5. Use the original environment for one `USPEX -r`, capture output/exit status, and verify actual
   structure/generation progress before restoring the original loop. A uses the existing results
   folder; B/C deliberately creates a new attempt. If the probe fails or stalls again, keep the
   loop stopped and diagnose the captured failure. `USPEX IS DONE!` requires checking whether
   completion was real or a deliberate B/C restart omitted its clean step.

Moving a run is safest only when all engine jobs are terminal. Remote modes and
wrappers may embed absolute paths even if core USPEX state is movable.

## Recovery decision tree

```text
USPEX runner stopped
├─ engine process alive but artefact/IO frozen for many of this run's own generation cycles
│     → dead loop: read log/nohup/Warnings, report the cause, then branch A (state intact)
│       or branch B (state suspect / failure reproduces) — references/liveness.md
├─ scheduler jobs still active → restart only the driver; do not resubmit jobs
├─ jobs terminal, outputs valid → preserve/move actual stale markers, run USPEX -r once, verify ingestion
├─ jobs terminal, outputs missing → diagnose engine/submission; preserve failed dirs
├─ state/input mismatch → clone the run and repair with a documented migration
└─ want to re-launch from a named generation/folder, source generation complete
      → USPEX --clean, then pickUpYN=1 with pickUpGen/pickUpFolder, then one foreground USPEX -r
```

Never hand-delete `CalcFold*`, scheduler logs, or state files to force a stuck run forward while
another process may still be reading them. Decide first whether the structure is queued, running,
completed, failed, or already ingested; an intended re-launch goes through the `--clean` sequence
of mode B or C, never through ad-hoc `rm`.
