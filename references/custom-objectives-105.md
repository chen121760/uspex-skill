# USPEX 10.5 legacy custom-property interface

> Load this when: a 10.5 installation must be preserved and a project needs an
> objective that stock 10.5 does not provide — a patched `userCalcFitness.py`
> (code `99`) plus a replaced `calcElasticML.py` (`optType 1201`–`1207`) reading
> `P1.txt`…`P7.txt` — or when such a workflow must be migrated to 10.6.

## 1. Scope

This reference describes one private, two-file patch of a USPEX 10.5
installation, the file handshake it defines, and the ways that handshake fails.
It is a clean-room interface description: it states the observable contract and
the failure modes, and it does not reproduce any project's model, formulas,
weights, tables, or scripts.

Three provenance labels are used throughout, and they are never mixed:
| Label | Meaning |
|---|---|
| **verified legacy behavior** | Directly confirmable from the stock and patched files read for this guide. |
| **recorded in the project's own documentation** | Stated by the material that accompanies the patch, but not confirmable from a source file in the provided set. |
| **recommended hardening** | Not present in those files. A safety improvement proposed here. |

Anything that is neither directly confirmable nor a proposal is marked
**not confirmed from the provided materials** and must be re-checked against the
user's own tree before it is relied on.

This route is a private patch, not a USPEX feature. Stock USPEX 10.5 does **not**
support generic `P1`–`P7` custom properties, and `1201`–`1207` are not
custom-property slots in a stock installation — they are the official ML
elasticity objectives (section 10). Any future agent must re-verify the source
before applying anything here to a different tree.

## 2. Version warning (read this first)

```text
STOP. Confirm all four of these against the user's installation before editing.
```

1. **Which tree.** This document is about the patched 10.5 tree only. 10.6 has a
   native route — see [`custom-objectives.md`](custom-objectives.md). Do not carry
   `P*.txt`, `geom.out`/`energy.txt`, or the `1201` remap into a 10.6 design.
2. **Which files.** Two installation-level files are involved, not files under
   `Specific/`. They belong to the installation, so every run using that
   executable inherits the patch.
3. **Which source.** The provided materials do not contain the USPEX-side routine
   that reads `<CALLRESULT>` and indexes the returned values. See section 10.
4. **Which channel.** The patch points are files *inside the installation tree*, and
   the driver loads them from that tree. If the tree the active `USPEX` executable
   was installed from is not the patched one, the `1201` objective is silently the
   stock bulk modulus again — verify `USPEXPATH`, the launcher path and both file
   hashes rather than trusting a `PATH` entry.

Never claim, and never imply, that stock USPEX 10.5 officially supports `P1`–`P7`
custom properties, or that `1201`–`1207` are custom-property slots in every 10.5
installation. The approach is a private patch with a private convention.

## 3. How to use this guide

Do not start by editing files. Work in this order:

1. **Collect the user's inputs.** Use the checklist below; ask for each item
   explicitly rather than inferring it.
2. **Confirm the release from the tree, not from the statement.** Read the
   executable's banner, the installation path, and the two files themselves.
   "We use 10.5" is not evidence that the tree is 10.5, and it is not evidence
   that the tree is already patched.
3. **Build the property map before touching code.** For every slot: name, units,
   larger- or smaller-is-better, the raw value the producer writes, and the
   invalid-candidate penalty. A slot whose direction is unknown is not ready.
4. **Follow the minimal-intrusion patch protocol** (sections 7 and 17):
   back up, hash, patch the smallest possible region, keep every other stock
   branch untouched, diff, compile, smoke-test, and only then run a real
   calculation.
5. **Validate in layers** (section 15) and stop at the first layer that fails.

### Ask the user for

| # | Item | Why it is needed |
|---|---|---|
| 1 | Their stock `userCalcFitness.py` (10.5, unmodified) | The patch is defined as a minimal diff against the exact original. |
| 2 | Their stock `calcElasticML.py` (10.5, unmodified) | Confirms the invocation form, argument order, and the 7-value output order. |
| 3 | Their patched copies of both files, if any | Two people may have patched the same tree; the effective behavior is what runs. |
| 4 | The property calculator (producer) and how it writes results | The producer is the only place where the science lives. |
| 5 | Number of properties, and which objective(s) are active | Determines how many slots are real and how many are fallbacks. |
| 6 | Whether enthalpy is still an objective, and the `[N]` switch generation | Decides whether `optType 1` stays and when Pareto starts. |
| 7 | The relaxation workflow (how many engine stages, and what runs last) | The last stage is the producer; the files it leaves behind are the contract. |
| 8 | The scheduler and the job-script pair (`Submission/submitJob_*`) | The stage command runs inside a job script, so the pair is part of the chain. |
| 9 | The interpreter and dependencies that run the producer | A producer that fails to import dies before it writes anything. |
| 10 | A written property map (names, units, directions, penalties) | Prevents an implicit zero or a sign error from becoming scientific output. |

## 4. Stock USPEX 10.5 vs patched USPEX 10.5

Both releases ship the same two files. The patched installation changes behavior
in three places and leaves everything else alone.

| Behavior | Stock 10.5 | Patched 10.5 |
|---|---|---|
| code `99` completion (`flag == 0`) | Reports `1` unconditionally | Requires `geom.out` and `energy.txt` to exist and be non-empty |
| code `99` enthalpy (`flag == 1`) | Returns the placeholder `5555` | Reads the first line of `energy.txt`, then deletes the file |
| `optType 1201`–`1207` | Calls the shipped ML-elasticity calculator (`calcElasticML.py`) | Calls the replaced file, which reads `P1.txt`…`P7.txt` |
| Other `userCalcFitness.py` flags | Template branches (`flag == 6` is undefined; others return a random number) | Unchanged |
| `calcElasticML.py` code path | Stock ML elasticity, one model per invocation | Replaced; the file keeps the stock name, invocation and output arity |
| Documented support status | Official ML elastic objectives only | Private patch; no official support |

**verified legacy behavior** for every row above except the last: the stock file
sets `target = 1` for `flag == 0`, `target = 5555` for `flag == 1`, and
`target = random.random()` otherwise; the patched file replaces exactly the
`flag == 0` and `flag == 1` branches. The `1201`–`1207` row is the observable
result of the patch as the project's own material documents it, not a statement
read out of the stock source.

The patch does not add a new `abinitioCode` number. Code `99` is what the shipped
stock `userCalcFitness.py` exists to serve — its header says "Users can interface
their own code here" — so the stage type is already there. **not confirmed from
the provided materials**: the 10.5 manual's `abinitioCode` table lists only
`1`-`16`, `18` and `19`; `99` appears nowhere in the manual text. Treat the number
as this workflow's private convention and confirm it against the user's tree.
What the patch changes is whether that callback can ever answer "done" and what the
enthalpy value is.

**One code-99 stage, not two.** The elasticity calculator is *not* an
`abinitioCode` entry: it is invoked per structure for `optType 12**`, alongside
the stage chain. So the stage list is `… 99` (one user-code stage that runs the
producer), and the adapter is reached through `optType`. Do not add a second `99`
believing it will run the adapter; the evidence for this is the case `INPUT.txt`,
whose `abinitioCode` block ends in a single `99` and whose objective is `1 1201`.

## 5. Architecture

```text
INPUT.txt:  abinitioCode  ... 99
            optType      1 1201 [N]      (enthalpy + slot 1; N = Pareto switch gen)

per structure, per generation
  USPEX writes geom.in into the structure directory
        |
        v
  stage 1..k-1: the ordinary engine stages (VASP and friends) from
                abinitioCode / commandExecutable
        |
        v
  stage k, code 99: the commandExecutable line for that stage
        |   reads  geom.in (and the previous stage's CONTCAR_*)
        |   writes geom.out          <- relaxed POSCAR
        |   writes energy.txt        <- one number
        |   writes P1.txt ... P7.txt <- one number per property slot
        |   writes engine logs
        v
  PATCH POINT 1   <USPEX>/application/archive/src/FunctionFolder/Tool/userCalcFitness.py
        |   flag 0: geom.out and energy.txt present and non-empty?  -> 1 or 0
        |   flag 1: read the first line of energy.txt, then delete it
        v
  PATCH POINT 2   <USPEX>/application/archive/src/FunctionFolder/calcElasticML.py
        |   invoked with the structure path
        |   reads the slot files beside that path
        |   prints one <CALLRESULT> line: seven space-separated values
        v
  USPEX records the selected column in results*/MLProperties
        |
        v
  ranking -> Pareto selection -> next generation
```

