---
name: uspex
description: Design, prepare, validate, run, restart, troubleshoot, and analyze USPEX 10.5/10.6 crystal-structure searches. Use for 3D bulk and 2D searches, fixed or variable composition including binary/ternary/quaternary 3D systems, surface reconstruction, VASP or code-99 relaxation stages, seeds, Slurm/PBS callbacks, repeated USPEX -r execution, native USPEX 10.6 x/y custom objectives through user_property_calculator.py, or migration of a documented USPEX 10.5 custom interface.
---

# USPEX 10.5/10.6

Treat the installed USPEX release as part of the input. Version 10.5 and 10.6
share most search machinery but have different supported routes for arbitrary
properties and external optimizers.

## Required inputs

- Exact USPEX release from its startup banner, installation record, or executable.
- Search class: 3D bulk, 2D crystal, or surface reconstruction.
- Fixed composition or variable-composition blocks; elements and atom/block bounds.
- Fitness direction for every objective and, for staged Pareto runs, the switch generation.
- Relaxation chain: VASP stages and/or a code-99 external optimizer/property stage.
- Scheduler, queue/partition, resource request, launch command, and site environment setup.
  Establish the queue from the machine itself (`scripts/check_schedulers.py`), never from a
  tutorial, another cluster, or a copied example: queue names and limits are site-specific.
- The interpreter that will run the USPEX helpers (`PATH` when `USPEX -r` starts) and, for
  code-99 or property stages, the separate interpreter/command that runs the external engine.

If a value is unknown, expose it as a named assumption. Do not silently copy it
from a chemically unrelated example.

## Situation index

| Situation | Load |
|---|---|
| Prepare a machine, install/move a release, or debug imports, skipped random topology, helper `ModuleNotFoundError`, or an immediate exit | `references/environment.md` |
| Choose calculation type; build `INPUT.txt`; map binary/ternary/quaternary blocks; configure VASP stages | `references/running.md` |
| Look up a keyword's default, units, or legal values before writing `INPUT.txt`; explain why a run is shorter or costlier than expected | `references/input-parameters.md` |
| Add arbitrary objectives, connect MatterSim or another external optimizer, define the code-99 file contract, or migrate a 10.5 custom property | `references/custom-objectives.md` |
| Build or debug Slurm/PBS `Submission/`; find out which queues/partitions exist and which your account may use; read or update the cluster's `~/.cluster-agents.md`; run a persistent `USPEX -r` loop | `scripts/check_schedulers.py`; `references/submission.md`; `references/resources.md` |
| Add seeds, seed selected generations, restart, move, or recover a run | `references/seeds-restarts.md` |
| Audit before launch; analyze convex hulls, Pareto fronts, surfaces, and VASP results | `references/validation-analysis.md` |
| Decide whether a run is finished, healthy, stalled or broken, and hand its results on | `scripts/uspex_status.py`; `references/validation-analysis.md` |
| Decide whether a quiet run is dead or still inside a long structure-generation phase (pinned core, frozen log, `still_running` left behind), report the likely cause, and choose the recovery | `scripts/check_run_liveness.py`; `references/liveness.md` |
| Package the structures that satisfy a user's condition out of a finished or running search | `scripts/extract_uspex_candidates.py`; `references/validation-analysis.md` |
| Diagnose stalled structures, missing output, scheduler loops, or malformed objectives | `references/errors.md` |
| Locate official manuals, examples, release notes, and companion tools | `references/resources.md` |

## Workflow

1. Pin the release. Never infer 10.6 capability from a 10.5-looking `INPUT.txt`.
2. Establish the runtime *before* designing the chemistry: set `MCRROOT`/`USPEXPATH`,
   put the helper interpreter first on `PATH`, and run
   `python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.5|10.6`.
   It must confirm that the `USPEX` on `PATH` is the tree `USPEXPATH` describes, that the
   interpreter's version matches a shipped `randomTopology.cpython-<tag>.so`, and that
   numpy/scipy/spglib import. A mismatch here degrades every later result silently.
