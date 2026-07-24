# Non-axisymmetric full O-grid status — 2026-07-24

Status: **software path implemented and natively exercised; prospective
scientific campaign not yet frozen or run**

This record keeps software completeness, native mesh verification, solver
execution, numerical verification, independent validation, promotion, and
release separate. Nothing in this record changes a benchmark fixture, registry
entry, validation state, promotion state, or product claim.

## Prerequisite disposition

The desktop-release QA goal recorded a definitive disposition before this work
began:

- the self-contained internal macOS arm64 package passed its bounded portable
  QA surface;
- external release remains blocked on Developer ID signing, Team ID,
  notarization/stapling, and an independent clean-Mac check; and
- no scientific or promotion state was changed by that release work.

Implementation started from clean commit `5f525dc` on the dedicated branch
`codex/full-ogrid-product-milestone`.

## Implemented software surface

- Explicit `solver.meshMode = "full-ogrid"`.
- Exactly one editor-authored source-to-sink, constant-diameter circular pipe.
- Fail-closed steady, incompressible, laminar scope.
- Deterministic five-block all-hex topology:
  - one center block;
  - four circumferential wall blocks;
  - four conformal internal interfaces;
  - explicit `inlet`, `outlet`, and `walls` patches;
  - no collapsed-axis cells.
- Explicit axial, annular-radial, circumferential, and core resolution.
- Product adapter, API, JobManager native-mesh routing, result loading,
  full-volume VTK/VTU preview, editor controls, exterior-surface extraction,
  and solver-space XYZ owner-cell probing.
- A prospective-request hook for a fully developed parabolic inlet and pressure
  outlet. This hook is not a frozen campaign contract and is not validation
  evidence.

The coarse/medium/fine product defaults refine every material discretization
dimension:

| Level | Axial | Annular radial | Circumferential | Core side | Cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 16 | 4 | 32 | 8 | 3,072 |
| Medium | 32 | 8 | 64 | 16 | 24,576 |
| Fine | 64 | 16 | 128 | 32 | 196,608 |

## Native software preflight disposition

A disposable coarse product case with length `0.024 m` and diameter `0.006 m`
was materialized and run with the configured
`flowlab/openfoam11-gmsh:2026-07-13` image:

- image ID:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`;
- OpenFOAM Foundation version: `11`;
- `blockMesh`: exit `0`;
- `checkMesh -allGeometry -allTopology`: exit `0`, `Mesh OK`;
- one connected region and three geometric/solution directions;
- 3,072 hexahedra, 3,553 points, 9,664 faces, and 8,768 internal faces;
- patches: `inlet` 192 faces, `outlet` 192 faces, `walls` 512 faces;
- bounds: `(0,-0.003,-0.003)` to `(0.024,0.003,0.003)`;
- minimum volume: `2.6367187e-11 m^3`;
- maximum aspect ratio: `16`;
- maximum non-orthogonality: `32.152674°`;
- maximum skewness: `0.55753716`;
- minimum cell determinant: `0.10455798`;
- minimum interpolation weight: `0.16487075`; and
- minimum face-volume ratio: `0.151744`.

The real FlowLab JobManager product path then:

- detected the Foundation runtime and normalized function objects;
- skipped non-applicable surface extraction and `snappyHexMesh`;
- repeated `blockMesh` and full `checkMesh` successfully;
- completed the steady solver in 56 iterations with exit `0`;
- exported five bounded VTK result artifacts;
- produced seven `fullOGridXYZProbes` pressure samples;
- reported relative inlet/outlet flow imbalance
  `4.627486223418557e-08`; and
- exported a 3,072-cell full-volume VTK containing `U`, `p`, `cellID`, and
  `wallShearStress`, with XYZ spans `[0.024, 0.006, 0.006]`.

The disposable solver VTK had SHA-256
`afadc9985eed2d711bfbed70b0bd0ddecc0d3678d3d899fd1c923279c902b23b`.
This hash records the software-preflight disposition only; the run is not a
retained scientific campaign and is not promotion evidence.
Both disposable preflight directories were removed after the disposition and
metrics above were recorded.

Directly invoking the generated `bash Allrun` without JobManager runtime
normalization failed before iteration one because Foundation OpenFOAM 11 does
not provide the generic `patchFlowRate` function-object type. The product path
passed because its existing runtime adapter converts the normalized diagnostic
contract to Foundation-compatible per-patch `surfaceFieldValue` objects. The
standalone-path limitation remains open and must not be hidden by the passing
JobManager result.

The optional pre-solve `postProcess -func yPlus` also exited `1` because a
turbulence model was not loaded in that pre-solve context. It remained
non-blocking for this laminar software execution. Production-mesh approval
remains blocked and was not reinterpreted.

## Verification completed for this increment

- Backend: `573 passed`.
- Frontend: `81 passed`.
- Type/lint gate: passed.
- Production build: passed.
- Focused topology, geometry-contract, adapter, execution-router, API, schema,
  editor-control, preview-surface, and probing tests: passed.

## States not yet reached

- Prospective O-grid scientific contract: **not frozen**.
- Retained coarse/medium/fine scientific runs: **not started**.
- Observed order and GCI: **not evaluated**.
- Conservation/profile gates under a frozen operator: **not evaluated**.
- Immutable scientific evidence package: **not assembled**.
- Controlled independent scientific review: **not requested**.
- Benchmark/registry/validation changes: **none**.
- O-grid promotion: **not authorized**.
- External release: **blocked independently**.

The next gate is to freeze a separate O-grid verification contract and runbook,
including immutable operators, thresholds, refinement interpretation, geometry
error treatment, provenance requirements, and independent-review rules, before
any retained scientific level is executed.
