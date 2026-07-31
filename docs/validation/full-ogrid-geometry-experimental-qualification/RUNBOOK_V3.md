# Full-O-grid generated-geometry experimental qualification v3

## Scope

This runbook executes only the prospective contract in
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json`. A passing campaign qualifies the
bounded experimental software geometry path. It does not validate a physical
model, promote a fixture or preset, alter either straight-pipe campaign, or
authorize a release.

V3 is a new full-revolution topology. It does not revise or rerun the immutable
wedge v1/v2 contracts or their retained evidence.

## Preconditions

1. Read `AGENTS.md`, the final assessment and resolution backlog, the evidence
   retention boundary, `docs/openfoam-axisymmetric-wedge-2026-07-22.md`, and
   the full-O-grid straight-pipe v2 contract and runbook.
2. Confirm the contract, runner, adapters, result identity, result loaders,
   frontend selection path, and their focused tests are clean and committed.
3. Confirm Docker can resolve
   `flowlab/openfoam11-gmsh:2026-07-13` to one immutable image ID.
4. Choose a new, empty, ignored directory below
   `benchmarks/cases/full-ogrid-geometry/campaigns/`.
5. Do not edit, reuse, compact, or overwrite any previous campaign directory.

## Prospective preflight

From the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.full_ogrid_geometry_qualification \
  benchmarks/cases/full-ogrid-geometry/campaigns/<new-run-id> \
  --materialize-only
```

The preflight generates each single-edge shape and all three runtime grids
twice. Every generated file hash must match. Each preview must be a
positive-volume 3D all-hex mesh with only `inlet`, `outlet`, and `walls`, and
every runtime case must contain the frozen fail-closed source-cell identity
contract.

## Retained execution

Only after the exact preflight source has been committed:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.full_ogrid_geometry_qualification \
  benchmarks/cases/full-ogrid-geometry/campaigns/<new-run-id> \
  --run
```

The runner creates a fresh JobManager runtime per level and records source
commit, contract hash, image tag and immutable image ID, generated manifests,
mesh and solver logs, native VTK artifacts, identity mappings, full and preview
loads, edge-selection proofs, per-level evaluations, and the three-grid
assessment.

Do not stop a long pressure solve merely because it is slow. Inspect log
modification time, container state, and CPU activity. Classify unavailable
Docker or runtime infrastructure separately from scientific gate failure.

## Frozen pass operator

All mandatory gates in the contract are conjunctive. In particular:

- exact three-dimensional, one-region, all-hex topology and exact patches;
- finite `p` and `U`, normal termination, mass imbalance at most 0.1%;
- 100-sample pressure-drop tail span at most 0.5% and flow tail span at most
  0.1%;
- three approximately 2x grids, observed order from 0.5 through 4, and fine GCI
  at most 5%;
- all frozen contraction, throat, and recovery trends on the fine grid;
- a verified one-to-one source-cell mapping, full and preview result loads,
  unique edge ownership, and zero connector-owned cells.

An invalid, non-monotone, degenerate, or out-of-range Richardson sequence
fails. Missing or ambiguous cell identity fails. No threshold may be tuned
after observing a result.

## Retention and status

Raw campaigns remain ignored and local unless separately governed. Never
force-add a campaign tree. A compact artifact may enter Git only through the
existing allowlist and a separate review.

Regardless of outcome, retain `validated=false`, `promotionAuthorized=false`,
and `releaseAuthorized=false`. Do not update fixture pointers, registries,
validated presets, final validation claims, or release documentation.
