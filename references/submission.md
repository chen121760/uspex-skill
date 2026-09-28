# Submission callbacks and persistent execution

> Load this when: adapting `Submission/` for Slurm/PBS, finding out which queues you may use,
> debugging job-state polling, or running repeated `USPEX -r` calls.

## Find a usable queue first (do this before anything else)

A queue name is **site-specific** — never guess it, never copy it from a tutorial, another
cluster, or a previous project. Resolve it in this order, cheapest source first:

0. **Read the cluster's own operating guide** — `~/.cluster-agents.md` in the home directory of
   the machine you are logged into. This is the convention used by the `hpc-submit` skill
   (canonical template: its `references/cluster-guide-template.md`): a per-cluster file, authored
   once on the cluster, holding the scheduler, default queue, accounts, module preamble, launch
   commands, and job-script patterns. When it exists it beats probing *and* beats anything the
   MOTD points at, because it records what was actually tested here. Read it before anything else:

   ```bash
   python scripts/check_schedulers.py --facts ~/.cluster-agents.md   # default path anyway
   ```

   A recorded queue is still confirmed against the live scheduler — a guide can be stale — but a
   recorded fact means the user is not asked twice. If the guide exists but records no queue, ask
   the user for it, verify it, then record it. If there is no guide at all, run the discovery
   below and then **offer to write one** (`--facts-out` renders a `## Scheduler` block containing
   only observed facts, with blank fields left blank so the next reader knows to ask).

1. **Probe the scheduler.** Slurm calls them *partitions*, PBS calls them *queues*; both answer
   the same three questions: what exists, what your account may submit to, and with which
   resource limits:

   ```bash
   python scripts/check_schedulers.py --workdir RUN_DIR        # readable report
   python scripts/check_schedulers.py --json                   # machine-readable
   python scripts/check_schedulers.py --test-submit --nodes 1 --ntasks 8 --walltime 01:00:00
   ```

It is read-only: it lists queues and never queues a job. `--test-submit` is the one exception
and is still safe — it runs `sbatch --test-only`, a Slurm dry run that validates the request
with no allocation. Exit codes: `0` a usable queue was found, `2` no scheduler client at all,
`3` a client exists but no queue could be confirmed.

The underlying commands, if you prefer to look yourself:

| Question | Slurm | PBS family |
|---|---|---|
| Which scheduler am I on? | `sinfo --version`, `sbatch --version` | `qstat -B`, `qstat --version` |
| What exists? | `sinfo -s`, `sinfo -o "%P %a %l %D %c %m %G"`, `scontrol show partition` | `qstat -Q`, `qstat -q` |
| What are the limits? | `scontrol show partition <name>` (`MaxTime`, `MaxNodes`, `TRES`, `AllowAccounts`) | `qstat -q` (`resources_max`, `resources_default`, `max_user_queuable`) |
| May *I* use it? | `scontrol show partition` → `AllowAccounts`/`AllowGroups`; then a dry run: `sbatch --test-only --partition=P --nodes=1 --ntasks=8 --time=01:00:00 --wrap=true` | request one tiny job and read the rejection; `qstat -q` → `acl_user`/`acl_group`/`enabled`/`started` |
| Is it healthy? | `sinfo -R` (why nodes are down), `sinfo -N -o "%P %t"` | `pbsnodes -a \| grep -E "state|queue"` |
| Why is my job waiting? | `squeue -u $USER -o "%.10i %.9P %.8T %.10r %.10M"` (`r` = reason: `Priority`, `Resources`, `QOSMaxJobsPerUser`, …) | `qstat -f JOBID`, `qstat -u $USER`, `showq -u $USER` |

Three traps beginners hit:

- **`sinfo` lists partitions you cannot submit to.** Visibility is not permission. Confirm with
  a dry run or the partition's `AllowAccounts` before wiring one into `Submission/`.
- **`module avail` / `module load` often come first.** On many clusters the client exists but is
  not on `PATH` until a module is loaded, so "no scheduler found" may just mean "load the module".
- **PBS is a family, not a product.** Torque, OpenPBS, PBS Pro, and the PBS-derived controllers
  used on some Chinese academic clusters all answer `qsub`/`qstat` with different columns and
  different queue semantics. Read `qstat -B` and treat queue limits as server-specific.

