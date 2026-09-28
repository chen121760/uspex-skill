# Custom properties and external optimizers

> Load this when: defining multiple objectives, using USPEX code `99`, connecting an
> ML potential/predictor, or reproducing the group USPEX 10.5 custom method.

## Read this first: 10.5 and 10.6 spell "code 99" completely differently

`abinitioCode = 99` exists in both releases and means "run my own external
program" in both. Everything around it differs, and the difference is the number
of **stages** and who owns the file handshake.

| | **USPEX 10.6 — native** | **USPEX 10.5 — stock** | **USPEX 10.5 — patched** |
|---|---|---|---|
| `relax + property`: number of code-99 stages | **ONE** code-99 stage | Works only as a placeholder | **TWO** code-99 stages |
| Who runs your program | `commandExecutable` line *k* for stage *k* | same, but the stock callback ignores the result | same |
| Who reports enthalpy | `Specific/user_property_calculator.py` flag `1` | `FunctionFolder/Tool/userCalcFitness.py` flag `1` (stock: `5555`) | patched `userCalcFitness.py` |
| Who reports property x | the **same** callback, flag `101` | not supported | patched `FunctionFolder/calcElasticML.py`, position `P1`…`P7` → `1201`…`1207` |
| Stage-to-stage geometry/energy | **no contract; do not rely on one** | no contract | project convention: `geom.out` + `energy.txt`, read by the patched callback |
| `optType` syntax | `1 max_x` (direction prefix) | `1 1201 [N]` (switch generation) | `1 1201 [N]` |
| Where the value shows up | `Individuals` → `Property_X` (negated copy), `Pareto_ranking` (raw) | — | `results1/MLProperties`, `ParetoRankingInGeneration` |
| Production-ready on a stock install | yes | **no** | only in the isolated patched tree |

The single most consequential line in this table is the first one: on 10.6, a
relaxation followed by a property evaluation is **one stage with one wrapper
program**, not a chain of stages.

## Native USPEX 10.6 route (preferred)

### 1. Declare the objective direction in `optType`

```text
% optType
1 max_x
% EndOptType
```

The second field is an objective with an **explicit direction prefix**:
`min_x`/`min_y` minimize, `max_x`/`max_y` maximize. Enthalpy is always objective 1
(minimized) in a multi-objective run.

Verified on 10.6 (03/06/2026): `1 min_x`, `1 max_x`, `1 max_y` are accepted and
print the `Multi-Objective Pareto optimization` banner in `results1/OUTPUT.txt`;
a **bare** `1 x` or `-1 x` aborts with `Error in DetermineOptType (line 107)` →
`MATLAB:badsubscript`. Write the prefix. EX37 ships `1 min_x`; a bare `x`/`y` in
`optType` is not a safe spelling on this build even though some 10.6 material
shows it.

### 2. One code-99 stage, one `commandExecutable` line

```text
% abinitioCode
99
% ENDabinit

% commandExecutable
/home/user/miniconda3/envs/mattersim/bin/python -u ./run_matter_logd.py > engine.out 2>&1
% EndExecutable
```

Rules that matter:

- **One line per stage.** With `abinitioCode 99` there is exactly one line. Each
  additional `99` needs its own line, but see "Do not chain stages" below before
  adding one.
- **Invoke the interpreter directly** (`envs/<env>/bin/python -u ./wrapper.py`).
  `conda run -n <env> python ...` interposes a process between USPEX and your
  program: the working directory, `sys.executable` and stdout redirection stop
  being what your script expects. Both 10.6 runs that work in the group's test
  tree call the environment's `python` binary by absolute path.
- **Redirect inside the command** (`> engine.out 2>&1`) so USPEX does not parse
  your program's chatter as its own output.
- The wrapper itself is a project file. `USPEX` copies `Specific/*` (including
  `Specific/run_*.py` and `Specific/user_property_calculator.py`) into each
  structure directory before running the stage, so `./run_matter_logd.py` resolves.

### 3. `Specific/user_property_calculator.py` is the only thing USPEX reads back

USPEX invokes it as

