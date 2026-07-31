# Full-O-grid geometry qualification blocked handoff — 2026-07-31

## Status

The branch implements and prospectively freezes the bounded experimental
full-O-grid geometry path, but the qualification is **blocked** at explicit
solver-cell identity. No validation, promotion, fixture-pointer, registry,
release, or retained-evidence status changed.

- Branch: `codex/geometry-axisymmetric-qualification`
- Exact committed V4 execution source: `3c8b27b188e4904cf63f27ac49014758eb3302a8`
- Baseline: `d3bef156`
- V3 contract SHA-256:
  `f40c0dc582c58496209872c03808ee493c32535f3048af9e5ddbf78cbf98ba1f`
- V4 revision SHA-256:
  `2e17b59d87a9f01f5f7c88859a9d7c72db80ce8d7bb7f06e7a5e65fd41a028d9`
- Frozen image:
  `flowlab/openfoam11-gmsh:2026-07-13` at
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`

## Implemented software path

- A straight-axis circular multi-segment full-revolution O-grid with five
  conformal hexahedral blocks per geometry segment.
- Prospective generation-only Venturi, contraction, expansion, and nozzle
  cases.
- A five-edge contraction/throat/expansion/recovery runtime case.
- Three exact logical grids with 2,496, 19,968, and 159,744 cells.
- Deterministic generated-file hashing across two builds.
- Versioned fail-closed normalized logical vertex identity.
- Complete explicit edge ranges with zero connector-owned or unowned cells.
- Full/preview result-load and schematic-selection evaluators.
- Unsupported SU2 full-O-grid and axisymmetric requests remain fail closed.
- The existing axisymmetric and full-O-grid straight-pipe campaign modules
  remain byte unchanged.

## Prospective generation evidence

V4 preflight passed for all seven cases. Independent generated-file hashes
matched for Venturi, contraction, expansion, nozzle, and all three runtime
levels. All generation-only previews were 3D, positive-volume, and exposed
exactly `inlet`, `outlet`, and `walls`.

The V4 retained preflight report is under the ignored campaign:

`benchmarks/cases/full-ogrid-geometry/campaigns/2026-07-31-v4-r1/preflight-report.json`

Its SHA-256 recorded in campaign state is
`4d216fcb303a3b4267268a419663663fa036c854d26624d026146e3c7372ac6b`.

## Retained execution evidence

### V3

`benchmarks/cases/full-ogrid-geometry/campaigns/2026-07-31-v3-r1`
is immutable. It stopped before solver launch because JobManager routed the new
profile through `snappyHexMesh`. V4 prospectively corrected only the
manifest-bound direct-`blockMesh` command routing.

### V4 coarse

`benchmarks/cases/full-ogrid-geometry/campaigns/2026-07-31-v4-r1`
is immutable. The coarse run established:

- `blockMesh`: pass, 2,496 hexahedra, 3,021 points;
- exact patches: `inlet` 48, `outlet` 48, `walls` 832;
- `checkMesh -allGeometry -allTopology`: `Mesh OK`;
- one connected region;
- three geometric and three solution directions;
- minimum volume `2.6367187e-10 m3`;
- maximum non-orthogonality `27.121578 degrees`;
- maximum skewness `0.43386129`;
- solver exit code `0`, normal `End`, 2,000 SIMPLE iterations;
- final relative mass imbalance `8.000000012079291e-08`;
- 100-sample pressure-drop relative span `0.002521505244810559`;
- 100-sample measured-flow relative span `4.5299997835931725e-06`;
- mean tail flow `5.000000239e-06 m3/s`.

Those mesh and solver observations satisfy their individual V4 bounds, but
they do not constitute a qualified level because result identity failed.

## Blocking result-identity evidence

The generated and solver meshes both contain 2,496 unique cells. The V4
`full-ogrid-normalized-logical-vertex-signature-v3` comparison found:

- matching signatures: 1,608;
- generated-only signatures: 888;
- solver-only signatures: 888.

The retained job failed closed with:

`cannot verify OpenFOAM source-cell identity: an OpenFOAM solver cell has no matching generated source-cell signature`

Observed mismatches include normalized circumferential coordinates differing
at approximately the ninth decimal place (for example `0.923879533` versus
`0.923879532`). The frozen canonicalization must not be loosened after seeing
this result. Any alternative identity operator requires an independently
reviewed prospective revision and a fresh output directory.

Because identity is unverified:

- the raw `foamToVTK` artifact was not admitted as a FlowLab-native result;
- full and preview result loading are not proven on actual output;
- schematic selection is not proven on actual output;
- edge trends were not evaluated;
- medium and fine runs were not launched;
- observed order and fine-grid GCI are unavailable.

## Test evidence

- Focused backend qualification/result pipeline suite: 225 passed.
- Focused V4 routing and execution suite: 126 passed.
- Full backend: 606 passed, 3 skipped, 7 failed. All seven failures are
  pre-existing `local_evidence` tests whose ignored historical campaign files
  are absent from this isolated worktree.
- Frontend unit tests: 94 passed.
- Focused Playwright multi-edge provenance selection: 1 passed.
- TypeScript lint: passed.
- Production frontend build: passed.

No test was weakened or skipped to obtain these results.

## Integration conflict surface

The files most likely to conflict are:

- `server/flowlab/adapters.py`
- `server/flowlab/execution.py`
- `server/flowlab/full_ogrid.py`
- `server/flowlab/result_identity.py`
- `server/flowlab/results.py`
- `server/flowlab/schemas.py`
- `src/App.tsx`
- `src/results/vtk.ts`
- `src/types.ts`
- `tests/e2e/editor.spec.ts`

The new qualification runner, contracts, runbooks, and focused tests are
isolated under the corresponding full-O-grid geometry filenames and
directories.

## Required next decision

Do not continue V4. Review whether a new prospective identity revision can
prove a unique one-to-one mapping while remaining invariant to OpenFOAM’s
coordinate realization. Only after that review may a fresh V5 contract and
new ignored campaign directory be authorized.
