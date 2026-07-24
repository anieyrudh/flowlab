# Full O-grid straight-pipe verification runbook

Status: **prospective v1 contract; no retained scientific result observed at
freeze**

This runbook governs the first full-revolution, non-axisymmetric O-grid
verification candidate. It does not reuse the axisymmetric campaign as proof,
does not modify the formal `straight-pipe` fixture, and cannot authorize
promotion. The frozen machine-readable authority is
`VERIFICATION_CONTRACT_V1.json`.

## Claim and state boundaries

The only candidate claim is numerical verification of FlowLab's five-block
full-360 O-grid product path for one steady, incompressible, laminar,
constant-diameter straight circular pipe against the Hagen–Poiseuille
solution.

Keep these states separate in every record:

1. software completeness;
2. native mesh verification;
3. solver execution;
4. three-level numerical verification;
5. controlled independent review;
6. fixture or registry promotion;
7. product promotion; and
8. desktop or external release.

A solver exit code, `Mesh OK`, or a passing numerical campaign is not
independent validation. `promotionAuthorized` remains `false` throughout this
campaign.

## Frozen case

- Length: `0.024 m`
- Radius: `0.006 m`
- Density: `1000 kg/m^3`
- Dynamic viscosity: `0.001 Pa s`
- Volumetric flow: `1e-5 m^3/s`
- Reynolds number: approximately `1061.03`
- Boundary operator: discrete-flux-normalized fully developed parabolic
  velocity at `inlet`, zero kinematic pressure at `outlet`, and no slip at
  `walls`
- Product execution: adapter-generated case queued through FlowLab
  `JobManager`

The exact analytical values, observation operators, tolerances, and levels are
frozen in the JSON contract. Do not reinterpret or tune them after any retained
level begins.

## Refinement sequence

| Level | Axial | Annular radial | Circumferential | Core side | Cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 16 | 4 | 32 | 8 | 3,072 |
| Medium | 32 | 8 | 64 | 16 | 24,576 |
| Fine | 64 | 16 | 128 | 32 | 196,608 |

Every logical material dimension doubles and the cell count increases by
eight. The wall facet count also doubles. Richardson order and GCI therefore
describe the combined solution-discretization and wall-geometry-realization
sequence. They must never be described as isolating solution discretization.

## Pre-execution gate

Before starting a retained campaign:

1. confirm the desktop-release QA prerequisite already has a definitive
   disposition;
2. confirm this contract, runbook, campaign module, O-grid topology, adapter,
   execution, result parser, schemas, and verification utilities are committed
   and clean;
3. calculate and retain the contract SHA-256;
4. verify the configured Docker image resolves locally to exactly one immutable
   image ID;
5. materialize every level twice in memory and require identical generated-file
   hashes;
6. confirm no target campaign directory exists with retained content; and
7. confirm no active or unresolved campaign directory will be modified.

If any gate fails, record an infrastructure or contract failure and stop. Do
not create a replacement result inside the same retained directory.

## Execution

Run from the repository root into a new ignored directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.full_ogrid_straight_pipe_campaign \
  benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v1-r1 \
  --run-and-package
```

The runner must:

- materialize the exact editor project through `adapters.generate_case`;
- queue every level through `JobManager`;
- retain partial or failed output without overwriting it;
- stop the campaign after an infrastructure or mandatory per-level failure;
- preserve all already-created evidence;
- evaluate only from retained solver outputs and frozen operators; and
- package a completed three-level campaign whether scientific gates pass or
  fail.

Do not invoke the generated `Allrun` as a substitute for JobManager. Foundation
OpenFOAM requires FlowLab's runtime function-object normalization for the
frozen patch-metric contract.

## Evaluation

For each level, retain and evaluate:

- exact cell and patch face counts;
- one connected all-hex region;
- three geometric and solution directions;
- positive volume and the frozen mesh-quality metrics;
- solver-produced nonzero XYZ extents;
- SIMPLE convergence, final linear residuals, and continuity;
- signed inlet/outlet fluxes and relative conservation;
- area-averaged inlet/outlet kinematic pressure converted to Pa;
- mid-plane cell-centred axial and transverse velocity metrics; and
- polygon wall-area deficit.

After all three levels, apply the frozen Richardson/GCI operator to pressure
drop. If the sequence is non-monotone, degenerate, or otherwise invalid,
record GCI as unqualified with the exact reason. An invalid GCI cannot pass the
sequence gate.

Never remove a failed result, change an operator, or widen a threshold. A new
hypothesis requires a successor contract and a new output directory.

## Evidence package

The package must include deterministic archives or compact reports for:

- generated case manifests;
- blockMesh/polyMesh artifacts and mesh logs;
- solver logs and residual history;
- raw solver-produced fields;
- patch-metric outputs;
- runtime and source provenance;
- per-level evaluations;
- campaign-level GCI and gate assessment;
- the frozen contract and runbook; and
- an artifact index and tree digest.

The runner makes the package read-only. The package manifest and independent
review request must bind the exact package tree digest. Raw campaigns remain
local and ignored; no evidence may be force-added broadly.

## Controlled independent review

The reviewer must be independent of implementation and execution and must
verify:

- contract prospectivity and hash;
- source cleanliness and commit identity;
- image tag, immutable image ID, OpenFOAM version, and JobManager records;
- deterministic case hashes;
- mesh topology, patch roles, and quality at every level;
- observation-operator implementation;
- conservation, profile, pressure, observed-order, and GCI calculations;
- package artifact hashes and tree digest;
- claim scope; and
- absence of fixture, registry, validation-state, promotion, or release
  mutation.

Until a controlled reviewer accepts the exact package digest, the final state
is at most `verification-candidate-awaiting-independent-review`. No benchmark
fixture, registry entry, validation pointer, UI claim, or promotion state may
change.