```text
python3 Specific/user_property_calculator.py FLAG STEP
```

with the **structure's calculation directory as the working directory**, and reads
exactly one line from stdout:

```text
<CALLRESULT> <value>
```

| Flag | Request | Convention |
|---|---|---|
| `0` | did this stage produce a complete, finite result? | return `1` (finished) or `0` (not finished) |
| `1` | energy / enthalpy | minimization |
| `101` | property `x` | direction comes from `optType` |
| `102` | property `y` | direction comes from `optType` |

Flag `0` is asked for every stage; `1` and the property flags are asked after the
stage's command has run. Answering `0` forever is the classic silent failure: the
driver retries the structure instead of ever reaching the property.

### 4. Put *both* calculators inside the single stage

This is the shape that a 10.5 two-stage workflow must be translated into:

```text
USPEX writes geom.in into CalcFold<ID>
  -> commandExecutable runs ./run_adapter.py (cwd = CalcFold<ID>)
       step A: external optimizer/predictor relaxes geom.in
               -> geom.out (relaxed geometry), energy.txt (enthalpy)
       step B: property model runs on geom.out
               -> P1.txt (raw property value), P2.txt... (features)
       step C: restore energy.txt to step A's enthalpy if step B overwrote it
       step D: copy geom.out to a preserved name (see below)
  -> USPEX asks flag 0, flag 1, flag 101 in the SAME directory
```

A complete, tested adapter in this shape:

```python
#!/usr/bin/env python3
"""USPEX 10.6 code-99 engine: relax with an ML potential, then score the property."""
import math, shutil, subprocess, sys
from pathlib import Path

RELAXER = Path("/home/user/bin/user_cal_step5.py")      # reads geom.in, writes geom.out
PROPERTY = Path("/home/user/bin/logD_mobility_geom.py")  # reads a geometry, writes P1.txt
UNCONVERGED = 999999.0


def scalar(path: Path):
    """Return the single finite scalar in path, else None."""
    try:
        tokens = path.read_text(encoding="utf-8").split()
        if len(tokens) != 1:
            return None
        value = float(tokens[0])
    except (OSError, ValueError):
        return None
    return value if math.isfinite(value) else None


def main() -> int:
    relaxed = Path("geom.out")
    proc = subprocess.run([sys.executable, str(RELAXER), "--pressure", "0", "--max-retries", "1"])
    energy = scalar(Path("energy.txt"))
    if proc.returncode != 0 or not relaxed.is_file() or energy is None or energy >= UNCONVERGED:
        # MatterSim failed: leave direction-aware sentinels, not missing files.
        Path("energy.txt").write_text(f"{UNCONVERGED:.16g}\n", encoding="utf-8")
        Path("P1.txt").write_text("-10.0\n", encoding="utf-8")
        return 0
    Path("energy.txt").write_text(f"{energy:.16g}\n", encoding="utf-8")
    # The property model must see the RELAXED cell; keep a copy because the
    # callback may be asked after the stage directory has been consumed.
    shutil.copyfile(relaxed, "relaxed_geom.out")
    subprocess.run([sys.executable, str(PROPERTY), "--temperature", "300", "--geom", str(relaxed)])
    Path("energy.txt").write_text(f"{energy:.16g}\n", encoding="utf-8")   # step B may clobber it
    if scalar(Path("P1.txt")) is None:
        raise RuntimeError("property model produced no usable P1.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

and the matching callback:

```python
#!/usr/bin/env python3
"""USPEX 10.6 callback: enthalpy (flag 1) and property x (flag 101)."""
import math, os, sys

FAILED_ENERGY = 999999.0