The two patch points are independent files in two different directories. Either
one alone is useless: patch point 1 unlocks code `99` but carries no property,
and patch point 2 carries properties but is only reached for `optType 12**`,
which stock 10.5 routes into the ML-elasticity channel.

The full patch-point paths, with `<USPEX>` standing for the installation root:

```text
<USPEX>/application/archive/src/FunctionFolder/Tool/userCalcFitness.py
<USPEX>/application/archive/src/FunctionFolder/calcElasticML.py
```

**verified legacy behavior**: the two files are distinct modules in distinct
directories, and both paths above are the paths the project's own teaching
material names. Note the asymmetry — that material places `userCalcFitness.py`
under `FunctionFolder/Tool/` but `calcElasticML.py` directly under
`FunctionFolder/`. Confirm both locations in the user's tree rather than
searching only one of them.

## 6. Original USPEX 10.5 interface behavior

### `userCalcFitness.py` (stock)

Invocation, taken verbatim from the stock file:

```python
flag = int(sys.argv[1])
Step = int(sys.argv[2])
```

| Flag | Documented meaning (stock header) | Stock behavior |
|---|---|---|
| `0` | if converged/done or not | `target = 1` — unconditionally |
| `1` | energy/enthalpy | `target = 5555`, annotated "keep this number if you are not going to change enthalpy" |
| `-1` | collect all energy | `random.random()` |
| `2` | pressure tensor | `random.random()` |
| `-2` | all pressure tensor | `random.random()` |
| `3` | dielectric constant | `random.random()` |
| `4` | gap | `random.random()` |
| `5` | magmoment | `random.random()` |
| `-5` | atomic magmoments | `random.random()` |
| `6` | elastic constant matrix (array data) | `target = elasticMatrix` — the name is never defined in the file |
| `7` | atomic forces | `random.random()` |
| `8` | relaxation atomic positions | `random.random()` |
| `9` | relaxation lattice parameter | `random.random()` |
| `10` | birefringence | `random.random()` |
| `11` | half-metal | `random.random()` |

Output line:

```text
<CALLRESULT> <target>
```

**verified legacy behavior**: the table above is the stock file's own header
comment plus its branch structure. The file is a template: `flag == 6` would
raise `NameError` because `elasticMatrix` is not defined anywhere in it, and
every flag other than `0`, `1` and `6` returns a random number. Stock 10.5
therefore cannot report a real energy for a code-99 stage, and cannot report a
custom property at all.

**not confirmed from the provided materials**: where the driver chooses to call
this file for `optType 12**`. The project's own material states that setting
`1201`, `1202` and so on makes USPEX call `calcElasticML.py`, and the stock file
provides the parseable seven-value result that makes the channel work; the
USPEX-side code that links the two was not provided.

Note that the `flag == 6` branch is a different mechanism from the
`1201`–`1207` channel. Flag `6` is the user-code callback asking for a 6×6
matrix; `1201`–`1207` are parsed by the driver and routed to
`calcElasticML.py`. They are not the same code path, and one must not be used to
reason about the other.

### `calcElasticML.py` (stock)

Invocation and output, taken from the stock file's `__main__` block:

```python
structurePath = str(sys.argv[1])
MODELNAME = str(sys.argv[2])          # the model path is argv[2]
assert os.path.exists(structurePath)
```

```text
<CALLRESULT> K G E v G/K Hv Kg
```

That is one line, seven space-separated values, in that fixed order. Column *i*
of the returned list is the quantity that `optType 12i` selects — 1 is bulk
modulus, 2 shear, 3 Young, 4 Poisson, 5 Pugh, 6 Vickers, 7 fracture toughness.
**verified legacy behavior**: the single print statement and its argument order
are read directly from the stock file.

### `1201`–`1207` (stock meanings, USPEX 10.5 manual)

| Code | Symbol | Manual description |
|---|---|---|
| `1201` | K | Bulk modulus — maximization of bulk modulus |
| `1202` | G | Shear modulus — maximization of shear modulus |
| `1203` | E | Young's modulus — maximization of Young's modulus |
| `1204` | v | Poisson's ratio — maximization of Poisson's ratio |
| `1205` | G/K | Pugh's modulus ratio — maximization of Pugh's modulus ratio |
| `1206` | Hv | Vickers hardness — maximization of Vickers hardness |
| `1207` | Kg | Fracture toughness — maximization of fracture toughness |

The manual groups these as "ML-based elasticity-related properties ('12**')",
states that all seven are maximized, and notes that the fracture-toughness
formula is valid for insulators (and carbides, borides, hydrides). The 10.5
release note adds that the constant-elasticity-from-ML feature is new in 10.5
(`optType=1201-1207`) and is computed with a graph convolutional network.

**verified legacy behavior**: quoted from the 10.5 manual table and release note
in the provided extracted text. All seven are "maximization of", which matters
because it is the direction the patched file inherits for whatever it returns.
The manual also cautions, for the elasticity route it documents, that the
computed values may be numerically unstable and that the user must check them
carefully. That caution is the manual's, about its own method; the point carrying
over here is that a property value reaching an objective deserves an independent
check regardless of which route produced it.

## 7. `userCalcFitness.py` modification

### The patch

The patched file changes exactly two branches. The semantically relevant part is:

```diff
+ import os                              # added by the patch

  if flag == 0:
-     target = 1                       # unconditional in the stock template
+     target = 1 if <geom.out exists and is non-empty>
+                  and <energy.txt exists and is non-empty> else 0

  elif flag == 1:
-     target = 5555                    # stock placeholder
+     with open('energy.txt') as f: target = float(f.readline())
+     os.remove('energy.txt')
```

The `import os` is the third and last line the patch adds; without it the patched
`flag 0` branch raises `NameError`.

Both patched conditions are quoted as pseudocode placeholders on purpose: the
point is the semantics, not the spelling. When you patch the user's file, keep
their formatting, variable names and line continuations — it is their tree.

Everything else — the header, the `flag == 6` branch, the random fallback, and
the `<CALLRESULT>` print — is untouched. **verified legacy behavior**: this is
the complete behavioral difference between the stock and patched files; both
were read line by line.

**recommended hardening** in the shipped template
[`../assets/legacy105/userCalcFitness_patch_template.py`](../assets/legacy105/userCalcFitness_patch_template.py),
whose three deliberate deviations from the teaching patch you must know about if you
diff the two:

| Branch | Stock 10.5 | Teaching patch | Shipped template |
|---|---|---|---|
| `flag == 0` | `1` always | artifact check | same as the patch |
| `flag == 1`, valid number | `5555` | read, then delete | same as the patch |
| `flag == 1`, unparseable or non-finite | `5555` | forwards the token (`nan`) or raises | non-zero exit, **no** `<CALLRESULT>`; the token is kept on a parse failure |
| unknown flag (`3`, `11`, …) | `random.random()` | unchanged: `random.random()` | `5555` placeholder |
| `flag == 6` | `NameError` | unchanged: `NameError` | unchanged: `NameError` |

The unknown-flag row is the third deviation: the template deliberately returns a
constant instead of a random number, because that branch is inert for this interface
and a random value is needless noise. It is not the stock behaviour and it is not what
the teaching patch does. If your project reaches that branch, implement it explicitly.

Two details that are easy to miss:

