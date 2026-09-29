# Error map

> Load this when: USPEX stalls, the scheduler callback loops, an engine stage produces no result, or rankings look scientifically wrong.

## Start here

```bash
python scripts/uspex_status.py RUN --stale-minutes 30
```

It returns a verdict (`done`, `running`, `stalled`, `failed`, `unknown`), counts sentinel
energies and missing per-structure outputs, attributes scheduler jobs to this directory by
workdir, and reports concurrent engine/driver processes. Use the table below when the verdict
says to intervene, or when the numbers look scientifically wrong.

`--stale-minutes` is a fixed constant, and a search whose generation cycle takes hours is legitimately
silent for much longer than any constant. Before treating `stalled` as a death certificate:

```bash
python scripts/check_run_liveness.py RUN --sample-seconds 90
```

It measures the run's own generation cadence from `results*/generation*` mtimes, samples the engine's
`rchar`/`wchar` and CPU, and returns `done` / `working` / `stuck` / `stopped` / `unknown` with the
next actions. The reasoning, the false-positive traps (pinned core, `still_running`, buffered log),
the interpretations to report, and the recovery branches are in `references/liveness.md`.

| Symptom | Likely cause | Checks | Fix |
|---|---|---|---|
| `Unable to import numpy and scipy, Random Topology will be skipped!`, `ModuleNotFoundError: No module named 'numpy'`, or `ImportError: randomTopology` | the `python3` on `PATH` is not the intended helper interpreter, has no shipped `.so` for its version, or lacks numpy/scipy | `python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.5`; `ls FunctionFolder/randomTopology.cpython-*.so` | select an interpreter matching a shipped tag (10.5.0 ships 3.6–3.9) and install numpy/scipy/spglib into it; re-run the pilot before trusting topology-based structures |
| `ModuleNotFoundError: No module named 'spglib'` in `spacegroup/spacegroup.py` | helper interpreter lacks spglib | env check, then `python3 -c "import spglib"` | install spglib into the helper interpreter; `SYMMETRY` columns are otherwise lost |
| First `USPEX -r` prints the banner and exits 0 after one batch | normal driver behaviour: one invocation is one batch, not a finished search | `USPEX_IS_DONE`, `results*/Individuals`, scheduler state | repeat `USPEX -r` (driver loop) until `USPEX_IS_DONE` exists; never equate exit 0 with completion |
| `Undefined function or variable 'multiFit'` in `extractStructures` at the end of generation 1 | staged Pareto switch generation set to `1` (second objective active inside generation 1) — a driver defect that also reproduces on the stock ML channel | `optType` block in `Parameters.txt`, generation number in `OUTPUT.txt` | use `[N]` with `N >= 2` and `numGenerations >= 2`; confirm the property is still recorded in the pre-switch generations |
| INPUT parse fails immediately | 10.6-only keyword on 10.5; malformed `% ... % End...` block | executable banner, `Parameters.txt`, copied example | restore release-matched syntax and block endings |
| `numSpecies` rejected or compositions nonsensical | row width/order does not match `atomType`; block basis misunderstood | expand every row to a chemical formula | repair matrix and bounds; rerun preflight |
| No jobs appear | wrong `whichCluster`; callback not executable/importable; scheduler command unavailable | run callback help/import, inspect stderr | adapt the correct local/remote pair and environment |
| `Submission/` is missing or only half-filled, so nothing can be submitted | the run was copied without its callbacks, or only one of the pair was carried over | `python scripts/check_schedulers.py --workdir RUN --search-root DIR` (reports state `missing`/`empty`/`pair_incomplete`/`template` and lists `submitJob_local.py` pairs found on the machine, with the queue name each uses) | copy a working pair from a neighbouring run and adjust it; if none exists on the machine, ask the user for scheduler/queue/account/module preamble rather than inventing them |
| The cluster guide (`~/.cluster-agents.md`) exists but has no queue recorded | the site facts were never captured, or the guide is a fresh template | `python scripts/check_schedulers.py --facts ~/.cluster-agents.md` reports `guide-incomplete` and stops there | ask the user for the queue/account, verify it with the discovery helper, then record it in the guide with `--facts-out` so it is not asked again |
| "No scheduler found" / queue name unknown / `sbatch: error: Invalid partition name specified` / `qsub: Unknown queue` | the queue was guessed, or the client needs a module, or the account lacks access to that queue | `python scripts/check_schedulers.py --facts ~/.cluster-agents.md --workdir RUN --test-submit`; `sinfo -s` / `qstat -Q`; `module avail` | take the partition/queue name from the cluster guide if recorded, else from the discovery output; confirm with the dry run, then write it into `Submission/submitJob_local.py`; never copy a queue name between sites |
| Jobs queue forever (`PENDING`, reason `Resources`/`QOSMaxJobsPerUser`/`AssocGrpNodeLimit`; PBS `Q`/`H`/`W`) | request exceeds queue limits, account over quota, or `numParallelCalcs` over-subscribes the fairshare window | `squeue -u $USER -o "%.10i %.9P %.8T %.10r %.10M"`, `sacctmgr show assoc`; `qstat -f JOBID`, `qstat -q`, `showq -u $USER` | lower nodes/tasks/GPUs/walltime or `numParallelCalcs`, or move to a queue whose limits fit; re-derive the request from `sinfo`/`qstat -q` |
| Jobs submit but job ID is missing | brittle `sbatch`/`qsub` parsing | capture exact submit stdout/stderr | use regex/parser matching the site's real output |
| USPEX waits forever after job ends | terminal state treated as active; stale ID; callback crashed | call `checkStatus` manually, inspect queue/accounting | classify states explicitly and always emit callback marker/value |
| Engine at 100 % CPU for hours, `log` frozen, `uspex_status.py` says `stalled` | normal long MATLAB phase (ranking + evolving the next generation, measured at ~21 min of complete file silence) versus a real dead loop — silence alone cannot separate them | `python scripts/check_run_liveness.py RUN --sample-seconds 90`; compare the newest artefact's age with this run's own `results*/generation*` cadence; read `/proc/PID/io` twice | if artefacts and I/O move, leave it alone and re-check around the cadence; only if I/O is frozen past ~3 cadences with no job of this run is it a dead loop → `references/liveness.md` |
| `still_running` present, but no engine/driver/helper of that run is alive | stale marker from an invocation that was killed or died inside the MATLAB engine phase — it is written on entry and removed on return, so it is not a hang indicator | process list filtered by cwd (`check_run_liveness.py` prints it), scheduler, driver log | clear it only in the continue-in-place branch (`rm -f still_running still_reading`) after the no-live-writer proof, then one foreground `USPEX -r`; see `references/liveness.md` and `references/seeds-restarts.md` |
| `MATLAB:badsubscript` with a trace through `SoftModeMutation_*` → `EA_*` → `Start` → `USPEX` in `nohup.out`/driver log, engine then spins forever | the MATLAB side died on a soft-mode mutation of a structure it could not handle; the engine waits for a reply that never arrives | the trace, `Warnings`, the helper's stderr, `still_running`, `/proc/PID/io` frozen | preserve evidence, report the interpretation, then branch A (continue in place); if it reproduces on the same operator, branch B (`USPEX --clean` + `pickUpYN`/`pickUpGen`/`pickUpFolder`) from a generation before the offending structure |
| Driver log ends with no new invocation while its jobs are already terminal | the driver exited between invocations (wrapper non-zero exit, SSH death, missing loop) | driver log tail (`exit=`), process list, `STOP_USPEX_LOOP` | fix the wrapper and re-attach the loop; this is a restart, not a kill |
| USPEX advances before engine completes | status callback interprets temporary scheduler error as terminal | compare timestamps, queue, accounting | distinguish command failure from “job absent”; retry transient errors |
| Job leaves queue, result is absent | engine crash, walltime, OOM, bad module/launcher | scheduler accounting and engine stdout/stderr | fix resource or engine input; never synthesize a result |
| Code-99 structure repeats forever | relaxed geometry or energy missing/malformed; flag 0 never reports done; a reused CalcFold exposes a private artifact from an older structure | call flags 0/1 directly; compare current `geom.in` digest with the producer-owned manifest; inspect engine log and finite energy | atomically publish the external optimizer's outputs and current-input manifest; let USPEX manage its own files |
| 10.6 custom property is zero/constant | flag 101 returns a default, evaluates the unrelaxed input, or reads a private intermediate with no current-structure provenance | call flag 101 on two known relaxed structures and recompute the property independently | calculate `x` directly in the callback or validate a project-owned intermediate; return a finite raw value and use a documented penalty only for model-domain invalidity |
| 10.5 `1201` shows bulk modulus instead of custom property | stock `calcElasticML.py` is active or the wrong installation is on `PATH` | executable path/checksum and `MLProperties` | use an isolated patched build or migrate to 10.6 `x`/`y` |
| Custom property looks sign-inverted in `Individuals` | normal convention: that column shows the internally negated copy so all objectives share one orientation | compare `optType`, an independent raw callback value, and the ranking | keep returning the raw larger-is-better value; never negate it as a “fix” |
| Pareto front ignores the custom objective | switch generation at `1` (driver defect), flag 101 failed for some individuals, or a fabricated/constant value | `ParetoRankingInGeneration`, direct callback tests, independent property recomputation | use `[N]` with `N >= 2` when staging objectives; make flag 101 return the current finite raw value |
| `INCAR_i` not found | stage index does not match `abinitioCode` position | enumerate stages and `Specific/` files | rename/add exact stage file; do not reuse blindly |
| VASP energy outliers dominate hull | unconverged/mismatched settings or wrong normalization | OUTCAR convergence, POTCAR/ENCUT/k mesh, atoms/formula | reconverge consistently and rebuild hull |
| Seeds ignored | wrong filename/generation, invalid POSCAR, composition outside bounds | `Seeds_history`, `Warnings`, species/counts | use the installed example's naming and valid block basis |
| Surface energies are incomparable | raw slab energies used across stoichiometries; substrate/reference mismatch | EX19/EX23 method and phase-diagram inputs | use consistent chemical potentials/reference energies |
| 2D structures collapse or interact across vacuum | wrong calc type, insufficient vacuum, 3D relaxation constraints | periodic visualization and final cell | correct `-200/-201`, vacuum, and relaxation settings |

## Escalation ladder

1. Freeze new submissions; preserve the failing structure directory and logs.
2. Reproduce with one structure and `numParallelCalcs = 1`.
3. Run the engine command manually in that structure directory.
4. Run submit/status callbacks manually and compare their values with scheduler truth.
5. Check the release's official example for the same calculation type/code.
6. Diff only inputs and adapters, changing one variable at a time.
7. Resume the original run only after the one-structure contract passes end to end.
