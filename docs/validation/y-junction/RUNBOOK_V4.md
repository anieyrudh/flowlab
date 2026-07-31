# Bounded symmetric Y-junction qualification runbook V4

Status: **prospective contract frozen before first V4 OpenFOAM execution**

The machine-readable authority is `QUALIFICATION_CONTRACT_V4.json`. All earlier
campaigns remain retained and immutable.

V3 passed every per-case mesh, solver, conservation, symmetry, branch-drop,
and negative-control gate. Its nested 4/8/16-cell family still had increasing
pressure-drop differences and negative observed order. V4 moves the same
unchanged `r=2` experiment to 8, 16, and 32 cells across the diameter.

The three cell sizes are 0.75, 0.375, and 0.1875 mm. Geometry, flow physics,
solver controls, measurement coordinates, `cellPoint` interpolation, every
threshold, ownership, and the claim boundary are unchanged.

## Pre-execution gate

Before a retained V4 run:

1. commit this contract, runbook, matching desktop presets, generator,
   adapter, evaluator, diagnostics parser, and focused tests;
2. require every frozen path to be clean at the exact commit;
3. resolve the OpenFOAM image to an immutable ID;
4. materialize every case twice and require identical complete file hashes;
5. require one connected all-hex region, exactly three ports, positive cell
   volumes, explicit edge ranges, and the unowned generated junction identity;
6. require fixed `cellPoint` sampling at the declared paired coordinates; and
7. use a new absent or empty ignored campaign directory.

## Execution

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.y_junction_campaign \
  benchmarks/cases/y-junction/campaigns/2026-07-31-v4-r1 \
  --run-and-package
```

The runner executes three equal-pressure cases followed by the fine asymmetric
negative control. Every retained directory is immutable after execution
starts.

## Stop rule and claim boundary

If V4 does not produce observed order from 0.5 to 4 and fine-grid GCI no more
than 5%, retain the failure and stop. Do not change the gate.

A complete pass would be software and numerical qualification of this exact
bounded generated path. It would not be independent empirical validation,
arbitrary network support, product promotion, or external release.
`promotionAuthorized` remains `false`.
