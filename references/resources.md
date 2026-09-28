# Resources and provenance

> Load this when: locating authoritative syntax, release-specific examples, or analysis tools.

## Primary USPEX sources

- [USPEX 10.5 online manual](https://uspex-team.org/online_utilities/uspex_manual_release/EnglishVersion/uspex_manual_english/index.html)
- [USPEX 10.6 online manual](https://uspex-team.org/online_utilities/uspex_manual_release-10.6_04_06_2026/EnglishVersion/uspex_manual_english/index.html)
- [USPEX release notes](https://uspex-team.org/zh/uspex/release)
- [USPEX documentation portal](https://uspex-team.org/en/uspex/documentation)
- Installed examples: `USPEX -e` and `USPEX -c NUM`

The installed executable and its bundled examples outrank this skill when syntax
differs. Record the release and example used in every project README.

## Cluster-specific facts this skill cannot know

Queue/partition names, accounts, resource ceilings, and site policy differ per cluster and are
**not** in any manual. Get them from the machine itself:

- `python scripts/check_schedulers.py --workdir RUN --test-submit` — reports the schedulers and
  queues present, their limits, what the run's `Submission/` currently requests, and whether a
  dry run is accepted;
- the site's own pages: Slurm (`sinfo`, `scontrol`, `sbatch`) and PBS (`qstat`, `qsub`,
  `pbsnodes`) man pages, plus the cluster's user documentation for module names and accounts;
- the site's example job scripts. Copy the directives, not the queue name.

Treat any queue name found in a tutorial, a paper, or another project as a placeholder.

## Companion projects

- [AI-Computational-Chemist](https://github.com/JCLiuGroup/AI-Computational-Chemist):
  the progressive-disclosure layout used by this skill.
- [USPEX Analyzer](https://github.com/chen121760/USPEX-Analyzer): browser-based
  analysis for 3D/2D, fixed/variable composition, convex hulls through quaternary
  systems, Pareto fronts, genealogy, filters, and structure export.

## Cluster facts: the `~/.cluster-agents.md` convention

Site facts (scheduler, default queue/partition, account, module preamble, launch commands,
job-script patterns) belong to the **cluster**, not to this skill. The convention is a file the
agent reads after logging in:

```text
~/.cluster-agents.md      # on the cluster itself, one per machine
```

- **It is the third and decisive tier of site discovery**: local connection bootstrap (never in a
  repo) → login MOTD → this guide. On conflict the guide wins over anything the MOTD points at,
  because it records what was actually verified on that machine.
- **Its canonical template is owned by the `hpc-submit` skill**
  (`tools/hpc-submit/references/cluster-guide-template.md` in AI-Computational-Chemist). This
  skill does not duplicate that schema; it reads the `## Scheduler` section and can emit one:

  ```bash
  python scripts/check_schedulers.py --facts ~/.cluster-agents.md     # read recorded facts
  python scripts/check_schedulers.py --facts-out BLOCK.md             # render observed facts
  ```

- **Record, do not ask twice.** After verifying a newly discovered fact, merge it into the guide
  (the guide's own update rule). Keep secrets and licensed contents out; keep connection details
  on the operator's machine.

If the guide is absent, probe as in `submission.md` and then offer to create it.

## Provenance of local practice

The 10.5 compatibility section was distilled from a private group workflow and
real PBS/Slurm calculations. It records only observable interfaces, decision rules,
and generic pseudocode. No private scripts, formulas, trained models, cluster paths,
or source patches are included.

The scheduler assets are site-neutral clean-room templates. They are not copies of
any cluster's Submission directory and must be adapted and smoke-tested locally.