- **`readline()` is not a parse.** It returns the first line, whatever it is. A
  multi-line `energy.txt` is accepted silently, and only the first line is used.
- **`float()` is not a validation.** `float('nan')`, `float('inf')`, `float('-inf')`
  and `float('1e400')` all succeed. As written, the patch will forward a
  non-finite value as if it were an energy.

Conversely, one ordinary safety property is already satisfied: because the delete
follows the parse, a read or parse failure raises before `energy.txt` is removed,
and the file survives for inspection. The hazard is the opposite one — a value
that *does* parse but is not an energy.

### Lifecycle of `geom.in`, `geom.out`, `energy.txt`

| Step | Who acts | File state |
|---|---|---|
| 1 | USPEX | writes `geom.in` (POSCAR) into the structure directory |
| 2 | the code-99 producer (the last `abinitioCode` stage) | reads `geom.in` and any earlier stage's output |
| 3 | the producer | writes `geom.out` (relaxed POSCAR) and `energy.txt` (one number) |
| 4 | the producer | writes `P1.txt`…`P7.txt` for the property slots |
| 5 | `userCalcFitness.py`, `flag == 0` | both `geom.out` and `energy.txt` must exist and be non-empty → `1`, else `0` |
| 6 | `userCalcFitness.py`, `flag == 1` | reads the first line of `energy.txt` as a float, then **deletes** `energy.txt` |
| 7 | `calcElasticML.py` | reads the slot files (and not `energy.txt`) |
| 8 | USPEX | ranks, selects, starts the next generation |

**verified legacy behavior** for the patched branches; the surrounding stages are
the project's documented convention, not a USPEX 10.5 standard.

### Why `energy.txt` is deleted

The delete is what makes the handshake single-use. Without it, `energy.txt` would
persist in the structure directory, and a later structure evaluated in the same
reused `CalcFold*` directory could have its `flag == 1` answered with the
*previous* structure's energy — a silent, plausible-looking, wrong number that no
error message would ever flag. Consuming the file means a second `flag == 1` for
the same directory cannot succeed by accident.

The same delete is also a real production hazard, and there is direct evidence of
it in the provided materials: one dielectric-property producer in this project's
own workflow restores `energy.txt` from a preserved `energy.txt.backup` after it has
written its own sentinels and copied the geometry, and before it extracts the
properties. **verified legacy behavior** for the restore, including its
documented reason — for a multi-objective USPEX flow, an earlier extraction step
runs first and consumes the energy, so only the backup still carries it.

Consequences to plan for:

- anything that runs **after** `flag == 1` and needs the energy must not read
  `energy.txt`; it must read a preserved copy, or the value must be re-created
  before the next consumer asks;
- anything that runs **before** `flag == 1` may read it safely, because deleting
  happens after the read.

### Ordering

`flag == 0` and `flag == 1` are separate invocations, and the patch does not
coordinate them. **verified legacy behavior**: `flag == 0` only *tests* that
`energy.txt` exists and is non-empty; it does not read, delete, or modify it.
`flag == 1` is the only branch that removes the file.

**not confirmed from the provided materials**: the exact call order inside the
USPEX driver. The safe reading is a constraint rather than a sequence claim —
`flag == 0` must never depend on `energy.txt` still being present after
`flag == 1` has run, because the file is gone at that point. An implementation
that is correct under either order is the only one worth shipping.

### The stale-energy hazard

A populated `energy.txt` is not evidence that the current structure produced it.
Reused `CalcFold*` directories, a producer that crashed before writing, and a
producer that wrote only some of its outputs all leave files behind that look
valid. The patched `flag == 0` cannot tell the difference: it checks existence
and size only.

**recommended hardening** (each item absent from the patched file unless noted):

1. treat `flag == 1` as a parse, not a read: require exactly one whitespace token,
   require it to be finite, and require it to be plausible for the project's
   energy range;
2. keep the delete after the parse (the patch already orders it that way) and make
   that ordering an explicit, tested property rather than an accident of statement
   order;
3. validate the producer's exit status and the provenance of `geom.out`, not just
   its size;
4. keep a producer-side `energy.txt.backup` and re-create `energy.txt` before any
   consumer that needs it (this is what the project's own producer already does —
   the one item here with a verified precedent);
5. have the producer write sentinel values into every output **before** extraction
   starts, so a crash leaves an obviously-invalid value rather than a stale one.

## 8. The legacy property adapter (`calcElasticML.py`)

### Thin-adapter rule

The replaced file keeps the stock file's name, invocation, and output format, and
drops the stock science entirely. It becomes a thin adapter:

```text
resolve the calculation directory from the structure-path argument
read one scalar per property slot from that directory
print ONE <CALLRESULT> line with exactly seven space-separated values
in the stock order (slot 1 ... slot 7)
```

The order is part of the contract. `optType 1201` takes the first value, `1202`
the second, and so on, so a slot that reads the wrong file silently optimizes a
different objective.

**verified legacy behavior**: the patched files in the materials have this shape —
they resolve the calculation directory from the structure-path argument, read one
scalar per slot, and print one `<CALLRESULT>` line with seven values in slot order.
The *shape above* is therefore observed, not proposed; what this guide adds is which
failures must be fatal. The two patch stages found in the materials differ only in
which slot names are read:

| Stage | Slot sources | Fallback behavior |
|---|---|---|
| 1 | three named project files plus four constants | missing file → warning on stderr, returns a per-slot default |
| 2 (canonical) | `P1.txt`…`P7.txt`, one scalar each | missing or unreadable file → warning on stderr, returns `0.0` |

**verified legacy behavior**: stage 2 does not exit non-zero on a missing file.
A missing `P4.txt` therefore yields `0.0`, which for a maximization objective is
not a "missing" marker at all — it is a candidate value, and if the real property
can be negative, it is a good one. **recommended hardening** replaces that
default with a hard failure (below).

### Invocation form

```text
python calcElasticML.py <structure_path> [model_path]
```

The adapter must accept the stock argument shape. Stage 2 documents `model_path`
as optional in its usage string but never reads `sys.argv[2]`, and it validates
only the structure path. **verified legacy behavior.** Treat the second argument
as reserved: accept it, ignore it, and never require it.

### Directory resolution

**verified legacy behavior**: the adapter resolves its calculation directory as

```python
os.path.dirname(os.path.abspath(structure_path))
```

so all slot files are looked up beside the structure file, and the adapter never
uses the process working directory. This is why passing a genuine path works and
why the rule below exists.

**recommended hardening**: if the argument is ever a bare relative filename
(`POSCAR` rather than `/abs/path/POSCAR`), `dirname` collapses to the process
working directory, and the adapter reads slot files from wherever it happens to
be. Resolve an absolute path first; then, when the argument had no directory
component, accept the resulting directory **only if it actually contains the
structure file**, and abort otherwise. Log the chosen directory on every
invocation so a caller can see which one was used. The canonical implementation
below does this, and it makes the collapse harmless: either the process is really
running in the structure directory, or the run stops instead of scoring the
wrong files.

### Canonical reference implementation

Safe by default: every slot must be present and parseable unless the operator has
explicitly declared a default for it. Generic code, no project specifics.

The shipped template
[`../assets/legacy105/calcElasticML_adapter_template.py`](../assets/legacy105/calcElasticML_adapter_template.py)
matches the listing below on every contract path and is stricter on the command
line, so **use the file rather than retyping the listing**: it validates
`--allow-default` and exits `3` on a bad declaration where the listing lets
`float()` raise, it aborts with exit `2` where the listing raises `SystemExit`
(exit `1`), it accepts only the seven slot names, and it rejects a non-finite
default. Keep the listing as the readable specification of what the file does.

