# Mesh Controls And OpenFOAM checkMesh Gate - 2026-06-14

FlowLab now has a stronger mesh-control path for generated solver cases and a
native OpenFOAM mesh check before CFD execution.

## What Changed

- Added `solver.meshControls` support to the project schema:
  - `longitudinalRefinement`
  - `boundaryLayerLayers`
  - `boundaryLayerGrowthRate`
  - `targetYPlus`
  - per-edge `refinementRegions`
  - opt-in `featureRefinement` for Venturi throats and diameter transitions
  - starter mesh-quality threshold overrides
- Added a desktop inspector mesh-controls panel in the advanced solver section.
  Users can now edit mesh resolution, longitudinal refinement, 2D boundary
  layer strip count/growth, target y-plus, feature clustering, selected-edge
  local refinement, and starter quality thresholds from the GUI. These values
  are persisted into project JSON and passed to case generation as
  `solver.meshControls`.
- Added `mesh/controls.json` to generated case bundles with normalized controls,
  derived transverse fractions, quality thresholds, and explicit
  `productionReady: false` status.
- Added `mesh/refinement_plan.json` to generated case bundles with per-region
  segment counts, edge-refinement factors, feature-refinement factors, optional
  station clustering, and explicit `productionReady: false` status.
- Added `mesh/boundary_layer_plan.json` to generated case bundles with
  target-y-plus first-cell sizing estimates, Reynolds number, smooth-pipe
  friction factor, friction velocity, starter strip height, and explicit
  `productionReady: false` status.
- Added `mesh/prism_layer_plan.json` to generated case bundles with native
  prism-layer review inputs derived from the y-plus plan: first-cell height,
  requested layer count, growth rate, total prism stack height, native mesher
  control requirements, and explicit `productionReady: false` status.
- Added `mesh/adaptation_plan.json` to generated case bundles with
  solver-neutral native adaptation targets derived from source refinement,
  geometry features, boundary-layer y-plus sizing, and future solver-field
  indicators. It keeps native adapted volume meshes, adaptation history, and
  post-adaptation solver-quality evidence unresolved with explicit
  `productionReady: false` status.
- Added `mesh/physical_groups.json` to generated case bundles with a
  machine-readable source-region map for Gmsh physical names/tags, SU2 markers,
  Code_Saturne groups, OpenFOAM aggregate patches, inlet/outlet/wall/front-back
  roles, and explicit `productionReady: false` status.
- Added `mesh/openfoam_snappy_handoff.json` to generated case bundles with
  review-only OpenFOAM `snappyHexMesh`/`addLayersControls` planning inputs:
  expected CAD/STL files, boundary patch roles, refinement-region levels,
  wall-layer controls derived from `mesh/prism_layer_plan.json`, required
  quality evidence, and explicit `productionReady: false` status.
- Added `mesh/openfoam_native_mesh_preflight.py` to generated case bundles as
  a local preflight for materialized OpenFOAM cases. It checks for the reviewed
  CAD/STL triSurface, installed native dictionaries, non-placeholder
  `locationInMesh`, and snappy/checkMesh/y-plus evidence before a native
  `snappyHexMesh` attempt; it writes a structured report and stays blocked
  until those native inputs exist.
- Added `mesh/su2_native_meshing_handoff.json` to generated case bundles with
  review-only SU2 marker preservation, viscous-layer sizing, adaptation target,
  expected native `.su2` file, and SU2 startup/mesh-diagnostic evidence
  requirements, with explicit `productionReady: false` status.
- Added `mesh/code_saturne_native_meshing_handoff.json` to generated case
  bundles with review-only Gmsh/CGNS/MED import intent, volume/boundary group
  localization, prism-layer import requirements, adaptation evidence, and
  Code_Saturne preprocessing/listing quality requirements, with explicit
  `productionReady: false` status.
- Added review-only OpenFOAM dictionary templates:
  `mesh/openfoam_snappyHexMeshDict.template`,
  `mesh/openfoam_surfaceFeatureExtractDict.template`, and
  `mesh/openfoam_meshQualityDict.template`. These templates are generated from
  the handoff manifest and preserve patch, refinement, feature-extraction, and
  layer-control intent, but they are not runnable without reviewed CAD/STL
  surfaces and native mesh-quality evidence.
- Added `mesh/production_mesh_plan.json` to generated case bundles with a
  machine-readable production-mesh gap checklist. It records starter passes for
  port-aware source topology, source-mesh quality, deterministic refinement
  intent, physical-group mapping, OpenFOAM native-meshing handoff, y-plus
  sizing, prism-layer review inputs, and solver-neutral adaptation targets
  while keeping CAD-quality geometry, production 3D volume meshing, generated
  prism layers, native adaptation history, and solver-native mesh/y-plus
  evidence as failing readiness checks.
