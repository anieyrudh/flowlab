# Mesh Quality Gate Evidence - 2026-06-14

FlowLab now emits deterministic starter-mesh quality evidence for generated
solver cases.

## Exported File

Generated mesh bundles include:

- `mesh/quality.json`
- `mesh/boundary_layer_plan.json`
- `mesh/production_mesh_plan.json`
- `mesh.quality` embedded in `mesh/flowlab_mesh.json`

The report schema is `flowlab.mesh_quality.v1`.
The boundary-layer sizing plan schema is `flowlab.boundary_layer_plan.v1`.
The production-mesh gap plan schema is `flowlab.production_mesh_plan.v1`.

## Metrics

The current source-mesh quality report includes:

- Point count
- Cell count
- Region count
- Minimum and maximum cell area
- Maximum aspect ratio
- Minimum and maximum interior angle
- Maximum source non-orthogonality estimate
- Maximum source skewness estimate
- Inverted cell count
- Degenerate cell count
- Thresholds used for the starter gate
- Warnings
- `productionReady: false`

The source non-orthogonality and skewness values are deterministic 2D
angle-based estimates from the starter quad-strip cells. They are useful for
early handoff review and warning thresholds, but they are not substitutes for
OpenFOAM `checkMesh`, SU2 mesh diagnostics, Code_Saturne preprocessing, or
solver-native y-plus/non-orthogonality evidence.

The boundary-layer plan records target y-plus, per-edge Reynolds/friction
estimates, target first-cell height, starter strip height, and
`productionReady: false`.

The production-mesh plan records:

- generated starter evidence files;
- pass/fail readiness checks for source topology, source quality, deterministic
  refinement intent, y-plus sizing, CAD-quality geometry, 3D volume meshing,
  prism-layer meshing, and solver-native mesh evidence;
- blocking reasons and recommended next steps;
- `productionReady: false`.

## Guardrail

The mesh generator normalizes quad winding before quality evaluation and fails
closed for degenerate source quads. The job validator requires OpenFOAM, SU2,
and Code_Saturne cases to include `mesh/quality.json`, blocks any generated case whose report has
`status: failed`, and rejects quality reports that omit numeric source
non-orthogonality/skewness metrics or their thresholds. If either source
angle-quality estimate exceeds its recorded threshold, the report must not claim
`status: ok` and must carry warnings. OpenFOAM validation also requires
`mesh/production_mesh_plan.json` to use schema
`flowlab.production_mesh_plan.v1`, include readiness checks, keep
`productionReady: false`, and list blocking reasons until production CAD,
prism-layer, and solver-native quality evidence exists.
The production acceptance checklist additionally requires
`nativeQualityEvidence` so OpenFOAM, SU2, and Code_Saturne quality commands,
required metrics, y-plus/wall-distance evidence, and missing native report
status remain explicit while production readiness is blocked.

When OpenFOAM writes recognizable `checkMesh` output, FlowLab records
solver-native mesh evidence at `logSummary.checkMesh`. The parsed summary can
include point, face, internal-face, and cell counts plus max aspect ratio,
max/average non-orthogonality, max skewness, failed-check count, and pass or
completion flags. The quality gate treats `Failed 0 mesh checks.` as passing
and only fails on nonzero failed-check counts or other fatal/NaN indicators.
For multi-region `checkMesh -region ...` output, FlowLab records the same
metrics separately under `logSummary.checkMeshRegions.<region>`, which is the
evidence channel needed before the CHT guard can be relaxed.

## Smoke Evidence

Command:

```bash
npm run smoke:openfoam -- --runtime-root /tmp/flowlab-openfoam-mesh-quality-runtime --output /tmp/flowlab-openfoam-mesh-quality-smoke.json --timeout 300
```

Observed:

- Status: `complete`
- Execution: `docker`
- Exit code: `0`
- Manifest includes `mesh/quality.json`
- Manifest includes `mesh/production_mesh_plan.json`
- Required-file evidence includes `mesh/quality.json`
- Required-file evidence includes `mesh/production_mesh_plan.json`

This gate does not certify production CFD readiness. It only proves that the
FlowLab v1 port-aware quad-strip starter mesh is non-degenerate under the
recorded thresholds. Production-grade CFD still requires CAD-quality 3D meshing,
boundary-layer refinement, mesh-quality checks from the target solver, and
domain-specific review.