```python
#!/usr/bin/env python3
"""Read one scalar per property slot and emit the seven-value CALLRESULT line."""

import argparse
import math
import os
import sys
from pathlib import Path

SLOTS = tuple(f"P{i}.txt" for i in range(1, 8))


def resolve_calc_dir(structure_path: str, bare_name: bool) -> Path:
    """Directory that holds the structure and its slot files.

    An absolute path always resolves beside itself. A bare relative filename
    would otherwise collapse to the process CWD, so accept that only when the
    CWD really holds the structure file.
    """
    if not bare_name:
        return Path(os.path.abspath(structure_path)).parent
    if Path.cwd().joinpath(structure_path).is_file():
        return Path.cwd()
    raise SystemExit(f"cannot resolve a directory for {structure_path!r}")


def read_slot(calc_dir: Path, name: str) -> float:
    """One finite scalar from name, or raise. Never returns a default."""
    target = calc_dir / name
    if not target.is_file() or target.stat().st_size == 0:
        raise FileNotFoundError(f"{target}: slot file missing or empty")
    tokens = target.read_text(encoding="utf-8").split()
    if len(tokens) != 1:
        raise ValueError(f"{target}: expected one scalar, found {len(tokens)}")
    value = float(tokens[0])
    if not math.isfinite(value):
        raise ValueError(f"{target}: not a finite scalar: {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("structure_path")
    parser.add_argument("model_path", nargs="?")  # reserved; accepted, unused
    parser.add_argument(
        "--allow-default",
        action="append",
        default=[],
        metavar="Pi.txt=VALUE",
        help="declare a slot optional and give its explicit fallback; repeatable",
    )
    args = parser.parse_args()

    allowed = {}
    for item in args.allow_default:
        name, _, raw = item.partition("=")
        allowed[name] = float(raw)

    calc_dir = resolve_calc_dir(
        args.structure_path,
        bare_name=Path(args.structure_path).parent == Path("."),
    )
    print(f"[adapter] slot directory: {calc_dir}", file=sys.stderr)

    values = []
    for name in SLOTS:
        try:
            values.append(read_slot(calc_dir, name))
        except (FileNotFoundError, ValueError) as exc:
            if name not in allowed:
                print(f"[adapter] FATAL: {exc}", file=sys.stderr)
                return 2
            print(f"[adapter] {exc}; using declared default "
                  f"{allowed[name]!r}", file=sys.stderr)
            values.append(allowed[name])

    # Exactly one line, seven space-separated values, stock order.
    print("<CALLRESULT> " + " ".join(str(v) for v in values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

This is **recommended hardening** in its defaults, not a description of the
patched files: the patched stage-2 adapter returns `0.0` for a missing slot and
exits `0`, whereas this version refuses unless the slot was declared optional
with an explicit value.

### Producer / adapter separation

Three roles, three files. Do not merge them:

| Role | Owns | Must not do |
|---|---|---|
| Producer (project script) | running the science, writing `geom.out`, `energy.txt`, and the slot files | decide the objective direction, or emit an objective-shaped number it did not measure |
| Adapter (`calcElasticML.py`) | the fixed 7-value output order and nothing else | compute, scale, clamp, or invent a property value |
| `userCalcFitness.py` | code-99 completion and the energy handshake | know anything about properties |

**verified legacy behavior** for this separation in the materials: the adapter
contains no science, and the producer contains no knowledge of `1201`.

**recommended hardening**: keep the producer out of the USPEX tree. It should be
a project file invoked by an absolute path from `commandExecutable`, so that a
re-installation of USPEX does not silently revert the science while keeping the
patch.

## 9. `P1.txt`…`P7.txt` exchange-file contract

### Per-file content

| File | Content | Example |
|---|---|---|
| `P1.txt` | exactly one scalar, the raw value of slot 1 | `41.7` |
| `P2.txt` | exactly one scalar, slot 2 | `-3.2` |
| … | … | … |
| `P7.txt` | exactly one scalar, slot 7 | `0.61` |

**verified legacy behavior**: the canonical adapter opens each file as text,
`.strip()`s it, and passes the whole result to `float()`, so a single number with
optional surrounding whitespace and a trailing newline is the contract.

**recommended hardening** — the read side must validate:

1. the file exists and is non-empty;
2. it contains exactly **one** whitespace-separated token;
3. the token converts to a float;
4. the float is finite (`math.isfinite`);
5. the token matches a plain decimal/scientific pattern (`^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$`)
   rather than "whatever `float()` happens to accept", which also admits `1_000`, `nan`
   and `inf`;
6. the value is inside the project's physical range for that slot;
7. the file is **current** for the structure being scored (freshness), not a
   leftover from a previous occupant of the directory.

### What must never be in the file

Never put any of the following in a slot file:

- a unit, an element name, a column header, or a comment;
- two or more numbers (a mean and a spread, a vector, a tensor component list);
- a sentinel word such as `nan`, `inf`, `none`, or `failed` — `float()` accepts
  the first two, so they travel all the way into the ranking;
- a Python underscore literal such as `1_000` — `float()` accepts it as `1000.0`, so a
  machine-readable formatter's output can slip through as a valid value;
- a formatted table, a JSON object, or a log line;
- a value copied from a different structure, a different functional, or a
  different stage;
- a placeholder that the adapter is expected to interpret.

**related, not to be confused**: the 10.6 native route in
[`custom-objectives.md`](custom-objectives.md) does **not** use `P1.txt` or any
numbered property file. The name `P1.txt` also appears there as one
project's choice of private intermediate. The two conventions are unrelated even
though the filename matches.

### Stale-file problem

A slot file that was written for a previous structure is indistinguishable from a
current one by content alone. Three mechanisms cause this:

1. **`CalcFold*` reuse.** Structure directories are reused, and old slot files
   survive in them.
2. **A partially failed producer.** Extraction dies after writing some outputs.
3. **A producer that writes nothing on failure** and leaves the ancient file in
   place.

**verified legacy behavior** for three of the producers in the materials, and
**not** for the rest: those three write a sentinel value into every property output
*before* extraction begins, so a crash leaves an obviously invalid value instead of
a stale one, and they write the real values only after every extraction step has
succeeded. One initializes `P1.txt`…`P6.txt` to `0` at startup and documents that
this "prevents stale results from a previous calculation from being reused if the
current extraction fails". Another writes `0` into its property file and `999` into
`energy.txt` before doing any work. The other producers in the same materials do
**not** pre-write: they write their outputs only on the success path. So sentinel-first
is a pattern this project converged on, not a property of the interface — check each
producer rather than assuming it.

**recommended hardening**: sentinel-first writing is the right pattern, but the
sentinel must be **direction-aware and finite** and must be documented as an
invalid-candidate penalty, not as a value. A sentinel that happens to be the best
possible value for a minimization objective converts an infrastructure failure
into a winning candidate.

The adapter as written has no freshness check at all. Prefer a per-structure
record — the producer writes a small sidecar with the digest of the `geom.in` it
consumed, and the adapter (or a wrapper) refuses to score slot files whose
sidecar does not match the current structure. This is the same provenance idea
described for 10.6 in [`custom-objectives.md`](custom-objectives.md), applied to
a 10.5 tree.

### Write-side rules

For the producer:

1. create every output file with a sentinel value **first**;
2. do the extraction;
3. write the real slot values only after **all** extraction has succeeded;
4. write each output to a temporary name and rename it into place, so a reader
   never sees a half-written number (a truncated write can produce a valid-looking
   shorter number);
5. on any infrastructure failure, leave the sentinel **and** exit non-zero;
6. never delete a slot file as a way of signalling failure — the adapter reads
   missing and stale file states differently, and a delete is not atomic;
7. print one diagnostic line per slot to the stage log, so a later audit can
   reconstruct what was written and from what input.

## 10. `1201`–`1207` mapping

### The official table

Section 6 reproduces the 10.5 manual's table. Those seven meanings are what a
stock installation computes. They are the reason the channel is usable at all
(the driver already parses a 7-value result) and the reason it is dangerous to
forget the patch (a patched-looking `INPUT.txt` on a stock executable quietly
optimizes a bulk modulus).

### The positional relation

`optType 12i` selects value *i* of the single `<CALLRESULT>` line. That is the
whole mechanism:

```text
1201 -> value 1  -> the first slot file the adapter reads
1202 -> value 2  -> the second slot file
...
1207 -> value 7  -> the seventh slot file
```

**verified legacy behavior**: the stock file prints exactly seven space-separated
values in a fixed order, and the patched adapter reads exactly seven slot files in a
fixed order and prints one line. The next step — that this line is what the driver
reads for `optType 12**` — is the project's own documented convention, and the
provided materials do not include the USPEX-side source that performs it.

The project's own teaching material states plainly that `1201` reads its first
custom file, `1202` the second, `1203` the third. That statement is the evidence
for the positional relation; it is not an official USPEX statement.

**Recorded consequence in the project's own documentation of this patch**: the
value that reaches the user through `1201` is recorded in the first `MLProperties`
column, whose heading is still the stock `Modulus:Bulk` label. The heading
therefore no longer describes the content, and anyone reading `MLProperties` must
be told that the column names are stock labels while the numbers are project
properties.

### What is a generic slot

**`P1`–`P7` are generic property slots.** Their meaning, units, sign convention,
direction and penalty are entirely project-defined and belong in the project's
own property map — never in this file, and never inferred from the slot number.

Two example maps, deliberately different and deliberately incomplete (names only;
no formulas, no units from a real project):

| Slot | Example map A (electronic/transport-flavoured) | Example map B (dielectric-flavoured) |
|---|---|---|
| `P1` | primary target of the search | primary target of the search |
| `P2` | secondary target, Pareto partner | secondary target, Pareto partner |
| `P3` | diagnostic-only quantity | a dielectric contribution |
| `P4` | structural descriptor | total static response |
| `P5` | screening flag | band gap |
| `P6` | unused (constant fallback) | breakdown-related quantity |
| `P7` | unused (constant fallback) | mechanical-figure-of-merit input |

The project's own materials contain a third, environment-specific map in which
`P1` is a critical-temperature prediction, `P2` a dynamical quantity and `P3` its
dynamical counterpart. That map is **one project's** property map and appears here
only as an example; it must never be presented as the meaning of the slots.

### What is not confirmed

**not confirmed from the provided materials**:

- the exact USPEX 10.5 source routine that reads `<CALLRESULT>` from
  `calcElasticML.py` and how it indexes the returned values (the user did not
  provide that source file);
- whether `1201` selects column 1 by position alone or through a name lookup in a
  configuration table;
- the full set of column headings in `results*/MLProperties` — only that its first
  column is headed `Modulus:Bulk`;
- whether the 7-value format is enforced anywhere before the parse (that is,
  whether a 6- or 8-value line raises, truncates, or reads out of order);
- whether the selected column is also re-derived or normalized anywhere between
  the parse and the ranking.

> **Before editing anything: ask the user for the source file(s) responsible for
> parsing the custom/elastic-property result.** Confirm the mapping from that
> source — or from the user's working legacy implementation — and adjust it if the
> evidence disagrees. Never infer the mapping from the version string, from the
> manual alone, or from a successful-looking run.

A safe way to settle it empirically, once the tree is available: run the adapter
by hand with seven distinct recognizable values, start a toy run, and read
`results*/MLProperties` and `ParetoRankingInGeneration` to see which value landed
in which column. That is evidence; the version number is not.

### Direction and penalty

**verified legacy behavior**: the stock `1201`–`1207` objectives are all
maximization. The patched channel inherits that direction, because the driver's
handling of `12**` is unchanged.

Contract for a project slot:

| Concept | Rule |
|---|---|
| Larger-is-better slot | the producer writes the raw measured value, unchanged |
| Smaller-is-better slot | the producer writes the quantity as measured; the direction must be expressed by the objective definition, and the project record must state which convention applies |
| Penalty for a scientifically invalid candidate | finite, and on the losing side for that slot's direction |
| Infrastructure failure | never a penalty and never a value — it must raise or exit non-zero |

**recommended hardening**: record, per slot, the exact numeric penalty and the
exact direction, and fail the run if a penalty is on the winning side. A
direction-blind penalty is how a search converges onto broken candidates.

**not confirmed from the provided materials**: how a sign convention interacts
with the `MLProperties` column for a slot chosen through `1201`–`1207`. In
particular, whether the value there is the raw one or an internally oriented
copy. Verify it against the user's own tree — with a two-structure hand test —
before drawing conclusions from a column's sign. For the corresponding 10.6
convention, see [`validation-analysis.md`](validation-analysis.md).

## 11. `INPUT.txt` configuration

Annotated example. The keys shown are the ones this interface depends on; every
other keyword keeps its normal 10.5 meaning.

```text
% optType
1 1201 [15]
% EndOptType
```

- `1` — enthalpy remains the first objective.
- `1201` — the second objective, routed to the patched `calcElasticML.py`.
  It is not a custom-property name; it is the stock elastic slot number whose
  column now carries a project property (section 10).
- `[15]` — the manual's Pareto switch generation: enthalpy-only search through
  the first 14 generations, Pareto from generation 15. See section 13 for the
  exact wording and for the two boundary values that must be avoided.

```text
% abinitioCode
1 1 1 1 1 99
% ENDabinit
```

- one entry per relaxation stage; `99` is the user-code stage and must be present
  for the producer to run at all;
- the interface does not require any particular number of engine stages — the
  example uses five so that the last VASP stage's output is what the producer
  reads.

```text
% commandExecutable
mpirun vasp_std > log
mpirun vasp_std > log
mpirun vasp_std > log
mpirun vasp_std > log
mpirun vasp_std > log
python <absolute-path-to-producer>.py > log
% EndExecutable
```

- one line per stage, in the same order as `abinitioCode`;
- the last line is the producer. Give its interpreter and script by absolute path:
  the command runs inside a `CalcFold*` directory whose `PATH` is whatever the
  submitting job script provides;
- redirect the producer's stdout inside the command, so the job script does not
  interleave the producer's chatter with the engine's.

```text
1      : whichCluster
30     : numParallelCalcs
10     : ExternalPressure
```

- `whichCluster 1` submits the site job script and polls the status callback. The
  stage command therefore executes inside `Submission/`'s `submitJob_local.py`
  job script, not in the interactive shell — a command that works by hand can
  still fail there, because modules, environment and `PATH` differ;
- start the pilot with a small `numParallelCalcs`, not the production value.
  **recommended hardening** for this interface specifically, because a patched
  property stage that fails on the first structure wastes every job started
  before anyone reads the log.

Run loop:

```sh
while [ ! -f ./USPEX_IS_DONE ]; do
  USPEX -r >> log
  sleep 30
