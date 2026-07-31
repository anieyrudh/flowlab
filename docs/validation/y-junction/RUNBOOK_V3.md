# Bounded symmetric Y-junction qualification runbook V3

Status: **prospective contract frozen before first V3 OpenFOAM execution**

The machine-readable authority is `QUALIFICATION_CONTRACT_V3.json`. V1 and V2
campaigns remain retained and immutable.

V2 passed every per-case mesh, solver, conservation, symmetry, branch-drop,
and negative-control gate. Its primary pressure-drop sequence was non-monotone,
so observed order and GCI failed closed. V3 tests a new prospective hypothesis:
the Cartesian staircase geometry and solution mesh use a nested `r=2` family
with 4, 8, and 16 cells across the 6 mm diameter.

V3 changes no physics, solver setting, measurement coordinate, interpolation
operator, threshold, ownership rule, or claim boundary. The desktop
coarse/medium/fine controls select the same 1.5/0.75/0.375 mm nested family.

## Pre-execution gate

Before a retained V3 run:

1. commit this contract, runbook, product defaults, generator, adapter,
   evaluator, and focused tests;
2. require every frozen path to be clean at the exact commit;
3. resolve the OpenFOAM image to an immutable ID;
4. materialize every case twice and require identical generated-file hashes;
5. require one connected all-hex region, exactly three ports, positive cell
   volumes, explicit edge ranges, and the unowned generated junction identity;
6. require fixed `cellPoint` sampling at the declared paired coordinates; and
7. use a new absent or empty ignored campaign directory.

## Execution

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.y_junction_campaign \
  benchmarks/cases/y-junction/campaigns/2026-07-31-v3-r1 \
  --run-and-package
```

The runner stops at the first failed mandatory per-case gate. It executes the
fine asymmetric-pressure control only after all equal-pressure cases pass.
Every retained directory is immutable after execution starts.

## Claim boundary

A complete pass is software and numerical qualification of this exact bounded
generated path. It is not independent empirical validation, arbitrary network
support, product promotion, or external release. `promotionAuthorized` remains
`false`.
