# uspex-skill

An agent skill for **USPEX 10.5 / 10.6** crystal-structure searches: preparing a machine,
authoring `INPUT.txt`, finding a queue, wiring `Submission/`, running the search loop,
restarting it safely, and analysing what came out.

## What it covers

| Area | What the skill gives you |
|---|---|
| Runtime | Two-interpreter model (launcher vs. MATLAB Runtime vs. external engine), the shipped `randomTopology.cpython-*.so` ABI pin, `MCRROOT`/`USPEXPATH`, and a checker that fails loudly instead of degrading silently |
| Input authoring | `calculationType` matrix, composition blocks for binary → quaternary and pseudobinary spaces, `optType` staging, VASP stage chain, and a per-keyword default table checked against the installed release |
| Schedulers | Slurm/PBS queue discovery (including which queues your account is actually admitted to), a cluster-guide lookup, queue dry runs, and site-neutral `Submission/` callback templates |
| Running | Why one `USPEX -r` is not a finished search, `USPEX_IS_DONE`, a detachable driver loop, and concurrency accounting that matches what the scheduler really tracks |
| Restarts | Seeds, seed-selected generations, `--clean` + `pickUpYN`/`pickUpGen`/`pickUpFolder`, and how to prove no writer is still active |
| Liveness | Cadence-based triage of a quiet run — dead loop versus the long silent MATLAB phase every generation contains — plus the `still_running`/`still_reading` distinction, the interpretation to report, and the three recovery branches |
| Custom objectives | Native 10.6 `x`/`y` properties and code `99` — one stage, one wrapper, callback read-back — and why the 10.5 two-stage `geom.out`/`energy.txt` handshake must not be reproduced on 10.6; the direction/sign convention (raw value written, negated copy displayed); the isolated 10.5 pattern for installations that must be preserved |
| Legacy 10.5 custom properties | The patched two-file interface (`userCalcFitness.py` + `calcElasticML.py`), the `geom.in`/`geom.out`/`energy.txt` lifecycle, `P1.txt`…`P7.txt` as generic property slots, the verified `1201`–`1207` mapping evidence and what is still unconfirmed, a minimal-patch protocol, a layered validation ladder, a provenance record, and the migration to native 10.6 `x`/`y` |
| Analysis | Reading USPEX's real output files, per-file energy normalisation, failure sentinels, convex hulls, Pareto fronts, 2D/surface caveats, and a selection tool that packages only the structures a user asked for |

## Layout

```text
SKILL.md                     entry point: required inputs, situation index, workflow, guardrails
references/                  progressive disclosure — load only what the task needs
  environment.md             runtime contract, interpreter/ABI traps, the run loop
  running.md                 calculation types, composition blocks, VASP stages, operators
  input-parameters.md        keyword defaults and legal values, verified per release
  custom-objectives.md       x/y properties, code 99, the 10.6 code-99 contract
  custom-objectives-105.md   legacy 10.5 patched custom-property interface: patch, P1-P7, 1201-1207, validation, provenance
  submission.md              queue discovery, Submission/ callbacks, cluster guide
  seeds-restarts.md          seeds, restarts, move/recover
  liveness.md                dead run versus a long silent phase, and the recovery branches
  validation-analysis.md     preflight, hulls, Pareto fronts, structure packaging
  errors.md                  symptom → cause → fix
  resources.md               manuals, examples, companion tools, the cluster-guide convention
scripts/                     checkers and tools, all runnable standalone
assets/                      neutral Slurm/PBS Submission pairs and the run-loop driver
assets/legacy105/            copy-paste templates for the patched 10.5 custom-property interface
examples/                    how to pick the right shipped USPEX example
```

Everything under `references/` is loaded on demand, so a simple question does not pull in the
whole skill.

## Tools

All scripts are standard-library only (Python 3.9+), read-only unless stated, and usable
without an agent.

| Script | Purpose |
|---|---|
| `check_uspex_env.py` | Verify the installation: launcher vs. `USPEXPATH`, interpreter vs. shipped topology module, numpy/scipy/spglib, scheduler contract |
| `check_schedulers.py` | Discover queues/partitions and their limits, resolve per-account ACLs, read a cluster guide (`~/.cluster-agents.md`), find an existing `Submission/` pair, and dry-run a request (Slurm `sbatch --test-only`) |
| `preflight_uspex.py` | Static audit of a run directory before launch: blocks, stages, POTCAR/INCAR coverage, objectives, scheduler request |
| `smoke_code99.py` | Exercise an external code-99 adapter: required fresh outputs, callback flags `0`/`1`/`101`/`102`, documented penalty sentinels, and the `geom.out` consumption hazard |
| `smoke_legacy105_custom.py` | Exercise a USPEX 10.5 legacy property adapter on mock `P1.txt`…`P7.txt` slot files: the fixed-order seven-value `<CALLRESULT>` contract, and every misconfiguration that must fail loudly instead of becoming an implicit zero |
| `uspex_status.py` | Judge a run from artefacts — done / running / intervene / unknown — attributing scheduler jobs by workdir and detecting a second writer |
| `check_run_liveness.py` | Separate a dead loop from a long silent phase: measures the run's own generation cadence, samples engine I/O and CPU, and prints the evidence, the likely cause to report, and the next actions |
| `extract_uspex_candidates.py` | Select structures by condition (composition, atom count, space group, volume, Pareto front, enthalpy/atom, hull distance, origin, convergence) into CSV + POSCAR with a per-field provenance manifest |
| `package_uspex_run.py` | Preflight, then package a clean run (inputs, callbacks, driver, checkers) with a checksum manifest |