done
```

**verified legacy behavior** for the `optType`, `abinitioCode`,
`commandExecutable`, `whichCluster`, `numParallelCalcs` and loop shapes above:
read from the case `INPUT.txt` and its line script. The producer path in the
example is the project's own path and is intentionally not reproduced here.

**not confirmed from the provided materials**: that `whichCluster 1` polls a
status callback in exactly the way described; the case `01-A-B-C.sh` shows the
driver loop only, and the callback pair is a separate file. Confirm the callback
pair against the user's `Submission/` before relying on it — see
[`submission.md`](submission.md).

## 12. Single-property example

Goal: one custom objective, one slot, one producer, no Pareto.

```text
% optType
1 1201 [9999]
% EndOptType
```

- `[9999]` sets the switch generation beyond the search's generation count, so the
  Pareto phase never starts. **verified legacy behavior**: this spelling is what
  the project's own material recommends when the intent is "record the property
  during the run" rather than "optimize on it jointly".
- With this shape the property is still recorded, per the material, in
  `MLProperties` — so a single-objective search is also the cheapest way to
  validate that the channel is live before introducing a second objective.
- Use `numGenerations` well below `9999` and state that in the project record, so
  the intent is unambiguous later.

Producer obligations, in order:

1. sentinel-write `energy.txt` and every slot file the run declares;
2. read `geom.in` and the preceding stage's output;
3. extract the property for slot 1;
4. write `geom.out`, `energy.txt`, and `P1.txt`;
5. exit non-zero on any failure and leave the sentinels in place.

Verification:

```bash
python -m py_compile <producer>.py
python <adapter>.py <structure-dir>/geom.out        # expect one 7-value CALLRESULT line
cat results1/MLProperties                            # expect the P1 value in column 1
cat results1/ParetoRankingInGeneration               # expect it there too, for ranking
```

The `ParetoRankingInGeneration` check is the one that matters: the ranking file
is where the direction is actually applied. See
[`validation-analysis.md`](validation-analysis.md) for the direction convention
and for why `Individuals` must not be used as a join key.

## 13. Multi-property example

Goal: two or more project properties, Pareto selection, staged switch generation.

```text
% optType
1 1201 1202 [15]
% EndOptType
```

Semantics of `[N]`, quoted from the 10.5 manual: setting
`max_Hardness min_Diel_sus [5]` means the search is enthalpy-only for the first 5
generations and switches to Pareto optimization from the 6th. The same text says
in general that a multi-objective search should first be run on enthalpy alone
and only then on the Pareto set of properties.

Rules that follow:

1. **Switch late enough.** A switch generation of `1` activates the second
   objective inside generation 1; the repo's error map records that this aborts
   with `Undefined function or variable 'multiFit'` in `extractStructures` and
   that it reproduces on the stock ML channel as well. Use `N >= 2`. See
   [`errors.md`](errors.md).
2. **`numGenerations >= N`.** A switch beyond the last generation never happens,
   which is the single-objective case from section 12 — legitimate, but say so
   explicitly rather than leaving it implied.
3. **The property must be recorded before the switch too.** Confirm the pre-switch
   generations populate the property column, otherwise the warm-up is not
   exercising the producer and the Pareto phase will discover it later, at scale.
4. **One slot per active objective.** A `1201 1202` pair needs two real slot
   files; a third `1203` that resolves to a constant fallback adds a constant
   objective, which distorts the front without contributing information.
5. **Units and directions go in the project record**, per slot, before launch.

Validation on a reduced pilot, before production:

```bash
python scripts/uspex_status.py RUN --stale-minutes 30
```

and then, from the run's own artifacts: a populated `MLProperties` with the
expected number of columns, a `ParetoRankingInGeneration` whose `Fitness` moves
with the property, and at least one structure whose slot files were recomputed
independently from its relaxed geometry. For a custom-property adapter on mock
slot files, the dedicated smoke test is
[`../scripts/smoke_legacy105_custom.py`](../scripts/smoke_legacy105_custom.py).
Run it with no arguments to exercise its own reference adapter, and with
`--adapter <path>` to exercise the project's real adapter — including the shipped
template in
[`../assets/legacy105/calcElasticML_adapter_template.py`](../assets/legacy105/calcElasticML_adapter_template.py).
That harness also answers the staleness question of the exchange-file-contract
section:
`--require-fresh` turns slot files older than `geom.in` from a warning into a
failure. Treat a missing smoke test as "write the equivalent check", not as a
reason to skip the layer.

## 14. Failure handling

The rule the whole interface rests on: **an infrastructure failure must never
become a value.** Every mechanism below exists to keep that true.

The table describes the **teaching patch**. The shipped template
[`../assets/legacy105/userCalcFitness_patch_template.py`](../assets/legacy105/userCalcFitness_patch_template.py)
and the reference adapter
[`../assets/legacy105/calcElasticML_adapter_template.py`](../assets/legacy105/calcElasticML_adapter_template.py)
close several "not detected today" rows: both refuse a non-finite value, the
callback exits non-zero with no `<CALLRESULT>` line when `energy.txt` cannot be
parsed (keeping the token) or holds a non-finite number, and the adapter treats
every malformed slot as fatal. Install them and those rows become "detected",
which is the point of the hardening section.

| Failure | Correct behavior | Never |
|---|---|---|
| Producer cannot import a dependency | exit non-zero; leave sentinels; stage log shows the traceback | write a plausible number, or write nothing and let a stale file answer |
| Producer crashes mid-extraction | sentinels remain; `flag == 0` may still report done if the artifacts exist | rely on `flag == 0` to detect the crash — it checks existence and size only |
| `energy.txt` missing at `flag == 1` | the patched callback raises (`FileNotFoundError`) and the structure is retried or fails loudly | silently return `0`, `5555`, or a random value |
| `energy.txt` holds several lines | **not detected today**; only the first line is used, even by the shipped template | assume the file is well-formed because it parsed |
| `energy.txt` holds `nan`/`inf` | refused by the shipped template: non-zero exit, no `<CALLRESULT>`; the teaching patch forwards it | let it into the ranking |
| `energy.txt` is unreadable or unparseable | **not today** — the delete sits after the parse, so the file survives a read failure and the exception propagates; the shipped template prints a diagnostic and exits non-zero with the token kept | swallow the exception and return a placeholder |
| A slot file is missing | **not detected today**; the stage-2 adapter logs a warning and returns `0.0` (both this guide's reference adapter and the shipped template refuse instead) | treat `0.0` as "missing" — it is a legal value, and often a winning one for a minimization objective |
| A slot value is non-finite or multi-token | **not detected today** in the teaching adapter; the shipped template exits non-zero without a `<CALLRESULT>` line | treat a caught exception as a value |
| A stale slot file survives a `CalcFold*` recycle | **not detected today**; the smoke test can flag it (`--require-fresh`) | assume file mtime proves ownership |
| The job died in the queue (walltime, OOM, bad module) | the run shows a failed structure; the artifacts are missing or sentinel | accept any scheduler outcome as a scientific result |
| The producer is not on `PATH` in the job script | the stage fails; the log shows a shell error | assume a command that works interactively works under the job script |
| The wrong `calcElasticML.py` is active (an unpatched tree, or a second installation) | the objective is the stock bulk modulus; `MLProperties` shows a modulus-scale number | debug the input file instead of the installation |

Additional rules worth stating explicitly in a project record:

- **Completion is not convergence.** The patched `flag == 0` answers "is this
  calculation finished", not "is the geometry converged". A producer that
  converges nothing and simply writes the input geometry back will be reported as
  complete, and the property of the unrelaxed cell will be optimized. The
  materials' own producers implement an explicit convergence test and write a
  penalty energy when it fails — copy that discipline, not just the file names.
- **A penalty is a scientific statement.** Use one only for a candidate that is
  invalid by the project's definition (outside the model's domain, missing a
  required species, non-converged but still meaningful). Document it, make it
  finite, and make it lose for that slot's direction.
- **Never an apparent success.** A producer that cannot produce a value must
  leave a state that the rest of the chain can tell apart from a real one: a
  non-zero exit, a sentinel outside the physical range, and a diagnostic line.

## 15. Testing

Seven layers. Do not skip a layer, and do not merge two layers into one run: each
layer localizes a different class of defect.

| Layer | What it is | Concrete pass criterion |
|---|---|---|
| **L1** | Python syntax and import of both patched installation files | `python -m py_compile userCalcFitness.py calcElasticML.py` exits 0 for both; no `NameError` can remain hidden in an unvisited branch — read those branches by eye |
| **L2** | Adapter behavior on mock slot files | every declared slot file present → one `<CALLRESULT>` line with exactly seven finite values in the fixed order; a finite negative value is **accepted** (a documented penalty is a legitimate value); each misconfiguration (missing file, empty file, two tokens, `nan`, `inf`, non-numeric) → non-zero exit and **no** `<CALLRESULT>` line, unless that slot was explicitly declared optional; runner: `python scripts/smoke_legacy105_custom.py` (its own reference adapter) or `python scripts/smoke_legacy105_custom.py --adapter <ABSOLUTE path to the project adapter> --allow-skip` |
| **L3** | `userCalcFitness.py` flags `0` and `1` in a scratch directory | write both artifacts → `flag 0` prints `1`; remove either → `flag 0` prints `0`; a non-empty `energy.txt` containing one finite number → `flag 1` prints that number and the file is gone afterwards; a second `flag 1` in the same directory fails rather than repeating the value |
| **L4** | one real structure through the real producer + adapter in a throwaway directory | `geom.out` parses as a structure, `energy.txt` holds one finite plausible number, every declared slot file holds one finite number, the adapter's seven values match a manual read of the slot files, and the producer's exit status is 0 |
| **L5** | one structure through a real USPEX invocation (one generation, `numParallelCalcs` small) | the structure directory contains the expected artifacts; `flag 0`/`flag 1` answered by the patched file; no `ERROR-OUTCAR-Gen*-Ind*-Step*` for that structure; the run reaches the next generation instead of retrying forever |
| **L6** | reduced NSF/pilot search — tiny population, `numParallelCalcs` 1–2, few generations | `results*/MLProperties` is populated with the expected number of columns; `ParetoRankingInGeneration` moves with the property; the pre-switch generations already record it; at least one non-penalty structure exercised the real property path; the submitted job script (not the interactive shell) ran the producer |
| **L7** | production | all of L6 holds at production scale; the provenance record (section 19) is complete and stored with the run |

Commands used at the earlier layers:

```bash
# L1 — both installation files
python -m py_compile userCalcFitness.py calcElasticML.py

