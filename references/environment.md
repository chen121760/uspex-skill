# Runtime environment contract

> Load this when: preparing a machine, after installing or moving a USPEX tree, when a run
> says the topology generator was skipped, when helper scripts error on imports, when USPEX
> exits immediately, or before delegating a run to a scheduler.

## Two interpreters, two jobs

A USPEX 10.5/10.6 search involves **two independent Python worlds**. Confusing them is the
most common environment failure.

| Role | Who runs it | Needs |
|---|---|---|
| **Launcher + helper scripts** (`USPEX`, `FunctionFolder/*.py`, `spacegroup/`, `Submission/*.py`) | the `python3` first on `PATH` when the user types `USPEX -r`; the same `PATH` is inherited by the MATLAB Runtime process and by every scheduler job | `numpy`, `scipy`, `spglib` — and a CPython **minor version matching one of the shipped `randomTopology.cpython-<tag>.so`** files |
| **MATLAB Runtime** | `MCRROOT/v91` (R2016b), started by the launcher | only `MCRROOT` and the `LD_LIBRARY_PATH` the launcher sets; no Python |

An external optimizer or ML potential (code `99`, a MatterSim/Prophet-like stage, a 10.6
`user_property_calculator.py`) is a **third** world: give it its own environment and call it
by absolute interpreter path from `commandExecutable`. Do not install torch/an ML stack into
the USPEX helper interpreter.

## Supported interpreter versions

Two packaging schemes exist, and the shipped module is the authority in both:

| Release | Module in `FunctionFolder/` | CPython |
|---|---|---|
| 10.5.0 (08/07/2021) | `randomTopology.cpython-36m/37m/38/39-x86_64-linux-gnu.so` | **3.6–3.9 only** — a hard minor-version pin, no 3.10+ build |
| 10.6 update (14/05/2026) | a single `randomTopology.abi3.so` | **no minor pin** (stable ABI, PEP 384); verified importing on 3.9, 3.10, 3.11 and 3.13 |

Verify for the exact tree in front of you rather than trusting this table:

```bash
ls <install>/application/archive/src/FunctionFolder/randomTopology.*.so
```

Use an interpreter whose major.minor matches a shipped tag, or any interpreter that can load the
stable-ABI module. A conda-forge or distro build of the same minor version is fine; the ABI, not
the vendor, is what matters. Dependencies still have to be present: `numpy`, `scipy`, `spglib`.

Two traps specific to the 10.6 update:

- it `mv`s the new module *into* `FunctionFolder/` and leaves the old version-tagged files
  behind. Python prefers `randomTopology.cpython-<tag>.so` over `randomTopology.abi3.so`, so on
  3.6–3.9 you keep loading the older build; remove or rename the stale tagged files if you mean
  to run the stable-ABI one;
- the module's `__init__` imports its sibling `latConverter.py`, so testing the `.so` alone in a
  temporary directory reports `No module named 'latConverter'` — that is not a version problem.

## Symptom → cause

| Symptom | Cause |
|---|---|
| `Unable to import numpy and scipy, Random Topology will be skipped!` then `ModuleNotFoundError: No module named 'numpy'` or `ImportError: randomTopology` | the `python3` USPEX uses lacks numpy/scipy, or its version has no shipped `.so` |
| `ModuleNotFoundError: No module named 'spglib'` inside `spacegroup/spacegroup.py` | helper interpreter lacks spglib (used for `SYMMETRY` reporting) |
| USPEX prints the banner and exits almost immediately with status 0 after doing one batch of work | normal driver behaviour, not a crash — see the run loop below |
| `USPEX: command not found` or `USPEXPATH is not set` | environment variables not exported in **this** shell |
| A run silently used the wrong release | another `USPEX` earlier on `PATH`, or `USPEXPATH` pointing at a different tree than the launcher |

## Health check before every real run

```bash
python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.5
```

It verifies `MCRROOT`/`USPEXPATH`, that the `USPEX` on `PATH` resolves to the tree
`USPEXPATH` points at, the interpreter version against the shipped `.so` tags, the
numpy/scipy/spglib imports, that the compiled topology module actually imports, the scheduler
contract implied by `whichCluster`, and then delegates the static file checks to
`preflight_uspex.py`. Every finding states a remedy. Exit code 2 = at least one ERROR.

Run it with the interpreter that will drive USPEX, or pass `--python`.

## Environment variables

```bash
export MCRROOT=/path/to/install        # the directory that contains v91/
export USPEXPATH=$MCRROOT/application/archive/src
export PATH=$MCRROOT/application/archive:$PATH
```

Prefer a **separate activation script per installation** (`<install>/activate.sh`) over
appending to `~/.bashrc` when more than one release must coexist: sourcing the script is
reversible and cannot silently switch an existing project to another build. Record which
installation a run used in `Parameters.txt`/`OUTPUT.txt` and in the project README.

## The USPEX run loop

`USPEX -r` performs **one batch of work and returns**. It submits or launches the pending
stage jobs, ingests whatever finished, writes results, and exits 0. A complete search is
produced by running it repeatedly until the run directory contains `USPEX_IS_DONE`:

```bash
while [ ! -f ./USPEX_IS_DONE ]; do
  date >> driver.log
  USPEX -r >> driver.log 2>&1
  echo "cycle exit=$?" >> driver.log
  sleep 10
done
```

Implications:

- one foreground `USPEX -r` is still the correct first smoke test — it proves parsing,
  submission, status checking and result ingestion — but it will not "finish" the search;
- never interpret a single exit 0 as success: check `USPEX_IS_DONE`, `results*/Individuals`,
  `USPEX.mat` and the scheduler state;
- the loop must outlive the SSH session (`nohup`/`setsid`/`tmux`/`screen`), and the
  environment from the activation script must be part of the loop's environment, not of the
  interactive shell only;
- if the user has no driver script, write one (see `assets/run_uspex_loop.sh`) and start it
  only after the foreground pass established that the pipeline works.