- Added `mesh/native_meshing_plan.json` and
  `mesh/gmsh_production_handoff.geo` as review artifacts for native production
  meshing. The manifest lists Gmsh/OpenFOAM/SU2/Code_Saturne handoff stages,
  required quality evidence, and remaining blockers; the `.geo` file preserves
  FlowLab physical group names and sizing intent but remains a placeholder
  scaffold, not CAD geometry.
- Added `mesh/production_mesh_acceptance.json` as a solver-neutral acceptance
  checklist. It keeps production approval blocked until CAD-quality geometry,
  native 3D volume mesh evidence, prism/hex-layer cells, native adaptation
  history, and
  OpenFOAM/SU2/Code_Saturne mesh-quality reports are available.
  It now includes `nativeQualityEvidence` with per-solver missing quality
  reports, required commands, required metrics, y-plus/wall-distance evidence,
  and adaptation-history requirements.
- Added `mesh/openfoam_review.json` to generated OpenFOAM case bundles with
  solver-specific readiness checks:
  - fitted `constant/polyMesh` export;
  - source mesh quality;
  - scripted `checkMesh -allGeometry -allTopology`;
  - CAD-quality 3D topology;
  - production boundary-layer/y-plus evidence;
  - pre-run solver-native mesh-quality evidence.
- Applied controls to actual source mesh generation:
  - longitudinal refinement increases segment count;
  - per-edge refinement multiplies selected edge segments;
  - feature refinement can multiply eligible Venturi, nozzle, expansion, and
    contraction edge segments and cluster stations near the throat/transition;
  - the default mesh now has one symmetric 2D transverse strip layer near both
    walls;
  - boundary-layer controls can tune or expand those transverse strip cells.
  - target-y-plus controls now produce deterministic first-cell sizing evidence
    per edge, but do not create production prism layers.
  - adaptation targets now identify per-edge geometry, boundary-layer, and
    solver-field indicator refinement inputs for native meshers, but do not
    create a native adapted 3D mesh.
- Added simple connector cells across two-port components so fitted solver
  meshes remain face-connected through junction bodies instead of stopping each
  pipe strip at separate component ports.
- Updated SU2, Gmsh, OpenFOAM, and Code_Saturne boundary exports to preserve
  inlet, outlet, and wall marker groups for multi-layer transverse meshes.
- Added a required OpenFOAM pre-solve gate:

```bash
checkMesh -allGeometry -allTopology
```

Generated OpenFOAM cases now run this after fitted `constant/polyMesh` or
`blockMesh` setup and before `foamRun` or `foamMultiRun`. Case validation
rejects OpenFOAM `Allrun` scripts that omit the check, and the job quality gate
marks nonzero failed-checkMesh logs as failed even if a wrapper exits `0`.
Completed and failed OpenFOAM jobs now also expose parsed native mesh evidence
under `logSummary.checkMesh` when stdout contains recognizable `checkMesh`
lines, including point/face/cell counts, max aspect ratio, max/average
non-orthogonality, max skewness, failed-check count, and pass/completion flags.
For multi-region logs, the same metrics are captured per region under
`logSummary.checkMeshRegions`, keyed by region names such as `fluid` and
`solid`.

The OpenFOAM review manifest is intentionally not a runtime pass/fail gate for
starter execution when `productionReady` is `false`. It records the gap between
a runnable starter mesh and a production CFD mesh. Smoke reports expose the same
evidence at `caseValidation.openfoamMeshReview`.

## Validation

Focused validation run during implementation:

```bash
python3 -m pytest server/tests/test_mesh_results.py \
  server/tests/test_adapters.py::test_openfoam_conjugate_heat_transfer_emits_multiregion_case_bundle \
  server/tests/test_adapters.py::test_openfoam_water_hammer_mode_records_tier1_handoff \
  server/tests/test_adapters.py::test_openfoam_graph_case_includes_fitted_polymesh -q

python3 -m pytest server/tests/test_execution.py::test_openfoam_exit_zero_with_failed_checkmesh_logs_fails_quality_gate \
  server/tests/test_execution.py::test_validate_solver_case_requires_openfoam_checkmesh_step \
  server/tests/test_execution.py::test_validate_solver_case_catches_missing_openfoam_assets -q
```

Results:

- Mesh/adapter focused tests: `20` passed.
- Execution validation focused tests: `3` passed after the test assertion was
  corrected to inspect captured logs for checkMesh failure text.
- Follow-up mesh-review validation added schema and smoke coverage for
  `mesh/openfoam_review.json`, including `productionReady: false` blocking
  reasons and `caseValidation.openfoamMeshReview` summary evidence.
- Follow-up production-mesh validation added schema coverage for
  `mesh/production_mesh_plan.json`; OpenFOAM case validation now requires it
  and rejects missing/invalid readiness checks or a premature
  `productionReady: true` claim.
