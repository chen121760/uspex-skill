# Submission assets

Use a pair already proven on the target site and audit it against
`references/submission.md`, or copy exactly one neutral pair into a USPEX run's
`Submission/` directory and edit the constants marked `EDIT`.

These templates are intentionally site-neutral reference implementations, not
drop-in cluster scripts. They do not contain private hostnames, queues, module
paths, accounts, or executable paths. The placeholder tokens make an unedited
production submission fail visibly.

Find a real queue before editing anything — never invent one:

```bash
python scripts/check_schedulers.py --workdir RUN_DIR --test-submit
```

It lists the Slurm partitions or PBS queues this cluster exposes, flags ones that are
down/disabled or restricted to other accounts, shows which queue the current
`Submission/` requests (and whether it still holds the `<PARTITION>`/`<QUEUE>`
placeholder), and with `--test-submit` runs Slurm's dry run so an inaccessible
partition is rejected before a production run exists.

Compile without submitting:

```bash
python -m py_compile Submission/submitJob_local.py Submission/checkStatus_local.py
```

Then exercise ID parsing and state transitions with one short, site-approved test
job before allowing USPEX to create a population.