Once you have a name, write it into the run's
[`Submission/submitJob_local.py`](../assets/submission/README.md) (`PARTITION` for Slurm,
`QUEUE` for PBS) and re-run the discovery helper with `--workdir`: it reports the configured
queue and flags a callback that still contains the `<PARTITION>`/`<QUEUE>` template placeholder.
Then prove one tiny job end to end before the production population.

## When `Submission/` is not configured yet

A run with no usable `Submission/` pair cannot submit anything, so resolve it in this order
instead of inventing a queue name or hand-writing scheduler directives:

1. **Take the answer from the cluster guide if it is recorded there.** `~/.cluster-agents.md`
   names the default queue, the account, and often pastes a tested job-script header — write
   those into `Submission/` rather than reconstructing them.
2. **Otherwise look for a pair that already works on this machine.** Reuse beats invention, and an
   existing pair silently carries the queue name, the account, the module preamble, and the
   resource request the site actually accepts:

   ```bash
   python scripts/check_schedulers.py --workdir RUN --search-root ~/work
   ```

   The helper walks that root (bounded depth and directory budget, pruning `results*`,
   `CalcFold*` and caches), reports every `submitJob_local.py` / `submitJob_remote.py` it finds,
   and extracts the queue name each one uses. On a real Slurm cluster this reports, for example,
   `queue=uspex` from a neighbouring project. Copy the pair into `RUN/Submission/`, adjust
   anything project-specific, and re-run the helper until the state reads `pair_present`.
2. **If nothing was found, check the cluster guide, then ask the user.** Do not guess a queue name,
   an account, or scheduler directives, and do not copy a queue name from a tutorial or another
   site. Ask for the scheduler the batch nodes run, the queue/account to use, the module preamble,
   and whether the site ships a `Submission/` pair to copy. The helper prints this as the
   `ask-user` verdict so the request is explicit rather than improvised.
3. Only when neither exists, copy the neutral template for the detected family from
   `assets/submission/{slurm,pbs}/` and fill in the placeholders, as described below. The
   templates are deliberately site-neutral and will fail visibly until specialised.
4. **Record what you learned** in the cluster guide so the next session starts at step 1:

   ```bash
   python scripts/check_schedulers.py --facts-out /tmp/scheduler-block.md
   ```

   That renders a `## Scheduler` block containing only observed facts; merge it into
   `~/.cluster-agents.md` on the cluster (append or edit the existing section) and verify each
   line. Blank fields are intentional — they mark facts still owed by the user, and the guide's
   own update rule is "record stable newly discovered facts here after verification, so the user
   is never asked twice". Keep secrets, tokens, and licensed file contents out of it.

A remote pair (`whichCluster = 2`) additionally needs transport, paths, and an SSH setup that
USPEX's own submission scripts can use — see the remote-submission notes further down.

## What USPEX expects

For `whichCluster = 1`, USPEX calls local scheduler adapters in `Submission/`:

```text
submitJob_local.py -i STRUCTURE_INDEX -c "ENGINE COMMAND"
checkStatus_local.py -j JOB_ID
```

The scripts print a marker followed by a machine-readable value:

```text
<CALLRESULT>
123456       # submit: scheduler job ID

<CALLRESULT>
0            # status: still queued/running

<CALLRESULT>
1            # status: scheduler considers it terminal
```

For `whichCluster = 2`, adapt the release's remote submission/check scripts and
test transport, paths, quoting, and file synchronization separately. “Remote” is a
USPEX execution mode, not a synonym for logging into the same cluster by SSH.

The shipped pair hard-codes both ends and transports a **VASP-only** file list:

```text
submitJob_remote.py : Home = '/home/etikhonov';  Address = 'rurik'
                      ssh <Address> mkdir -p <Home>/<remoteFolder>/CalcFold<ID>
                      scp POSCAR INCAR POTCAR KPOINTS myrun  ->  <Path>
                      ssh <Address> qsub <Path>/myrun
checkStatus_remote.py: ssh <Address> qstat <JOBID>
                      scp <Address>:<Path>/OSZICAR ./     # energy
                      scp <Address>:<Path>/CONTCAR ./     # structure
```

