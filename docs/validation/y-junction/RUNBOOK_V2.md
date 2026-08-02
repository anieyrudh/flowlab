# Bounded symmetric Y-junction qualification runbook V2

Status: **prospective contract frozen before first V2 OpenFOAM execution**

This runbook governs the same exact geometry, physics, mesh family, ownership
contract, cases, gates, and claim boundary as V1. Its machine-readable
authority is `QUALIFICATION_CONTRACT_V2.json`.

V1 remains retained and immutable. Its fine-grid velocity-symmetry failure was
caused by all three declared probe coordinates lying exactly on fine-grid
Cartesian cell faces while the default `cell` sampler selected different
adjacent cells in the mirrored branches. The global equal-pressure flow split,
pressure symmetry, and branch pressure-drop gates passed, but V1 remains a
failed experiment.

V2 changes only the observation operator: the same fixed mirrored coordinates
are sampled with OpenFOAM `cellPoint` interpolation. This choice is frozen
before any V2 solver execution. No threshold, geometry, physics value, solver
control, ownership rule, or promotion boundary is changed.

## Pre-execution gate

Before a retained V2 run:

1. commit the V2 contract, this runbook, generator, adapter integration,
   evaluator, and focused tests;
2. require every frozen source path to be clean at that exact commit;
3. resolve the OpenFOAM image tag to an immutable image ID;
4. materialize every case twice and require identical complete file hashes;
5. verify the generated mesh declares exactly three edge ranges and a separate
   unowned junction artifact;
6. verify the generated profile declares fixed `cellPoint` probe sampling at
   stations 10, 16, and 22 mm; and
7. use a new absent or empty ignored campaign directory.

## Execution

From the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.y_junction_campaign \
  benchmarks/cases/y-junction/campaigns/2026-07-31-v2-r1 \
  --run-and-package
```

The runner executes coarse, medium, and fine equal-pressure cases in order,
then the fine asymmetric-pressure negative control. Every mandatory gate must
pass. Raw campaign directories are immutable after execution starts.

## Claim boundary

A complete pass is software and numerical qualification of this exact
generated path only. It is not independent empirical validation, arbitrary
network support, product promotion, or external release.
`promotionAuthorized` remains `false`.