# L2 — adapter contract on mock slot files
python scripts/smoke_legacy105_custom.py
python scripts/smoke_legacy105_custom.py --adapter "<absolute path to the project adapter>" --allow-skip

# L3 — callback flags, in a scratch directory with a fake geom.out and energy.txt.
# Start from the shipped template rather than the stock file:
#   assets/legacy105/userCalcFitness_patch_template.py
python userCalcFitness.py 0 1     # both artifacts present -> 1 ; either absent -> 0
python userCalcFitness.py 1 1     # the number, and energy.txt is now gone
python userCalcFitness.py 1 1     # must NOT repeat the value: the file was consumed

# L4 — one structure, real producer then adapter
python <producer>.py
python calcElasticML.py geom.out
```

**recommended hardening**: carry the L2 and L3 transcripts into the provenance
record verbatim. They are the only evidence that the interface behaved as
intended at the moment it was installed.

## 16. Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| The objective equals a stock elastic modulus | the stock `calcElasticML.py` is active: the running executable belongs to an unpatched tree, or to a different installation than the one that was patched | hash both patch-point files in the tree the executable was installed from and compare with the recorded hashes; check `USPEXPATH` and the banner; re-run L2 |
| `MLProperties` is filled but the objective never moves | the adapter reads a slot no objective selects, or several objectives read the same constant fallback, or the switch generation never arrives | compare the `optType` list to the slot order; confirm each active slot is a real file with a varying value; check `[N]` against `numGenerations` |
| The property is always `0` | missing or unreadable slot file hitting the adapter's silent `0.0` default; or the producer fails before writing and nobody notices because the file exists from an earlier structure | check the stage log for the adapter's stderr warning; verify the sentinel-first write; replace the default with a hard failure |
| The property is always a constant non-zero | the adapter is reading constants instead of files (a stage-1-style adapter left in place), or the real files are never updated | inspect the adapter source for constant fallbacks; confirm each slot file's content changes between structures |
| `energy.txt` missing at `flag == 1` | `flag == 1` already ran for this directory (the file is deleted on read); a second consumer asked again; or the producer never wrote it | decide who owns the energy: re-create it from a preserved `energy.txt.backup` before the consumer that needs it, or stop consuming it early |
| A stale property is reused after a `CalcFold*` recycle | no freshness check anywhere; the old slot file survived | sentinel-write at producer start; add a per-structure provenance sidecar and refuse a mismatch; do not delete USPEX-managed files to fix this |
| Two installations are involved and the wrong one wins | the executable, the patch points and the helper tree were resolved from different places, or a module load in the job script changed which launcher is found | resolve the executable and both patch-point files by absolute path, hash them, and record the hashes; make the job script set the environment explicitly |
| The producer works by hand but fails under the job script | modules, environment, interpreter, or `cwd` differ; the command used a relative interpreter path | run the exact `commandExecutable` line inside the directory the job script creates; use an absolute interpreter path; see [`submission.md`](submission.md) |
| The search retries the same structure forever | `flag == 0` never returns `1`: the producer writes only one of `geom.out`/`energy.txt`, or writes it to the wrong directory | compare the producer's output paths with the directory USPEX runs the stage in; test both artifacts by hand in that directory; see [`errors.md`](errors.md) |
| USPEX finishes generation 1 with `Undefined function or variable 'multiFit'` | switch generation set to `1` | use `[N]` with `N >= 2`; this reproduces on the stock channel too |
| `MLProperties` first column is right but the Pareto front ignores the property | the switch generation, or the front is being read from the wrong file | read `ParetoRankingInGeneration` rather than `Individuals`; re-check `[N]`; see [`validation-analysis.md`](validation-analysis.md) |
| The adapter's numbers are right but the search optimizes the wrong direction | the direction is inherited from the stock `12**` semantics (all maximization) and a smaller-is-better slot was written as measured without stating the convention | write the project's direction per slot into the property record; verify with a two-structure hand test; run one objective slot at a time until the direction is proven |
| Column headings do not describe the content | expected: the first `MLProperties` column is still headed `Modulus:Bulk` | document the remap wherever the results are read; never publish an `MLProperties` column as a modulus without checking the installation |

## 17. Recommended hardening

Every item below is **recommended hardening** — none of it is present in the
provided files. Each names the failure it removes.

**Adapter (patch point 2)**

The shipped template
[`../assets/legacy105/calcElasticML_adapter_template.py`](../assets/legacy105/calcElasticML_adapter_template.py)
and the section-8 listing implement items 1-6; use one of them rather than writing
a new adapter from scratch.

1. No silent default. A missing/empty/unparseable slot file fails the adapter
   unless the operator declared that slot optional with an explicit value.
   *Removes:* an implicit `0.0` becoming the best possible value for a
   minimization objective.
2. Validate every slot: one token, finite float, inside the project's physical
   range. *Removes:* `nan`/`inf`/concatenated values entering the ranking.
3. Require `len(sys.argv) >= 2`, matching the usage string the adapter already
   prints, and accept (ignore) the reserved second argument.
   *Removes:* an `IndexError` traceback being mistaken for a missing-property
   warning.
4. Log the resolved slot directory on every invocation.
   *Removes:* silent reads from a collapsed working directory.
5. Accept a working-directory fallback for a bare filename argument only when that
   directory really holds the structure file; otherwise abort. This guide's
   reference adapter (section 8) does this. *Removes:* scoring slot files that
   belong to some other directory.
6. Exit non-zero when any required slot fails.
   *Removes:* USPEX scoring a structure whose property was never computed.

**Callback (patch point 1)**

7. `flag == 1` parses instead of reads: exactly one token, finite, plausible.
8. Make the delete-after-parse ordering explicit and tested. The patch already
   deletes only after the value parsed, but nothing in the file says that is
   intentional, so a later edit can invert it without noticing.
9. `flag == 0` checks that the producer did not report failure, and validates the
   format of `geom.out` instead of only its size.
10. Keep a producer-side `energy.txt.backup` and re-create `energy.txt` before any
    consumer that needs it. (The project's own producer already does this part —
    the hardening is to make it a requirement rather than one producer's habit.)

**Producer**

11. Sentinel-write every output before extraction starts; write real values only
    after all extraction succeeded; atomic rename into place.
12. Direction-aware, finite, documented penalties. A penalty that can win is a bug.
13. Non-zero exit and a diagnostic line on every infrastructure failure.
14. Never delete a slot file to signal failure.

**Provenance and installation**

15. Hash the two patch-point files as installed, and check the hashes before a
    production launch. *Removes:* the wrong-install failure mode.
16. Record the patched tree's location in the run record, and keep the patched tree
    isolated so it cannot be silently replaced by an upgrade.
17. Ask the user for the USPEX-side parser source and confirm the `12**` → column
    mapping against it (section 10).
18. Add the freshness/provenance sidecar described in section 9.
19. Extend the smoke test to assert the *absence* of the silent-zero path, so a
    future edit cannot reintroduce it.

**Operational**

20. Run the L2/L3 checks with the interpreter that will actually run in the job
    script, not the interactive one.
21. Keep the pilot's `numParallelCalcs` small until one full generation has been
    inspected by hand.

## 18. Migration to USPEX 10.6

Keep only the science; re-implement it through the native callback.

1. **Extract the science from the producer**, as a standalone stage that reads the
   final relaxed structure: the raw property definition, its units, its direction,
   the invalid-candidate penalty, which relaxed geometry is evaluated, and the
   optimizer/model versions and checkpoints.
2. **Implement it in `Specific/user_property_calculator.py`** with flags
   `0`/`1`/`101`/`102` as described in [`custom-objectives.md`](custom-objectives.md).
   Flags `0` and `1` keep the code-99 completion/enthalpy role; `101`/`102` return
   the raw `x`/`y` values the project used to place in slots.
3. **Do not carry the plumbing.** No `P1.txt`…`P7.txt`, no `1201` remap, no
   replaced `calcElasticML.py`, no patched `userCalcFitness.py`, and no
   `geom.out`/`energy.txt`-between-stages handshake. In 10.6 the
   relaxation and the property evaluation are one stage with one wrapper
   ([`custom-objectives.md`](custom-objectives.md)); the numbered-file convention
   and the two-patch architecture are 10.5 artifacts.
4. **Declare direction explicitly** in `optType` (`1 max_x`, `1 min_x`) and return
   the raw measured value. Verify the convention on the installed build — a bare
   `x`/`y` is not a safe spelling there.
5. **Validate against the old workflow**: score several known structures with both
   implementations and require agreement before trusting the new search. Where
   they disagree, the difference is usually a stage boundary or a file
   normalisation, not the model.
6. **Keep the 10.5 tree frozen** until the 10.6 run has produced results you
   accept; the two interfaces must never be mixed in one run.

## 19. Provenance record

Record this for every patched installation, in the run directory, not only in a
chat log. Fill in the template:

```text
USPEX legacy custom-property interface — provenance record
==========================================================
Date:
Operator:
Purpose (search system, objectives):

