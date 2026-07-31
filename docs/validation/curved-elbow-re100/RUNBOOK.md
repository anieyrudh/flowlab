# Canonical curved-elbow Re=100 qualification runbook

Status: **prospective v1 contract; no retained scientific result observed at freeze**

This runbook governs one bounded true-3D path: a constant-diameter circular
pipe with a 10D inlet leg, one exact 90-degree bend at `Rc/D=3`, a 10D outlet
leg, and steady incompressible laminar flow at `Re=100`. The machine-readable
authority is `QUALIFICATION_CONTRACT_V1.json`.

## Claim boundary

The campaign may establish only that this exact OpenFOAM product path passes
its frozen software, mesh, result, provenance, symmetry, conservation, and
three-grid pressure-loss gates. It is numerical qualification, not independent
experimental validation. It does not qualify arbitrary CAD, other elbows,
other Reynolds numbers, turbulence, transient, compressible, multiphase,
hybrid-mesh, SU2, promotion, or release.

Existing axisymmetric and straight full-O-grid results are implementation
precedents only. Their evidence is not inherited.

## Frozen topology and sequence

The mesh is a 15-block all-hex sweep: five conformal O-grid blocks through each
of an inlet leg, circular elbow, and outlet leg. The two component interfaces
share vertices and remain internal. No triangulated CAD or `snappyHexMesh`
stage is used.

| Level | Inlet | Bend | Outlet | Annular | Circumference | Core | Cells |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Coarse | 20 | 12 | 20 | 2 | 16 | 4 | 2,496 |
| Medium | 40 | 24 | 40 | 4 | 32 | 8 | 19,968 |
| Fine | 80 | 48 | 80 | 8 | 64 | 16 | 159,744 |

Every logical direction doubles and cell count rises by eight. Richardson
order and GCI therefore describe the combined solution-discretization and
geometry-realization sequence.

## Pre-execution gate

Before any retained mesh or solver execution:

1. commit the contract, this runbook, geometry/case generator, evaluator,
   result/provenance path, schemas, and focused tests;
2. require every transitive scientific path to be clean at that commit;
3. retain the contract SHA-256 and source hashes;
4. resolve the configured OpenFOAM image to exactly one immutable image ID;
5. generate every level twice and require identical per-file hashes;
6. verify the result component map covers every source cell exactly once
   through the three explicit component ranges;
7. verify the chosen output directory does not exist or is empty; and
8. confirm no active or unresolved campaign will be modified.

Any failure is retained as infrastructure or contract failure. Do not repair
it inside the same output directory.

## Execution and evaluation

Run from the repository root into a new ignored directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.curved_elbow_campaign \
  benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v1-r1 \
  --run
```

The runner must materialize through `adapters.generate_case`, execute through
`JobManager`, retain partial failures, and stop before the next level after any
mandatory per-level failure. It may evaluate only retained `polyMesh`, logs,
patch metrics, and solver-produced VTK fields.

Each level must pass:

- exact profile and runtime geometry within 1%;
- one connected three-direction all-hex region, `checkMesh`, zero invalid
  cells, exact patches, and all frozen quality thresholds;
- finite `p` and `U`, zero exit, and normal declared termination;
- at most 0.1% relative flow imbalance;
- positive static pressure loss and no total-pressure gain beyond the frozen
  numerical allowance;
- at most 2% mirror-symmetry error across `z=0`; and
- complete, non-overlapping explicit source-cell provenance for inlet leg,
  elbow, and outlet leg.

Only after all three levels pass may the evaluator calculate the frozen
three-grid pressure-loss order and fine-grid GCI. Invalid mathematics fails
closed. Required order is `0.5–4`; fine GCI must be no more than `5%`.

## Iteration and retention

Never weaken or reinterpret an observed gate. Do not add an offset or
uncertainty allowance after seeing a result. A mesh or GCI failure may be
diagnosed read-only. Any changed topology, discretization, solver control,
operator, or threshold is a new prospective contract and a new output
directory.

Raw campaign evidence stays local and ignored. Do not force-add it. The compact
report must bind the contract, source commit, generated case trees, runtime
image, JobManager records, logs, VTK fields, per-level evaluations, and final
artifact manifest by SHA-256.

`promotionAuthorized` remains `false`. No registry, validated pointer, UI
promotion state, or release claim may change in this campaign.