- Follow-up production-mesh acceptance validation added schema coverage for
  `mesh/production_mesh_acceptance.json`; OpenFOAM case validation now requires
  it and rejects missing acceptance criteria, solver acceptance entries, source
  artifact references, blocking reasons, or a premature `productionReady: true`
  claim.
- Follow-up prism-layer validation added schema coverage for
  `mesh/prism_layer_plan.json`; solver case validation now requires the native
  meshing plan to reference this review artifact and rejects premature
  `productionReady: true` claims.
- Follow-up adaptation-plan validation added schema coverage for
  `mesh/adaptation_plan.json`; OpenFOAM case validation now requires the native
  meshing plan and production acceptance checklist to reference this review
  artifact, and rejects missing adaptation targets, generated-source
  references, failing native-adaptation readiness checks, blocking reasons, or
  premature `productionReady: true` claims.
- Follow-up physical-group validation added schema coverage for
  `mesh/physical_groups.json`; solver case validation now requires the native
  meshing plan to list this artifact and checks for boundary/volume groups plus
  Gmsh, SU2, Code_Saturne, and OpenFOAM target mappings.
- Follow-up OpenFOAM native-meshing validation added schema coverage for
  `mesh/openfoam_snappy_handoff.json`; solver case validation now requires the
  native meshing plan to list this artifact and checks patch plans,
  `addLayersControls.layers`, readiness checks, blocking reasons, and
  `productionReady: false`.
- Follow-up OpenFOAM native-mesh preflight validation now requires
  `mesh/openfoam_native_mesh_preflight.py` in the production mesh plan, native
  meshing plan, and production acceptance evidence; validation checks that the
  script identifies its schema, report schema, CAD/STL requirement,
  `locationInMesh`, `snappyHexMesh -overwrite`, and y-plus evidence command.
- Follow-up OpenFOAM dictionary-template validation now requires the snappy
  handoff and native meshing plan to reference the generated templates and
  checks that the templates contain the expected review markers and major
  OpenFOAM dictionary sections.
- Follow-up SU2 and Code_Saturne native meshing validation added schema
  coverage for `mesh/su2_native_meshing_handoff.json` and
  `mesh/code_saturne_native_meshing_handoff.json`; solver case validation now
  requires the native meshing plan, production mesh plan, and production
  acceptance checklist to reference these artifacts and rejects malformed
  marker/group plans, missing prism-layer references, missing readiness checks,
  missing blocking reasons, or premature `productionReady: true` claims.
- Follow-up checkMesh log parsing added machine-readable
  `logSummary.checkMesh` evidence and corrected the quality gate so
  `Failed 0 mesh checks.` is treated as passing rather than as a generic
  failure.
- Follow-up CHT mesh-evidence parsing added `logSummary.checkMeshRegions` for
  region-scoped OpenFOAM `checkMesh -region ...` output.
- Follow-up water-hammer smoke after the default transverse layer and connector
  cell update completed through Docker with `status: complete`, `exitCode: 0`,
  `checkMesh` passing, 4 collected VTK result files, and 2 collected diagnostic
  files. See [openfoam-water-hammer-smoke-2026-06-14.md](openfoam-water-hammer-smoke-2026-06-14.md).
- The latest water-hammer smoke evidence is recorded in
  [openfoam-water-hammer-smoke-2026-06-15.md](openfoam-water-hammer-smoke-2026-06-15.md),
  including the completed Docker run, conservative production-readiness limits,
  handoff values, the blocked production mesh acceptance checklist, VTK outputs,
  residuals, and wall-force diagnostics.

## Remaining Limits

- This is still a deterministic FlowLab source mesh, not CAD-quality 3D
  meshing.
- The boundary-layer controls create 2D transverse strip cells before
  solver-specific extrusion. `mesh/prism_layer_plan.json` provides native
  prism-layer sizing inputs, but production prism cells, surface cleanup, true
  curvature-based unstructured refinement, and solver-native meshing remain
  pending.
- The y-plus plan is an estimate based on project fluid properties and starter
  design velocity assumptions. Production readiness still requires solver-native
  wall-distance/y-plus fields from an executed CFD case.
- Feature refinement is deterministic station clustering on FlowLab graph
  edges. It is useful for starter Venturi/nozzle transitions, but it does not
  replace CAD feature capture, surface remeshing, or adaptive solver feedback.
- Adaptation targets are now machine-readable review inputs for native meshing,
  but FlowLab still does not generate native adapted volume cells or capture
  before/after adaptation history.
- The Gmsh handoff file is intentionally review-only. Production workflows must
  replace its placeholder point with watertight CAD/B-rep surfaces, create real
  3D volumes and prism layers, then capture native solver-quality evidence.
- The SU2 and Code_Saturne native meshing handoffs are intentionally
  review-only. They preserve marker/group, viscous-layer, adaptation, and
  diagnostic intent, but they do not generate production `.su2`, Gmsh, CGNS, or
  MED meshes and do not replace solver-native quality checks.