A code-99 stage needs a different manifest: USPEX does **not** ship `geom.in` or
`parameters.json` to the remote side, and nothing fetches your stage's outputs back. Edit both
scp lists to move what your engine reads and what your callback returns, then re-test —
otherwise the callback runs locally against empty or stale files and the same structure repeats
forever. The remote side also needs its own interpreter/environment and working SSH keys.

## Adapt a site pair or start from the neutral templates

Prefer a scheduler pair already proven on the target site, then audit it against
the contracts and tests below. Preserve its queue/partition, account, resource,
environment, launcher, and job-ID conventions while replacing unsafe generic
exception handling. Do not copy a PBS pair to Slurm, a Slurm pair to PBS, or one
site's directives blindly to another site.

If no trustworthy site pair exists, copy one neutral reference pair from this
skill:

```bash
cp assets/submission/slurm/*.py RUN/Submission/
# or
cp assets/submission/pbs/*.py RUN/Submission/
```

Then edit the constants at the top of `submitJob_local.py`:

- partition/queue and optional account — take the value from the discovery step above, never
  from a template or a tutorial;
- node, task, CPU, GPU, memory, and walltime request — keep each one within the limits that
  `sinfo`/`scontrol` (Slurm) or `qstat -q`/`pbsnodes` (PBS) reported for that queue;
- module/conda activation and MPI launcher;
- scheduler job-name limits.

The assets are reference implementations, not drop-in cluster configuration. They
deliberately contain placeholders. Site policy and the engine build determine
whether the command should be `srun`, `mpirun`, `mpiexec`, or a direct GPU/Python
executable.

## Required callback behavior

| Callback | Must implement | Must fail instead of guessing |
|---|---|---|
| `submitJob_local(index, commandExecutable)` | write one job script in the current CalcFold, preserve the full engine command, submit once, parse the site's real job-ID form, emit `<CALLRESULT>` plus that ID | job-script write failure, submit-command failure, empty/ambiguous ID, unresolved template placeholders |
| `checkStatus_local(jobID)` | return `0` while the scheduler still tracks the job; return `1` only for an explicit terminal/unknown-old-ID result; emit exactly one callback result | scheduler command unavailable, controller/database/network error, permission failure, unparseable output, unknown state |

Generated job scripts must use unique job names/logs per structure, enter or retain
the CalcFold working directory, initialize the required environment, preserve the
engine command exactly once, and make engine failure visible in the scheduler exit
status. The callback must not delete scheduler or engine logs merely because a job
became terminal.

## Slurm contract

Submission must call `sbatch` and parse a numeric ID from text like
`Submitted batch job 123456`. Status should treat `PENDING`, `CONFIGURING`,
`RUNNING`, `COMPLETING`, and site-specific held/requeued states as active. Once a
job leaves `squeue`, inspect `sacct` when available and log its terminal state.

On some Slurm installations an expired or completed ID makes `squeue` exit 1 with
`slurm_load_jobs error: Invalid job id specified`. Accept only the specific
`Invalid job id` diagnosis as “not in `squeue`.” A generic
`slurm_load_jobs error`, controller connection failure, timeout, permission error,
or malformed response is an unknown scheduler state and must raise/retry rather
than return done. Any row returned by `squeue -h -j ID -o %T` remains active,
regardless of whether its state name is familiar.

## PBS contract

Submission must call `qsub` and accept IDs such as `123456.server`, while returning
the numeric or full form accepted by the installed USPEX callback. Status should
treat successful `qstat JOB_ID` lookup as active unless the state is explicitly
terminal; a nonzero `qstat` result means the scheduler no longer tracks the job.

## Completeness test matrix

First run offline tests with stubbed scheduler outputs; these must not submit:

1. Compile/import both callbacks and confirm the public function names expected by
   the installed USPEX release.
2. Render one job script from a command containing spaces, quotes, redirection, and
   multiple arguments. Confirm that it appears exactly once and that no placeholder
   remains.
3. Feed representative successful and failed `sbatch`/`qsub` text to the ID parser,
   including the site's suffix form. Reject empty, multiple, or malformed IDs.
4. Feed queued/running/held/requeued states to the status parser and require `0`.
   Feed explicit completed/expired-ID results and require `1`.
5. Simulate controller unavailable, accounting unavailable, permission denied,
   command missing, timeout, and unrecognized state. Every case must raise/exit
   nonzero; none may return `1`.
6. Assert the CLI emits exactly `<CALLRESULT>` and one machine-readable value on
   success, with diagnostics confined to stderr.