Installation
  USPEX release from banner:
  Installation root:
  Patched tree location:
  Launcher on PATH (resolved absolute path):

Patch point 1 — userCalcFitness.py
  Original path:
  Original SHA-256:
  Patched  path:
  Patched  SHA-256:
  Lines changed (cite the diff):

Patch point 2 — calcElasticML.py
  Original path:
  Original SHA-256:
  Patched  path:
  Patched  SHA-256:
  Lines changed (cite the diff):

Exchange-file contract
  energy.txt        : one finite number, eV/cell (units per project)   consumed by flag == 1, then deleted
  geom.out          : relaxed structure                                required by flag == 0
  P1.txt ... P7.txt : one raw finite number per declared slot          read by the adapter in order

Property map
  slot | property name | units | direction | raw value written | penalty (finite) | selected by
  P1   |               |       |           |                   |                  |
  ...  |               |       |           |                   |                  |

Objectives
  optType block as authored:
  Enthalpy still objective 1: yes/no
  Pareto switch generation [N]:
  numGenerations:

Producer
  Path:                       Interpreter (absolute path):
  Version/commit:             Model checkpoint (if any) + SHA-256:
  Converged-candidate test:   Penalty value and direction:
  Sentinel values written before extraction:

Parser confirmation
  Source file consulted for the <CALLRESULT> parse and the 12** column mapping:
  Mapping confirmed by (source / hand test):        Date:

