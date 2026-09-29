# Custom objectives and external optimizers

> Load this when: defining `x`/`y`, using code `99`, connecting an ML potential,
> or migrating a USPEX 10.5 custom-property workflow to 10.6.

## Native USPEX 10.6 data flow

USPEX 10.6 separates structure relaxation from custom-property evaluation:

```text
USPEX generates a structure
  -> relaxation stages from abinitioCode / commandExecutable
  -> final relaxed structure + energy
  -> Specific/user_property_calculator.py
       flag 0: completion for a code-99 stage
       flag 1: energy/enthalpy for a code-99 stage
       flag 101: property x
       flag 102: property y
  -> Pareto selection and the next generation
```

The callback is the public interface for `x` and `y`. It may calculate a property
directly from the final relaxed structure or parse a project-owned result file. USPEX
does **not** require `P1.txt`, `P2.txt`, or any other numbered property file in 10.6.

The installed examples establish both supported styles:

- EX37 calculates `x` directly in `user_property_calculator.py` from
  `optimized.structure` after ordinary GULP relaxation stages.
- EX38 ends a mixed relaxation chain (`3 3 3 3 99`) with a user-code stage and has
  the callback parse that stage's output.

Therefore stage count is chosen by the relaxation workflow. It is not fixed by the
presence of a custom objective, and code 99 is not a synonym for “property stage.”

## Declare direction explicitly

```text
% optType
1 max_x
% EndOptType
```

`min_x`/`min_y` minimize and `max_x`/`max_y` maximize. On the tested 10.6 build,
a bare `x` or `y` aborts in `DetermineOptType`; use the explicit prefix.

Return the raw measured value. For a maximized `log10(D)`, return `-4.7`, not
`+4.7` and not an extra negation. USPEX stores an internally oriented copy in
`Individuals`, so a maximized raw value can appear sign-flipped there. Read the
ranking from `ParetoRankingInGeneration`/`Pareto_ranking` and verify the raw value
independently on known structures.

## Code 99 is the external relaxation contract

For a MatterSim relaxation stage:

```text
% abinitioCode
99
% ENDabinit

% commandExecutable
/absolute/path/to/python -u ./run_mattersim.py > engine.out 2>&1
% EndExecutable
```

The stage command runs in a `CalcFold*` directory. A typical external optimizer reads
`geom.in` and writes:

- `geom.out`: relaxed POSCAR;
- `energy.txt`: one finite energy/enthalpy in eV per cell;
- engine logs needed for diagnosis.

Use an absolute interpreter path. `Specific/userInput_<step>` is copied to
`usercode.in` when the external program needs stage metadata.

Multiple relaxation stages are legal. Provide one command per stage (or a single
reusable command only when the installed parser and homogeneous stages support it),
and do not assume a private file from one stage is automatically available in another.
If two external programs must run sequentially in the same code-99 stage, a wrapper may
call them in order, but that is a project design choice rather than a 10.6 requirement.

## Preferred geometry-derived objective

When `x` depends on the relaxed geometry, calculate it in the callback:

```python
def calculate_property_x(calc_path: str = "./") -> float:
    structure = load_final_relaxed_structure(calc_path)
    value = predict_x(structure)
    if not math.isfinite(value):
        raise ValueError("property x is not finite")
    return value
```

For built-in engines, use the final-structure artifact produced by that engine, as EX37
does with `optimized.structure`. For code 99, verify the installed release's timing.
On the audited 10.6 build, `geom.out` may be consumed before flag `101` runs. In that
case the code-99 producer should preserve a project-owned copy such as
`relaxed_for_property.vasp`; the callback reads that copy. The preserved filename is a
private bridge, not a USPEX standard.

### CalcFold reuse

CalcFold directories may be reused. Do not respond by deleting every file in the
directory: USPEX owns its state and removes or replaces what it manages.

Make producer-owned outputs unambiguous instead:

1. atomically replace the private relaxed-structure copy after a successful optimizer run;
2. atomically write a small manifest containing the SHA-256 of the current `geom.in`,
   the private relaxed file, its digest, and the parsed energy;
