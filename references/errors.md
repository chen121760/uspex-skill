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
| USPEX advances before engine completes | status callback interprets temporary scheduler error as terminal | compare timestamps, queue, accounting | distinguish command failure from “job absent”; retry transient errors |
| Job leaves queue, result is absent | engine crash, walltime, OOM, bad module/launcher | scheduler accounting and engine stdout/stderr | fix resource or engine input; never synthesize a result |
| Code-99 structure repeats forever | `geom.out` or `energy.txt` missing/malformed; `userCalcFitness.py` flag 0 never reports done | atom count/cell, finite energy, callback value, stale files | make every stage write fresh geometry and energy atomically; test flags 0 and 1 directly |
| Custom property is always zero/constant | wrong `P1`...`P7` file/order; silent default; stale file | run `calcElasticML.py` on two known structures and inspect all callback positions | make parsing strict, values finite, and any penalty sentinel explicit and direction-aware |
| 10.5 `1201` shows bulk modulus instead of custom property | stock `calcElasticML.py` is active or the wrong installation is on `PATH` | executable path/checksum and `MLProperties` | use an isolated patched build or migrate to 10.6 `x`/`y` |
| Custom property looks sign-inverted in `Individuals` | normal convention: that column shows the negated copy so all objectives share one orientation | compare `P1.txt`/`MLProperties` with the `Individuals` column | keep writing the raw larger-is-better value; never negate it as a "fix" |
| Pareto front ignores the custom objective | switch generation at `1` (driver defect), property file missing for some individuals, or a fabricated/constant value | `ParetoRankingInGeneration`, `MLProperties`, per-`CalcFold*` property files | use `[N]` with `N >= 2`; make every stage write a fresh finite value |
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