Validation transcript
  L1 py_compile:
  L2 adapter smoke test (script, version, command, result):
  L3 flag 0 / flag 1:
  L4 one structure + real calculator:
  L5 one USPEX structure:
  L6 pilot (population, numParallelCalcs, generations, evidence files):
  L7 production launch date:

Attachments
  diff of both patch points; smoke-test output; parameters.json / INPUT.txt copy
  from the run; results*/MLProperties header; results*/ParetoRankingInGeneration
```

## 20. File contract

Roles: **USPEX** = the driver; **stage** = the engine stage that runs before the
producer; **producer** = the project script named by the last
`commandExecutable` line; **callback** = the patched `userCalcFitness.py`;
**adapter** = the patched `calcElasticML.py`.

| File | Producer | Consumer | Purpose | Ordering | Required |
|---|---|---|---|---|---|
| `geom.in` | USPEX | stage, producer | POSCAR of the generated structure; the stage's input | written before the first stage runs | yes |
| `CONTCAR_*` / engine outputs | the engine stages | producer | the relaxed geometry and energy the producer reads | after the engine stages, before the producer | yes, by producer design |
| `geom.out` | producer | callback (`flag == 0`) | relaxed POSCAR; existence + non-empty size **is** the completion signal | after the producer, before `flag == 0` | yes |
| `energy.txt` | producer | callback (`flag == 1`) | exactly one number; the code-99 enthalpy | written by the producer; read once by `flag == 1`, which then deletes it | yes |
| `energy.txt.backup` | producer | producer (on a later pass) | preserved copy so a second consumer can re-create the energy | written with `energy.txt`; restored before the next consumer needs it | project-defined |
| `P1.txt` … `P7.txt` | producer | adapter | one raw finite scalar per property slot | written after **all** extraction succeeded; read after `flag == 0` | one per active objective |
| `MLProperties` | USPEX | operator, analysis | per-generation property columns; column *i* carries the *i*-th `<CALLRESULT>` value; column 1 is still headed `Modulus:Bulk` | written after the property stage, per generation | yes, for `optType 12**` |
| `ParetoRankingInGeneration` | USPEX | operator, analysis | the ranking actually used, with the objective directions applied | written at the end of each generation | yes, for a multi-objective run |
| `INCAR_*` (VASP stages) | operator | engine stages | per-stage VASP settings; the last stage must produce whatever the producer reads | in place before the stage runs | yes, one per VASP stage |
| `Submission/submitJob_local.py` (+ status pair) | operator | USPEX (`whichCluster 1`) | the job script that actually runs the stage command | resolved before the first submission | yes with a scheduler |
| stage log (`log`, engine stdout/stderr) | job script / engine | operator | the only place a stage failure is visible | per stage | yes for diagnosis |

**verified legacy behavior** for the file names, the roles of `geom.in`,
`geom.out`, `energy.txt`, the slot files, and the `<CALLRESULT>` line. The
completion semantics of each row are the patched installation's convention, not a
documented USPEX 10.5 standard. `MLProperties` contents beyond its first column
heading, and the exact `INCAR_*` requirements of a VASP chain, are outside this
interface — see [`running.md`](running.md) and
[`validation-analysis.md`](validation-analysis.md).

## 21. Related

| Topic | Where |
|---|---|
| 10.6 native `x`/`y` custom objectives | [`custom-objectives.md`](custom-objectives.md) |
| Calculation types, `INPUT.txt` authoring, VASP stage chains | [`running.md`](running.md) |
| Symptom → cause → fix, including the `multiFit` switch defect | [`errors.md`](errors.md) |
| Direction conventions, Pareto analysis, ranking files | [`validation-analysis.md`](validation-analysis.md) |
| Scheduler callbacks and the job-script contract | [`submission.md`](submission.md) |
| Mock-slot smoke test for the seven-value adapter contract | [`../scripts/smoke_legacy105_custom.py`](../scripts/smoke_legacy105_custom.py) |
| Copy-paste adapter, callback patch and `INPUT.txt` skeleton | [`../assets/legacy105/`](../assets/legacy105/README.md) |
