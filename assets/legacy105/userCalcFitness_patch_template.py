#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reference patch for the ``code 99`` user-code stage of a USPEX 10.5 installation.

Background
----------
Stock USPEX 10.5 does not know how to run an arbitrary external program. It runs the
user-code stage only when the installation contains a usable

    <USPEX>/application/archive/src/FunctionFolder/Tool/userCalcFitness.py

and ``INPUT.txt`` lists ``99`` as the last entry of ``% abinitioCode``. USPEX then calls
that file as::

    python userCalcFitness.py <flag> <Step>

with the calculation directory as the working directory, and expects exactly one line

    <CALLRESULT> <scalar>

on stdout. The shipped file is a template: ``flag 0`` answers "1" unconditionally and
``flag 1`` answers the placeholder ``5555``.

What this template changes
--------------------------
The two branches that matter for this interface:

  * ``flag == 0`` (done / converged?) -- answer 1 only when the stage really produced a
    relaxed structure and an energy, i.e. both artifacts exist and are non-empty;
  * ``flag == 1`` (energy / enthalpy) -- read the first line of ``energy.txt`` as a float
    and then remove the file.

The ``flag == 6`` branch and its undefined ``elasticMatrix`` reference are left exactly
as the stock template had them. That branch, and the rest of the stock flag vocabulary
(pressure tensor, dielectric constant, forces, ...), belong to other workflow types and
are out of scope here; leaving them alone is what keeps the diff small and reviewable.
If your project needs one of them, implement it deliberately and record it in the
provenance record.

One further deviation: the unknown-flag branch of the stock template (and of the
teaching patch) returns ``random.random()``, which is useless noise for a property
interface. This template returns a constant ``5555`` instead -- the same placeholder the
stock ``flag 1`` branch uses. That branch is inert for this workflow, but the change is
deliberate, not inherited.

Three deliberate deviations from the teaching patch, documented here so nobody has to
reverse-engineer them:

  1. the value must be **finite**. The teaching patch forwards ``nan``/``inf``
     unchanged, which then travels into the ranking. A non-finite energy is an
     infrastructure failure, so this template reports it and exits non-zero instead.
  2. a parse failure exits non-zero with a diagnostic rather than raising a bare
     traceback. It never becomes a value, and it never becomes a placeholder.
  3. the unknown-flag branch returns the constant ``5555``, not ``random.random()``
     (above).

Deviations 1 and 2 consume the token on the *first* bad read: that is safe, because the
value is removed only after a successful parse or a deliberately invalid one, and USPEX
re-runs the stage for that structure. See references/custom-objectives-105.md.

The single-use handshake
------------------------
The removal in ``flag 1`` is the point of the design. ``energy.txt`` is a one-shot token:
because it is gone as soon as it has been read, a later structure evaluated in the same
reused ``CalcFold*`` directory cannot silently re-report the previous structure's energy.
If another consumer still needs the value, have the producer keep its own copy under a
different name and restore it -- do not remove the removal.

Read that reference before installing this: the arithmetic is small, but the file
lifecycle, the call ordering, and the failure semantics are where a patched run goes
wrong.

Installing
----------
1. Back up the shipped file and record its SHA-256.
2. Install this file in its place (keep the executable interpreter path correct).
3. Validate in layers: ``python -m py_compile``, then a scratch-directory test of
   ``flag 0`` and ``flag 1``, then one real structure, then a small USPEX pilot.
"""

from __future__ import annotations

import math
import os
import sys

FLAG_DONE = 0
FLAG_ENERGY = 1

GEOM_OUT = "geom.out"
ENERGY_TXT = "energy.txt"

EXIT_CONTRACT = 1
EXIT_USAGE = 2


class ContractError(RuntimeError):
    """The stage produced something USPEX must not receive as an energy."""


def has_content(path: str) -> bool:
    """True when the path is an existing, non-empty regular file.

    Ordering consequence: ``flag 1`` removes ``energy.txt``, so ``flag 0`` can only
    answer "done" while the handshake token is still present. Confirm against your
    patched tree that USPEX asks ``flag 0`` before ``flag 1``; a build that re-asks
    ``flag 0`` afterwards would see the stage as never complete.
    """
    return os.path.isfile(path) and os.path.getsize(path) > 0


def stage_complete() -> int:
    """flag 0: did this stage produce everything the following flags will need?"""
    return 1 if has_content(GEOM_OUT) and has_content(ENERGY_TXT) else 0


def read_and_consume_energy() -> float:
    """flag 1: return the stage energy once, then remove the handshake token.

    The removal follows a successful parse, so a read or parse failure leaves the file
    in place for inspection. A non-finite value still consumes the token on purpose:
    the calculation is broken either way and the next attempt rewrites it.
    """
    with open(ENERGY_TXT, "r") as handle:
        line = handle.readline()
    try:
        value = float(line.strip())
    except ValueError:
        raise ContractError(
            f"{ENERGY_TXT} did not contain a number: {line.strip()!r}; "
            "refusing to report an energy"
        ) from None
    os.remove(ENERGY_TXT)
    if not math.isfinite(value):
        raise ContractError(
            f"{ENERGY_TXT} contained a non-finite energy ({value!r}); "
            "refusing to report it"
        )
    return value


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: userCalcFitness.py <flag> <Step>", file=sys.stderr)
        return EXIT_USAGE
    flag = int(argv[1])
    # Step is part of the calling convention; this patch does not use it.
    _step = int(argv[2])

    try:
        if flag == FLAG_DONE:
            target = stage_complete()
        elif flag == FLAG_ENERGY:
            target = read_and_consume_energy()
        elif flag == 6:
            # Stock branch, kept untouched: the stock template expects a 6x6 matrix
            # that some other workflow supplies. Left undefined on purpose.
            target = elasticMatrix  # noqa: F821
        else:
            # Deliberate deviation: the stock branch returns random.random(). A
            # constant placeholder is safer for a property interface; see the module
            # docstring, deviation 3.
            target = 5555
    except ContractError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("No <CALLRESULT> is emitted: an infrastructure failure must never "
              "reach the search as a value.", file=sys.stderr)
        return EXIT_CONTRACT

    print("<CALLRESULT> " + str(target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
