# `assets/legacy105/` — reference material for the patched USPEX 10.5 interface

These files are **templates and documentation for a private, patched USPEX 10.5
installation**, not USPEX source files, and not a supported USPEX feature. Read
[`references/custom-objectives-105.md`](../../references/custom-objectives-105.md) first:
it contains the verified patch, the file contract, the mapping evidence, the validation
ladder, and the provenance record.

| File | Role |
|---|---|
| `calcElasticML_adapter_template.py` | Drop-in reference implementation of the legacy property adapter: reads one scalar per slot file, prints one fixed-order `<CALLRESULT>` line, logs the resolved slot directory, and exits non-zero on any broken slot. Per-slot `--allow-default Pi.txt=VALUE` declares an optional slot. |
| `userCalcFitness_patch_template.py` | Reference patch for the `code 99` user-code stage: `flag 0` completion check and `flag 1` energy transport, including the single-use `energy.txt` handshake. `flag == 6` and the other stock branches are left as the stock template had them; the unknown-flag branch returns a constant `5555` instead of the stock random number (a documented deviation). |
| `INPUT_105_annotated.txt` | Documentation-only `INPUT.txt` skeleton with the objective-block variants and the stage-command requirements. Not runnable as-is. |

## Cross-check the adapter against the smoke test

The smoke test validates any adapter, including this template, on mock slot files.
Pass the adapter as an **absolute** path — the harness runs it from a temporary
scratch directory, so a relative path would resolve against that directory:

```bash
python scripts/smoke_legacy105_custom.py \
    --adapter "$PWD/assets/legacy105/calcElasticML_adapter_template.py" --allow-skip
```

The template is written to the strict contract, so every misconfiguration case must
pass (the harness observes a non-zero exit and no `<CALLRESULT>` line). `--allow-skip`
is needed because a handful of harness self-check probes and the optional-second-argument
case do not apply to an external adapter; without it the run exits non-zero on those
skips by design, so that nothing is skipped silently.

`--compat-legacy` changes the *reporting* mode: a state the strict contract rejects is
listed as `CHARACTERIZED` instead of failing the run, with the dangerous silent-zero
default called out explicitly. Alone, it characterizes the harness's own embedded
legacy adapter; combined with `--adapter`, it characterizes the adapter you passed in
without changing it. To reproduce that default in a real adapter, declare it per slot
with `--allow-default`.

## Installing into a tree

1. Back up `<USPEX>/application/archive/src/FunctionFolder/Tool/userCalcFitness.py` and
   `<USPEX>/application/archive/src/FunctionFolder/calcElasticML.py`, recording their
   SHA-256 values.
2. Install the patched files under those exact names.
3. `python -m py_compile` both, then run the adapter smoke test, then the `flag 0`/`flag 1`
   scratch test, then one real structure, then a small pilot.
4. Record the provenance block from the guide, including the slot map
   (`P1`…`P7` → property, unit, direction, penalty).

Keep the patched tree separate from any shared or unpatched installation. The
patch points are files inside the installation tree, and the driver loads them from
that tree, so verify `USPEXPATH` and the launcher path rather than relying on `PATH`
ordering alone.