`probe_queues.sh` is a POSIX-shell equivalent of the queue probe for machines whose only
interpreter is Python 2.

```bash
python scripts/check_uspex_env.py RUN_DIR --uspex-version 10.6
python scripts/check_schedulers.py --workdir RUN_DIR --test-submit
python scripts/preflight_uspex.py RUN_DIR --uspex-version 10.6
python scripts/uspex_status.py RUN_DIR
python scripts/check_run_liveness.py RUN_DIR --sample-seconds 90
python scripts/extract_uspex_candidates.py RESULTS_DIR --list-fields
python scripts/smoke_legacy105_custom.py
python scripts/smoke_legacy105_custom.py --adapter "$PWD/assets/legacy105/calcElasticML_adapter_template.py" --allow-skip
```

Its own test suite runs offline, with no USPEX installation and no cluster:

```bash
python -m unittest discover -s scripts -p 'test_*.py'
```

## Installing into an agent

The skill is the repository root: `SKILL.md` carries the metadata and everything else is
content it loads on demand.

- **Claude-style skills / DSH** — place or symlink the checkout where the agent looks for skills
  (for example `~/.dsh/skills/uspex-skill`, keeping `SKILL.md` at that level).
- **Codex-style skills** — copy or symlink it next to your other skills (for example
  `~/.codex/skills/uspex`).
- **Any other agent** — point it at `SKILL.md` and let it follow the references. Nothing here
  depends on a specific runtime beyond Python 3 and a POSIX shell on the cluster.

Then invoke it by name (`uspex`) and ask for what you actually want, for example:

> Prepare and preflight a USPEX 10.6 variable-composition search for the Li–Mg–F–Cl system at
> 100 GPa, find which queue I can use, and show me the exact submit command before anything is
> submitted.

## What the skill assumes

- **The installed release is part of the input.** Never infer 10.6 capability from a 10.5-looking
  input; the tree's own parameter dictionary outranks the skill when they disagree.
- **Never invent site configuration.** Queue names, accounts, and scheduler directives come from
  the cluster itself (its `~/.cluster-agents.md` guide, a probe, or an existing working pair) or
  from the user — never from a tutorial or another cluster.
- **Exit status is not evidence.** A scheduler-terminal job is not a converged structure, and one
  `USPEX -r` is not a finished search.
- **Missing evidence stays missing.** Stale or absent outputs never become implicit zeros, and a
  value that cannot be established is reported as unknown rather than defaulted.
- **No borrowed chemistry.** Do not rank structures across inconsistent functionals, potentials,
  cutoffs, k-point policies, or pressures; do not reproduce licensed or private files
  (POTCAR contents, manual text, private patches).
- **A patched executable is part of the experiment.** Reusing USPEX's private `1201`–`1207`
  channel, or the numbered slot-file convention, is a property of one documented installation.
  Confirm it against the user's own source, keep it isolated, and record its provenance.

## Scope

USPEX `calculationMethod = USPEX` for 3D bulk, 2D crystals, and surface reconstruction, fixed and
variable composition. Out of scope: META, VCNEB/TPS, PSO, MINHOP, molecular-crystal (`310`/`311`)
workflows, and magnetic-property *analysis* (the magnetic `s`/`S` flag and `fracSpinMut` are
documented, since they change how a bulk search's energy ranking behaves).

## Credits and disclaimer

- The `~/.cluster-agents.md` cluster-guide convention and its canonical template come from
  [AI-Computational-Chemist](https://github.com/JCLiuGroup/AI-Computational-Chemist), which this
  skill's progressive-disclosure layout follows.
- [USPEX Analyzer](https://github.com/chen121760/USPEX-Analyzer) is a good companion for
  interactive hull/Pareto exploration and structure export.
- This is an independent, unofficial project. It is not affiliated with or endorsed by the USPEX
  team; USPEX itself and its manual remain theirs. Cite USPEX as its authors require, and check
  your installed release's own documentation before relying on any statement here.
- Content was written from observable behaviour of real installations and public documentation.
  It contains no licensed potentials, manual text, private source patches, or site credentials.
- The USPEX 10.5 legacy custom-property description is a clean-room interface record of a
  patched workflow that a user was already running: the patch semantics, the `geom.out`/
  `energy.txt` handshake, the numbered slot-file contract and the `1201`–`1207` mapping
  evidence come from their own files and their own notes, not from a project model or
  formula. What the materials could not settle is labelled as unconfirmed rather than
  guessed. The reference adapter, the callback patch and the smoke test in this repository
  are freshly written for this skill.