def read_scalar(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise FileNotFoundError(path)
    tokens = open(path, encoding="utf-8").read().split()
    if len(tokens) != 1:
        raise ValueError(f"{path}: expected one scalar")
    value = float(tokens[0])
    if not math.isfinite(value):
        raise ValueError(f"{path}: not finite")
    return value


def main(calc_path, flag, step):
    del step
    if flag == 0:
        # The engine preserved the relaxed geometry; geom.in alone only proves
        # USPEX generated the structure.
        relaxed = os.path.join(calc_path, "relaxed_geom.out")
        if not os.path.isfile(relaxed) or os.path.getsize(relaxed) == 0:
            return 0
        read_scalar(os.path.join(calc_path, "P1.txt"))
        return int(read_scalar(os.path.join(calc_path, "energy.txt")) < FAILED_ENERGY)
    if flag == 1:
        return read_scalar(os.path.join(calc_path, "energy.txt"))
    if flag == 101:
        return read_scalar(os.path.join(calc_path, "P1.txt"))
    if flag == 102:
        raise ValueError("property y is not configured")
    raise ValueError(f"unsupported flag: {flag}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: user_property_calculator.py FLAG STEP")
    print(f"<CALLRESULT> {main(os.getcwd(), int(sys.argv[1]), int(sys.argv[2]))}")
```

### 5. Why the wrapper preserves the relaxed geometry

Observed on a working 10.6 run: **after the last stage's command finishes, USPEX
consumes `geom.out`, then asks flags `0`/`1`/`101`.** A callback that reads
`geom.out` for a geometry-derived property therefore finds it missing. Copying
`geom.out` to `relaxed_geom.out` inside the stage and reading the copy is the
pattern both verified group runs use. Do not "solve" this by reading `geom.in`:
`geom.in` is the *unrelaxed* generated structure, and a property evaluated on it is
scientifically wrong (a cell can change by more than 20 % during relaxation).

### 6. Direction and sign convention

Write the **real, measured value** and let `optType` supply the direction.

- larger is better (log D, modulus, gap): write the raw positive value;
- smaller is better (a cost, a deviation): write the quantity as measured;
- every objective is stored internally oriented toward "smaller is better", so the
  `Individuals` column shows the **negated copy** while `Pareto_ranking` carries
  the raw magnitude used for ranking.

Observed on 10.6 (space-group objective, real output):

| File | Value for a structure whose property is 221 |
|---|---|
| per-structure `P1.txt` | `221` |
| `results1/Individuals` `Property_X` | `-221.000` (negated copy) |
| `results1/Pareto_ranking` `Fitness` | `221.000` (raw, used for ranking) |
| `results1/Properties` | a pointer file: the text `See Individuals` |

Never negate a larger-is-better value to "fix" the `Individuals` column; that
drives the search to the worst candidates.

### 7. Sentinels

An expected scientifically invalid candidate (no mobile species, gas-like cell,
unconverged relaxation) may receive a **direction-aware penalty** that the project
documents and tests — for a maximized property the low value, for a minimized one
the high value. Missing models, malformed inputs, unhandled exceptions and
scheduler failures must raise instead, so they can never be mistaken for a
penalised but valid candidate. Never write an implicit zero.

## Working reference on disk

Two complete 10.6 single-stage runs exist in the group's test area and are the
fastest way to answer "what does a correct adapter look like here":
`~/work/07-USPEX10.6-tests/uspex106-mattersim-sg-direct2-*` (MatterSim +
space-group property, includes the `1 max_x` `Individuals` output) and
`uspex106-li-mg-f-cl-logd-*` (MatterSim + log D + `1 max_x`, the same shape as a
production custom-objective search). Copy their skeleton before writing a new
adapter, and check the run's own `results*/Individuals` for a `Property_X` column
as the proof that the channel is live.

## Do not chain code-99 stages on 10.6

**Anti-pattern (reproduced failure):** translate the 10.5 two-stage ML workflow
literally into `abinitioCode 99 99 99` with three `commandExecutable` lines —
relaxation, an "energy carry-forward" shell script, and the property predictor.

What happens on 10.6:

- USPEX does parse all three `USERcode` stages and prints "There are 3 local
  relaxation steps for each individual structure";
- the later stages do **not** inherit the earlier stage's artefacts. Stage 2 in the
  reproduced run logged `no geom.out from the relaxation stage`: the relaxed
  geometry written by stage 1 was gone, so the property predictor scored the
  *unrelaxed* input;
- the property is only ever requested after the **last** stage, so a broken middle
  stage silently removes the objective instead of failing loudly;
- the run ends with `Many structures failed after relaxation` in `Errors`, no
  `P1.txt`, no `logD`, and an `Individuals` file with a header and no rows.

Symptoms to recognise: `Individuals` header-only, `P1.txt`/`log.D` absent from
every `CalcFold*`, `Errors` full of relaxation failures, and a callback whose flag
`0` never returns `1`. When you see these on 10.6, collapse the chain into one
stage rather than debugging the handshake.

If you genuinely need two external programs with different resource profiles, run
them **sequentially inside one wrapper** (as above). `numParallelCalcs` still
controls concurrency; nothing about one stage prevents two calculators.

## Legacy USPEX 10.5 route

Only use this when a patched 10.5 installation must be preserved. It is a
clean-room interface description; never copy, emit, or reconstruct the private
teaching files or their implementation verbatim.

`FunctionFolder/calcElasticML.py` (the seven ML-elastic slots `1201`-`1207`) and
`FunctionFolder/Tool/userCalcFitness.py` (the code-99 completion/enthalpy callback)
are **installation-level** files, not files under `Specific/`. Modify only an
isolated copy of the exact installation and record original and modified checksums.

Observed file-level contract:

```text
USPEX writes geom.in
external command relaxes/evaluates it
external command writes geom.out and energy.txt
external command also writes the custom scalar file(s)
userCalcFitness.py reports completion/enthalpy for the code-99 stage
calcElasticML.py reads the custom files and emits seven positional values
```

The relaxed geometry has **no documented return filename**: `geom.out` and
`energy.txt` occur nowhere in the shipped 10.5 tree or manual. They are this
project's own convention and work only because the patched callback reads them.
USPEX itself re-derives the structure and passes a symmetrised copy as a *path* to
the positional callback `calcElasticML.py <STRUCTURE> <MODELPATH>` →
`<CALLRESULT> v1 ... v7`.

- `userCalcFitness.py` `flag == 0`: return `1` only when the current stage has a
  nonempty parseable `geom.out` and a nonempty finite `energy.txt`; file existence
  alone is not enough when stale files are possible.
- `userCalcFitness.py` `flag == 1`: read one finite energy and remove/archive it
  only after successful parsing if the workflow expects consumption.
- `calcElasticML.py`: read `P1`…`P7` in order into `1201`…`1207`, validate
  finiteness, and never turn a missing value into `0.0`.

Typical `INPUT.txt` (patched tree only):

```text
% optType
1 1201 [2]
% EndOptType

% abinitioCode
1 1 1 1 1 99
% ENDabinit
```

The switch generation must be later than generation 1: on 10.5.0 a switch of `1`
aborts at the end of generation 1 with `Undefined function or variable 'multiFit'`
in `extractStructures` (reproduces with the stock ML channel too). Use `[N]`,
`N >= 2`, and verify from a toy run that `MLProperties` is populated even before
the switch.

In 10.5, `optType` values `1201`-`1207` have official elastic-property meaning.
Reusing one as a private channel requires an isolated, documented installation;
never apply that convention to an unpatched executable.

## Adapter acceptance tests

Run these before any production launch:

1. `scripts/smoke_code99.py SMOKE_DIR --command ... --callback user_property_calculator.py
   --property-flags 101 --penalty -10 --required-file geom.out --required-file energy.txt
   --required-file P1.txt --require-fresh --min-species 2`
   — proves the callback answers flags `0`, `1`, `101` with exactly one
   `<CALLRESULT>` line and that the engine left fresh, finite artefacts.
2. One real mixed-composition structure through the actual stage command, in a
   throwaway directory: check `geom.out`, `energy.txt`, the preserved relaxed copy
   and `P1.txt` all exist, and that `P1.txt` was computed from the **relaxed** cell.
3. A reduced scheduler-backed pilot (`numParallelCalcs = 1`, tiny population,
   tiny `initialPopSize`): confirm the submission callback runs, the stage command
   runs, and `results1/Individuals` gains a populated `Property_X` row.
4. Only then launch the production search.