3. Establish the queue before designing the run. Read the cluster's own guide first
   (`~/.cluster-agents.md`; `python scripts/check_schedulers.py --facts` parses it) — it is the
   recorded, tested answer and outranks both the MOTD and any probe. Then verify against the live
   scheduler: `python scripts/check_schedulers.py --workdir RUN_DIR` reports what Slurm partitions
   or PBS queues exist, which are available, their limits, whether the run's `Submission/` still
   holds a template placeholder, and whether the run's account is admitted by `acl_users`. Pick
   the queue from that output (a dry run such as `sbatch --test-only` confirms access), never from
   a tutorial or a copied example. If `Submission/` is missing or incomplete, the same command
   first searches (`--search-root DIR`, default: the run's project directory) for a
   submit-callback pair that already works on this machine and reports the queue name inside it;
   reuse that pair. If no pair exists anywhere and the guide is silent, **ask the user** for the
   scheduler, queue/account, and module preamble instead of inventing them, then record the
   verified facts with `--facts-out` so the next session starts from the guide.
4. Start from the closest example shipped with that executable: run `USPEX -e`,
   inspect the description, then copy with `USPEX -c NUM` in a clean directory.
5. Design the composition space, objective directions, relaxation stages, and
   scheduler contract before editing files.
   Check every default and legal value against the installed tree's own parameter
   dictionary, as summarized in `references/input-parameters.md`, instead of assuming
   the numbers of a copied example.
   For a custom objective on stock 10.5, first create and validate an isolated
   patched installation; changing `INPUT.txt` alone cannot repurpose `1201`.
   Express every objective direction explicitly: name, units, larger/smaller-is-better, the sign
   actually written into the property file, and the output file that carries it. A
   larger-is-better property is written as the raw positive value.
6. Keep the standard layout: `INPUT.txt`, `Specific/`, `Submission/`, and optional
   `Seeds/`; include `POSCAR_SUBSTRATE` only for surface modes.
7. Run `python scripts/preflight_uspex.py RUN_DIR --uspex-version 10.5|10.6`.
   Resolve every error, review every warning, and read the informational findings about
   database/topology operators and scheduler queue depth. Use
   `scripts/package_uspex_run.py` when moving a run: it preflights first and packages the
   INPUT, runtime helpers, a checksum manifest, and the complete environment checks together.
8. On 10.6, keep the relaxation and objective interfaces distinct. `abinitioCode` and
   `commandExecutable` define the relaxation stages; `Specific/user_property_calculator.py`
   returns completion/enthalpy for code 99 and returns custom `x`/`y` values for flags `101`/`102`.
   The callback may calculate `x` directly from the final relaxed structure or parse a
   project-specific intermediate. USPEX does not require `P1.txt`.
   Choose the number of stages from the intended relaxation chain; multiple stages are legal,
   and every stage must have the matching command/input contract. See
   `references/custom-objectives.md`.
   Then test one deliberately mixed-composition structure when the custom property depends on
   more than one species. Use `scripts/smoke_code99.py` to run the external command and
   verify the declared engine outputs plus callback flags `0`, `1`, `101`, and optionally `102`.
   Pass `--penalty` for every documented penalty sentinel so a smoke run cannot pass green
   while the real property path was never exercised, and `--consume-geom-out` when the callback
   uses a preserved copy of a code-99 relaxed geometry.
   Then test one scheduler callback path with a reduced throwaway USPEX input
   (`numParallelCalcs = 1`, tiny population). Do not use the full production initial
   population as the foreground smoke test.
9. Understand the run loop before starting a search: the run only finishes when the
   directory contains `USPEX_IS_DONE`, so `USPEX -r` must be repeated. Start
   `assets/run_uspex_loop.sh` (or write an equivalent driver if the site has none) for the
   production directory only after the reduced foreground pass established INPUT parsing,
   job submission, status checking, and result extraction; keep it detached from the SSH
   session. A large `initialPopSize` can spend minutes generating candidates before the first
   scheduler job appears, so judge startup from the durable driver log rather than an idle queue
   snapshot.
   A resumed run uses the same loop: first prove that no job or driver of that run is
   still active, then continue in place (remove `still_reading`) or, after `USPEX --clean`,
   re-launch from a completed generation of a chosen `results` folder with `pickUpYN = 1`
   plus the `pickUpGen`/`pickUpFolder` scalars —?see `references/seeds-restarts.md`.
10. Judge a running search from artefacts, not from an exit status or a queue snapshot:
   `python scripts/uspex_status.py RUN` returns 0 done, 1 running, 2 intervene
   (stalled/failed), 3 unknown, and reports generations completed, structures finished,
   sentinel energies, jobs attributed to this directory by workdir, and whether a second
   engine or driver is active. Treat a missing artefact as missing evidence, never as zero.
   Results bundles often omit `USPEX_IS_DONE`; completion is then inferred from `OUTPUT.txt`.
   Its staleness threshold is a fixed constant, so before acting on `stalled` for a search whose
   generation cycle is hours long, run
   `python scripts/check_run_liveness.py RUN --sample-seconds 90`: it measures this run's **own**
   generation cadence, samples the engine's I/O, and separates a dead loop from the long silent
   MATLAB phase every generation contains. A pinned core, an idle `matlab_helper`, a leftover
   `still_running`, and a queue with no job of this run are all normal mid-cycle; a dead run is
   condemned by silence measured against its own cadence with no file **and** no I/O movement.
   Then report the likely cause to the user and choose a recovery branch before killing anything —
   `references/liveness.md`.
11. Package what the user asks for instead of exporting everything:
    `python scripts/extract_uspex_candidates.py RESULTS_DIR --select 'CONDITION' --out DIR`
    selects by geometry (composition, atom count, space group, volume, lattice), by table
    values (Pareto front, enthalpy per atom, hull distance, objective, origin) or by
    convergence, and writes a CSV plus a manifest that records which file supplied each
    field. Run `--list-fields` first; `--dry-run` counts matches without writing. A
    condition on `Individuals` must name a generation, because its ids repeat per generation.
12. Validate the search outputs and independently reconverge shortlisted structures.

## Hard guardrails

- Do not commit or reproduce POTCAR files, copyrighted manuals, private source
  patches, user paths, hostnames, credentials, or bulky calculation output.
- The helper interpreter is part of the experiment. A `python3` whose version has no
  shipped `randomTopology.cpython-*.so`, or that lacks numpy/scipy/spglib, silently turns
  off the topology generator or breaks `SYMMETRY` reporting; treat that as a failed
  environment, not a cosmetic warning.
- The variation-operator mix is a scientific choice. Explicit `frac*` values need not sum to
  1 —?USPEX normalises them, so the ratio is what counts —?and `AutoFrac = 1` overrides that
  ratio with a mix USPEX evolves itself. `symmetries` takes a different group family per
  dimensionality (3D `2-230`, 2D layer groups `2-80`, surface plane groups `2-17`); never
  reuse one range for another dimensionality. See `references/input-parameters.md`.
- A single `USPEX -r` is one batch of work, not a completed search. Completion is
  `USPEX_IS_DONE`; never report a search as finished from an exit status alone, and never
  run the driver loop from a shell that an SSH disconnect will kill.
- A busy engine is not evidence of progress. The evolutionary algorithm runs *inside* `uspex.x`
  (embedded MATLAB runtime), so one core at 100 %, an idle `matlab_helper`, a leftover
  `still_running`, and a queue holding no job of this run are all normal between artefacts, and the
  driver log is both truncated per invocation and block-buffered. Decide liveness against the run's
  own generation cadence plus file/I/O movement (`scripts/check_run_liveness.py`), never against a
  fixed staleness constant, and never kill or restart a run before proving no engine, driver,
  helper or scheduler job of that run is still alive. See `references/liveness.md`.
- A larger-is-better custom property is returned as its raw measured value; the `Individuals`
  column shows its negated copy so that every objective shares one internal orientation, and
  the ranking is derived from that copy. Judge the convention from `optType`, the callback's
  independently checked raw value, and `ParetoRankingInGeneration`; never negate a
  larger-is-better value to "fix" an apparent inversion. Do not join per-generation output
  files by row or ID when attributing values to structures.
- The USPEX 10.5 custom multi-objective method in this skill is a clean-room
  interface description. Never copy, emit, or reconstruct the private teaching
  files or their implementation verbatim.
- Prefer native USPEX 10.6 `x`/`y` properties and official code `99`. Use the 10.5
  patch pattern only when the installed 10.5 environment must be preserved.
- In 10.6, stage count belongs to the relaxation design, not to the custom-property
  interface. The installed EX38 demonstrates a mixed multi-stage chain ending in code 99.
  `optType` needs an explicit direction prefix (`1 max_x`/`1 min_x`) on the tested 10.6
  build. Return `x`/`y` through the callback; do not assume that an earlier stage's private
  files survive into a later stage unless the installed release and adapter explicitly define
  that transfer.
- In 10.5, `optType` values `1201`-`1207` have official elastic-property meaning.
  Reusing one as a private channel requires an isolated, documented installation;
  never apply that convention to an unpatched executable.
- A code-99 stage is an explicit file contract. On native 10.6, `geom.out` and
  `energy.txt` are the usual relaxed-structure/enthalpy outputs for an external optimizer;
  custom `x`/`y` values come from `Specific/user_property_calculator.py`. A private
  intermediate is optional and project-defined. CalcFold directories may be reused, so
  private intermediates must be overwritten atomically or tied to the current `geom.in`
  with a digest/manifest. Do not blanket-delete USPEX-managed outputs; let USPEX manage its
  files and make only the producer-owned contract unambiguous. Expected scientifically
  invalid candidates may use documented direction-aware penalties, but infrastructure
  failures must never become implicit zeros or apparent success.
- Scheduler disappearance means only that the scheduler stopped tracking the job;
  it does not prove scientific success. Check the engine outputs.
- Never rank unconverged VASP structures or mix energies computed with inconsistent
  functionals, potentials, cutoffs, k-point policies, or pressures.
- Before submitting paid or shared-cluster work, present the run size, resources,
  and exact submit command for approval.
- Never invent scheduler configuration. Before probing anything else, read the cluster's own
  operating guide (`~/.cluster-agents.md` on the machine you are logged into — the
  `hpc-submit`/AICC convention, whose canonical template that skill owns). If it records the
  queue, account, and job-script pattern, use them. If `Submission/` has no usable
  submit/status pair, search for one that already works on this machine and reuse it; if none
  exists, ask the user for the scheduler, queue/account, module preamble, and any site-supplied
  pair. Then record the verified facts back into the guide with
  `python scripts/check_schedulers.py --facts-out FILE` so nobody is asked twice. Guessing a
  queue name, account, or scheduler directive is a failure mode, not a shortcut.
- With a scheduler, `numParallelCalcs` is the number of structure calculations USPEX may
  keep in flight, including queued jobs. It need not equal the number of physical GPUs or
  nodes, although the queue/account limits and per-job resource request still bound actual
  concurrency.