Then run one authorized live scheduler test:

1. Submit a harmless short command such as a bounded `sleep`; record the exact
   submit output and returned ID.
2. Poll while queued/running and require `0`; after it leaves the queue, compare
   with accounting and require `1`.
3. Query an old/invalid ID and verify the site's exact absence diagnostic. Do not
   generalize a shared prefix such as `slurm_load_jobs error` into success.
4. Submit one deliberately failing short command; status may become terminal, but
   its nonzero scheduler exit and missing scientific result must remain visible.
5. Submit one tiny engine/code-99 job outside USPEX and validate its geometry,
   energy, property, stdout, and stderr artifacts.
6. Set a tiny USPEX population and `numParallelCalcs = 1` in a separate smoke directory; run one foreground
   `USPEX -r` cycle and compare callback values with scheduler truth.
7. Scale only after submission count, job IDs, state transitions, scheduler exit
   states, and scientific result extraction all agree.

Never test by launching the full production population.

## Package a remote run

Use the packaging helper instead of manually copying individual runtime scripts:

```bash
python scripts/package_uspex_run.py RUN \
  --uspex-version 10.6 \
  --output uspex-run.tar.gz
```

It refuses a failed static preflight or an existing output archive unless `--force` is explicit.
The archive contains only the clean input set (`INPUT.txt`, `Specific/`, `Submission/`, optional
`Seeds/` and `POSCAR_SUBSTRATE`), the persistent driver, both environment/static checkers, the
queue-discovery helper, the code-99 smoke checker, the run-status checker and the conditional
structure packager, and `RUN_MANIFEST.json` with per-file hashes. It deliberately excludes
`results*`, `CalcFold*`, scheduler logs, and other bulky or stale run state.

## Persistent `USPEX -r` driver

USPEX 10.5/10.6 runners are commonly advanced by repeated `USPEX -r` calls. Copy
the supplied driver into the run directory:

```bash
cp assets/run_uspex_loop.sh RUN/
cd RUN
bash run_uspex_loop.sh
```

After the foreground smoke test, detach with the cluster-approved mechanism, for
example:

```bash
nohup bash run_uspex_loop.sh > run-uspex-loop.out 2>&1 &
```

The driver:

- stops when `USPEX_IS_DONE` appears;
- uses `flock` when available to prevent duplicate loops;
- honors `USPEX_POLL_SECONDS` and a `STOP_USPEX_LOOP` sentinel;
- appends every runner exit code to a durable log;
- never deletes scheduler or engine logs.

The driver only repeats `USPEX -r`; it is not a restart mechanism. Resuming or jumping back is an
operator decision made *before* re-attaching it: remove `still_reading` to continue the same run
in place, or run `USPEX --clean` and then set `pickUpYN = 1` with `pickUpGen`+`pickUpFolder` to
re-launch from a completed generation of a chosen `results` folder. The pick-up route does not
require the earlier attempt to have finished — only that source generation must be complete —
because `--clean` removes the done marker and stale state. If `USPEX_IS_DONE` is still present the
loop exits at once, and `USPEX -r` on its own only reports `USPEX IS DONE!`.

One active driver per run is essential. Two loops can race on the same USPEX state, and so can a
driver plus a hand-run `USPEX -r`. Check before attaching a second writer:

```bash
python scripts/uspex_status.py RUN --json     # concurrent_writers + state + attributed jobs
```

The status tool lists the engine and driver processes whose working directory is this run, and
raises a warning when more than one engine is active or when an engine runs with no driver while
`still_running`/`still_reading` is present.

## Capacity calculation

Before launch, state explicitly:

```text
population size
numParallelCalcs
jobs per structure and stages per structure
nodes/tasks/GPUs per job
maximum queued/in-flight structure jobs
expected simultaneously active nodes/tasks/GPUs after scheduler/QOS limits
expected walltime per stage
```

`numParallelCalcs = 80` with eight MPI ranks per structure can request 640 ranks;
the scheduler template does not enforce account quotas for you.

## Completion semantics

`checkStatus` returning 1 means “not active in the scheduler,” not “valid result.”
The USPEX engine adapter must still verify `CONTCAR`/`OUTCAR`, `geom.out`, energy,
and requested property files. Preserve failed job stdout/stderr for diagnosis.
