# Example policy

This skill does not duplicate the large example archive or licensed VASP inputs.
Use the examples bundled with the exact executable being run:

```bash
USPEX -e          # list examples and descriptions
USPEX -c 1        # copy one example into the current clean directory
```

Useful starting points in the 10.5/10.6 distribution include:

| Goal | Official example |
|---|---|
| 3D fixed-composition VASP | EX01 (Si) |
| 2D fixed-composition VASP | EX07 (Si) |
| 3D ternary variable composition | EX18 (Zn-O-H, GULP) |
| Variable-composition surface reconstruction | EX19 (B(111)) |
| Fixed-composition surface reconstruction | EX23 (MgO) |
| 2D variable composition | EX31 (Sn-S) |
| Pareto optimization | EX34 |
| USPEX 10.5 ML elastic properties | EX35 |
| 3D variable composition with Quantum ESPRESSO | EX36 (Na-H) |
| USPEX 10.6 arbitrary property `x`/`y` | EX37 |
| USPEX 10.6 arbitrary external code (`99`) | EX38 |

Example numbering is release-specific. Confirm the description printed by
`USPEX -e` before copying. Treat every copied example as a structural template:
replace chemistry, composition blocks, pressure, pseudopotentials, convergence
settings, executable, and scheduler resources deliberately.
