# Bounded symmetric Y-junction qualification runbook

Status: **prospective contract frozen before first OpenFOAM execution**

This runbook governs one FlowLab-generated true-3D path: a 6 mm circular inlet
and two identical 6 mm circular branches at `+30` and `-30` degrees. The inlet
and both branches are 27 mm long. The flow is steady, incompressible, laminar,
and nominally `Re=100`. OpenFOAM 11 is the first and only solver in scope.

The machine-readable authority is `QUALIFICATION_CONTRACT_V1.json`. Its gates
must not be weakened, reinterpreted, or tuned after any retained solver result
is observed.

## Claim boundary

A complete pass is software and numerical qualification of this exact
generated path. It is not independent experimental validation, general
branching-network support, arbitrary CAD or geometry validation, product
promotion, or external release. `promotionAuthorized` remains `false`.

## Geometry and ownership

The generated solver mesh is the deterministic union of three prospectively
declared circular primitives, realized as uniform Cartesian hexahedra. The
circular walls are therefore staircase approximations whose realization
refines with the solution.

Cell order is part of the generated artifact contract:

1. inlet-edge cells;
2. upper-branch edge cells;
3. lower-branch edge cells; and
4. generated junction cells.

Only the first three contiguous ranges may select schematic edges. Junction
cells carry the dedicated generated identity
`generated:y-junction:junction-core:v1` and have no schematic owner. The
frontend must fail closed for them. No consumer may infer ownership from cell
coordinates or displayed geometry.

## Frozen cases

| Case | Cell size | Outlet pressures |
| --- | ---: | --- |
| coarse | 1.125 mm | equal |
| medium | 0.750 mm | equal |
| fine | 0.500 mm | equal |
| fine negative control | 0.500 mm | lower outlet is 0.2 Pa lower |

The three equal-pressure cases use uniform `r=1.5`. The primary QoI is inlet
patch-average pressure minus the mean outlet patch-average pressure, in Pa.

## Pre-execution gate

Before a retained run:

1. commit the contract, runbook, generator, adapter integration, evaluator, and
   focused tests;
2. require every frozen source path to be clean at that exact commit;
3. resolve the configured OpenFOAM image tag to an immutable image ID;
4. materialize every case twice and require identical complete file hashes;
5. verify the generated mesh declares exactly three edge ranges and a separate
   unowned junction artifact;
6. use a new absent or empty ignored campaign directory; and
7. stop if the topology is not one face-connected fluid region or if ownership
   would require geometric reconstruction.

## Execution

From the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.y_junction_campaign \
  benchmarks/cases/y-junction/campaigns/2026-07-31-v1-r1 \
  --run-and-package
```

The runner executes coarse, medium, and fine equal-pressure cases in order and
stops before finer work if a mandatory per-level gate fails. It runs the
asymmetric fine negative control only after the equal-pressure fine case is
captured. Every case retains `checkMesh`, solver, function-object, VTK, source,
runtime, and hash evidence.

## Decision and retention

Every contract gate is mandatory. A mathematically invalid GCI fails. An
infrastructure failure is recorded separately from a scientific failure. Raw
campaigns remain ignored and immutable once execution begins.

Passing automated gates is not independent review. Any future promotion would
require a separately governed reviewer bound to the exact immutable package
digest, and this contract does not authorize that promotion.