3. in flags `0`, `1`, and `101`, require the manifest's input digest to match the current
   `geom.in` and verify the referenced relaxed file/digest;
4. do not publish a new manifest when the optimizer fails.

An old manifest then cannot validate a new structure, even if the old private files remain.
This solves the reuse problem without deleting USPEX-managed outputs.

## Callback contract

USPEX invokes:

```text
python3 Specific/user_property_calculator.py FLAG STEP
```

with the calculation directory as the working directory. Print exactly:

```text
<CALLRESULT> <one finite scalar>
```

| Flag | Meaning | Good implementation |
|---|---|---|
| `0` | code-99 stage complete? | validate current-structure provenance, relaxed geometry, and energy; return `1` or `0` |
| `1` | energy/enthalpy | return the current finite scalar in eV/cell |
| `101` | property `x` | calculate or parse raw `x`; direction comes from `optType` |
| `102` | property `y` | calculate or parse raw `y`; reject it clearly when unconfigured |

Load expensive property dependencies only for the requested property flag. Flags `0` and
`1` should remain fast and should not import an ML/property stack they do not use.

## Penalties and failures

Use a finite, direction-aware penalty only for a scientifically defined invalid candidate,
such as no mobile species or a molecular/vacuum structure outside the model domain. For a
maximized `log10(D)`, a documented low value such as `-10` is appropriate.

Missing models, unreadable structures, digest mismatches, malformed outputs, failed imports,
and scheduler errors are infrastructure failures. Raise or return “not complete”; never turn
them into zero or into the scientific penalty.

## Acceptance tests

Before production:

1. Run one real mixed-composition structure through the exact code-99 command. Require a
   relaxed geometry, finite energy, and a current-input provenance match.
2. Call flags `0`, `1`, and `101` directly. Each call must emit exactly one
   `<CALLRESULT> <value>` line; `101` must agree with an independent property calculation.
3. If `geom.out` is consumed before `101`, run `scripts/smoke_code99.py` with
   `--consume-geom-out --preserved <private-relaxed-file>` and require the callback still
   to succeed.
4. Run a reduced scheduler-backed USPEX pilot (`numParallelCalcs = 1`, tiny population).
   Confirm `Individuals` has populated `Property_X`, the raw values appear in the ranking,
   and at least one non-penalty structure exercises the real model path.
5. Launch production only after the pilot proves submission, relaxation, callback, and
   selection end to end.

## Legacy USPEX 10.5

Stock 10.5 does not provide the native 10.6 `x`/`y` callback route. In stock 10.5,
`optType` values `1201`-`1207` are the official machine-learning elastic-property channels
(bulk/shear/Young modulus, Poisson's ratio, Pugh ratio, Vickers hardness, fracture
toughness), served by the third-party `FunctionFolder/calcElasticML.py`.

A private 10.5 installation may nevertheless be patched into a custom-property channel:
replace that calculator with an adapter that reads one numbered scalar file per slot and
prints **one** `<CALLRESULT>` line carrying the fixed-order property values, and patch
`FunctionFolder/Tool/userCalcFitness.py` to transport the `code 99` stage's
completion/enthalpy through `geom.out`/`energy.txt`. Structure the patch as two independent
patch points and keep the whole thing isolated and documented.

That is a private convention, not an official API. Read
[`custom-objectives-105.md`](custom-objectives-105.md) before touching a 10.5 tree: it gives
the verified patch, the slot-file contract, the `1201`-`1207` mapping evidence, the layered
validation ladder, and the provenance record. Confirm the mapping against the user's own
source before editing, and never carry the numbered-file convention into a 10.6 design.

For a migration, preserve only the science:

- which relaxed structure is evaluated;
- the raw property definition, units, and direction;
- the invalid-candidate penalty;
- the optimizer and model versions.

Implement the property through the 10.6 callback and validate it against the old workflow on
several known structures. Do not reproduce the 10.5 file plumbing unless a project explicitly
needs it as a private intermediate.
