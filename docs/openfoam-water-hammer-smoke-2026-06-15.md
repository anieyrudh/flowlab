# OpenFOAM Water-Hammer Handoff Smoke Evidence - 2026-06-15

This document records the current FlowLab water-hammer advanced-mode smoke
evidence from `/tmp/flowlab-openfoam-water-hammer-current-smoke.json`.

## Command

```bash
npm run smoke:openfoam -- --advanced-mode water-hammer --runtime-root /tmp/flowlab-openfoam-water-hammer-current-runtime --output /tmp/flowlab-openfoam-water-hammer-current-smoke.json --timeout 120
```

## Observed Result

- Smoke: `openfoam-water-hammer-solve-through`
- Advanced mode: `water-hammer`
- Status: `complete`
- Completed: `true`
- Execution: `docker`
- Exit code: `0`
- Error: `null`
- Latest OpenFOAM time: `0.05`
- Logs captured: `631`
- `checkMesh` passed: `true`
- `checkMesh` failed checks: `0`
- `checkMesh` cells/faces/points: `39` / `172` / `112`
- Max non-orthogonality: `13.68402`
- Max skewness: `0.60869565`

Runtime diagnostics reported Docker as runnable for
`openfoam/openfoam11-graphical-apps`; native `foamRun` was not on `PATH`, so
the run used Docker.

## Case Evidence

The case manifest reported `49` generated files. Evidence-relevant generated
files included:

- `Allrun`
- `0/p`
- `constant/polyMesh/*`
- `constant/waterHammerPreview.json`
- `constant/waterHammerWaveform.csv`
- `mesh/controls.json`
- `mesh/quality.json`
- `mesh/boundary_layer_plan.json`
- `mesh/adaptation_plan.json`
- `mesh/production_mesh_plan.json`
- `mesh/production_mesh_acceptance.json`
- `mesh/native_meshing_plan.json`
- `mesh/gmsh_production_handoff.geo`
- `mesh/openfoam_snappy_handoff.json`
- `mesh/openfoam_native_mesh_preflight.py`
- `mesh/su2_native_meshing_handoff.json`
- `mesh/code_saturne_native_meshing_handoff.json`
- `mesh/openfoam_review.json`
- `mesh/flowlab_mesh.vtk`
- `mesh/flowlab_mesh.vtu`

`caseValidation.requiredFilesPresent` included:

```json
[
  "flowlab_case_manifest.json",
  "Allrun",
  "system/controlDict",
  "constant/polyMesh/points",
  "constant/polyMesh/faces",
  "constant/polyMesh/owner",
  "constant/polyMesh/neighbour",
  "constant/polyMesh/boundary",
  "constant/waterHammerPreview.json",
  "constant/waterHammerWaveform.csv",
  "system/blockMeshDict",
  "mesh/controls.json",
  "mesh/quality.json",
  "mesh/boundary_layer_plan.json",
  "mesh/adaptation_plan.json",
  "mesh/production_mesh_plan.json",
  "mesh/production_mesh_acceptance.json",
  "mesh/openfoam_review.json",
  "mesh/openfoam_native_mesh_preflight.py",
  "mesh/flowlab_mesh.vtk",
  "mesh/flowlab_mesh.vtu"
]
```

Selected manifest hashes from the refreshed run:

```json
{
  "0/p": {
    "sha256": "8dee81ba4ed8785b771f04ffa319d0aae7067a94e84f1ce103254e68c23143bf",
    "size": 954
  },
  "constant/waterHammerPreview.json": {
    "sha256": "99b7539cf52de17a79be2d5c1e95d038481681cc45e08c80cab05bacf4ed0fb9",
    "size": 2876
  },
  "constant/waterHammerWaveform.csv": {
    "sha256": "b874962e9caa78f7e67525e48d3be41e0b1751caa4294b17bc5a7dfd43fc85bd",
    "size": 165
  },
  "mesh/adaptation_plan.json": {
    "sha256": "384a051c4905397f60c049b7cbf98aca6a41180cd043c9828763f54a0427bb22",
    "size": 5614
  },
  "mesh/openfoam_review.json": {
    "sha256": "9d2e269e9472eec0c93b79dbf478b16a9884a08572672a6401cb855dba34aff6",
    "size": 3208
  },
  "mesh/production_mesh_acceptance.json": {
    "sha256": "75d2338daac3ca76403b1ab260a1f04a13fddf46d582cc381bc3ca6ee475762e",
    "size": 9696
  },
  "mesh/quality.json": {
    "sha256": "5cc920b9b467cb995c89eaefd5feba3be2764c1e08dedf637b89fd58501894ce",
    "size": 1162
  },
  "mesh/native_meshing_plan.json": {
    "sha256": "4218e6e31f7b0d4cb2f9e512496838578bd5f0f60f94915bbda169b22362df36",
    "size": 7797
  },
  "mesh/openfoam_native_mesh_preflight.py": {
    "sha256": "2fc4b05e905dddf2d24b280ffa3363e4434885464c82a34cd79917e6fe173e66",
    "size": 4950
  },
  "mesh/openfoam_snappy_handoff.json": {
    "sha256": "ed9469e0ea3d24856403dc98a39154e338beef474145e2769bc00a2104035e87",
    "size": 7214
  },
  "mesh/su2_native_meshing_handoff.json": {
    "sha256": "a1da6d1f13fc784ae984edc17f4cc571e5ce7b09bc92f621ead1606b4c92fdd9",
    "size": 3756
  },
  "mesh/code_saturne_native_meshing_handoff.json": {
    "sha256": "319c6f107f991a9f8e54e5826022c92bf99e01b5d0e3ad0248a98ad13ef9ff98",
    "size": 3273
  },
  "mesh/gmsh_production_handoff.geo": {
    "sha256": "8c0e227221417aae99c473814e023e237879ed0d9c8566b5e564f0b34b9d80a8",
    "size": 1442
  }
}
```

The generated `mesh/quality.json` summarized the source mesh as:

```json
{
  "cellCount": 39,
  "degenerateCellCount": 0,
  "invertedCellCount": 0,
  "maxAspectRatio": 5.0,
  "maxCellArea": 1152.0,
  "maxInteriorAngleDeg": 107.382389,
  "minCellArea": 64.4,
  "minInteriorAngleDeg": 72.617611,
  "pointCount": 56,
  "regionCount": 3
}
```

The OpenFOAM mesh review stayed intentionally conservative:

```json
{
  "meshGenerated": true,
  "meshType": "flowlab-quad-strip-one-layer-extrusion",
  "productionReady": false,
  "readiness": {
    "cad-quality-3d-topology": "fail",
    "checkmesh-scripted": "pass",
    "fitted-polymesh-export": "pass",
    "production-boundary-layer-evidence": "fail",
    "solver-native-checkmesh-evidence": "fail",
    "source-mesh-quality": "pass"
  }
}
```

The solver-neutral production mesh acceptance checklist stayed blocked as
expected:

```json
{
  "approvalStatus": "blocked",
  "productionReady": false,
  "readiness": {
    "boundary-layer-prism-mesh": "fail",
    "cad-geometry-source": "fail",
    "adapted-refinement-evidence": "fail",
    "native-3d-volume-mesh": "fail",
    "solver-handoff-artifacts": "pass",
    "solver-native-quality-evidence": "fail",
    "source-mesh-traceability": "pass"
  },
  "schema": "flowlab.production_mesh_acceptance.v1",
  "solverAcceptance": {
    "codeSaturne": "blocked",
    "openfoam": "blocked",
    "su2": "blocked"
  }
}
```

The new adaptation handoff remained review-only, as expected:

```json
{
  "schema": "flowlab.mesh_adaptation_plan.v1",
  "productionReady": false,
  "sourceQualityStatus": "ok",
  "targetCount": 2,
  "readiness": {
    "boundary-layer-adaptation-targets": "pass",
    "native-adapted-volume-mesh": "fail",
    "post-adaptation-quality-evidence": "fail",
    "solver-field-indicators": "fail",
    "source-refinement-targets": "pass"
  }
}
```

The refreshed manifest also includes solver-native meshing handoffs for the
production-mesh path. They are review artifacts, not executable production
meshes:

```json
{
  "mesh/native_meshing_plan.json": "flowlab.native_meshing_plan.v1",
  "mesh/openfoam_snappy_handoff.json": "flowlab.openfoam_snappy_handoff.v1",
  "mesh/openfoam_native_mesh_preflight.py": "flowlab.openfoam_native_mesh_preflight.v1",
  "mesh/su2_native_meshing_handoff.json": "flowlab.su2_native_meshing_handoff.v1",
  "mesh/code_saturne_native_meshing_handoff.json": "flowlab.code_saturne_native_meshing_handoff.v1",
  "mesh/gmsh_production_handoff.geo": "review-only Gmsh/OpenCASCADE scaffold"
}
```

## Handoff Summary

The smoke report exposes `caseValidation.waterHammerHandoff`, which confirms
the generated OpenFOAM case preserved the Tier 1 Method-of-Characteristics
handoff and pressure-table mapping:

```json
{
  "boundary": "inlet",
  "boundaryType": "uniformFixedValue table",
  "cfdCoupling": "pressure-wave-boundary-preview",
  "closureTime": 0.001626726,
  "criticalClosureTime": 0.00464779,
  "csv": "constant/waterHammerWaveform.csv",
  "dominantEdgeId": "inlet_pipe",
  "kinematicPressureRise": 1484.576417991,
  "model": "method-of-characteristics-preview",
  "pressureField": "0/p",
  "pressureRise": 1481904.180438,
  "pressureUnits": "kinematic m2/s2",
  "productionReady": false,
  "schema": "flowlab.water_hammer_handoff.v1",
  "settleTime": 0.006274516,
  "waveSpeed": 1484.576417991,
  "waveformRows": 4
}
```

The generated waveform CSV contained:

```csv
time,kinematicPressure,absolutePressure
0.0,0.0,101325.0
0.001626726,1484.576417991,1583229.180438
0.00464779,1484.576417991,1583229.180438
0.006274516,0.0,101325.0
```

## Solver Outputs

The completed run collected these OpenFOAM result files:

- `VTK/case_50.vtk`
- `VTK/inlet/inlet_50.vtk`
- `VTK/outlet/outlet_50.vtk`
- `VTK/walls/walls_50.vtk`

The collected VTK artifacts included bounded field summaries:

```json
[
  {
    "path": "VTK/case_50.vtk",
    "format": "legacy-vtk-ascii-v1",
    "cellCount": 39,
    "pointCount": 112,
    "fields": ["U:cell", "U:point", "cellID:cell", "p:cell", "p:point"]
  },
  {
    "path": "VTK/inlet/inlet_50.vtk",
    "format": "legacy-vtk-polydata-ascii-v1",
    "cellCount": 3,
    "pointCount": 8,
    "fields": ["U:cell", "U:point", "p:cell", "p:point", "patchID:cell"]
  },
  {
    "path": "VTK/outlet/outlet_50.vtk",
    "format": "legacy-vtk-polydata-ascii-v1",
    "cellCount": 3,
    "pointCount": 8,
    "fields": ["U:cell", "U:point", "p:cell", "p:point", "patchID:cell"]
  },
  {
    "path": "VTK/walls/walls_50.vtk",
    "format": "legacy-vtk-polydata-ascii-v1",
    "cellCount": 26,
    "pointCount": 56,
    "fields": ["U:cell", "U:point", "p:cell", "p:point", "patchID:cell"]
  }
]
```

It also collected diagnostics:

- `postProcessing/residuals/0/residuals.dat`
- `postProcessing/wallForces/0/forces.dat`

Latest parsed residual row:

```json
{
  "Time": 0.05,
  "Ux": 0.00257493049,
  "Uy": 0.00235088191,
  "p": 0.00131774873
}
```

Latest parsed wall-force row:

```json
{
  "Time": 0.05,
  "pressureFx": 113.335245,
  "pressureFy": -0.0018109957,
  "pressureFz": 0.0,
  "pressureMx": 0.0,
  "pressureMy": 0.0,
  "pressureMz": -0.00530376168,
  "viscousFx": 0.0195484889,
  "viscousFy": 1.12692782e-08,
  "viscousFz": 0.0,
  "viscousMx": 0.0,
  "viscousMy": 0.0,
  "viscousMz": 1.03110214e-08
}
```

## Interpretation

This smoke proves that the current OpenFOAM water-hammer mode can generate and
execute a starter OpenFOAM case through Docker, preserve the
schema-tagged Tier 1 MOC handoff, map the dominant Joukowsky pressure rise into
an inlet pressure table, pass the scripted starter `checkMesh` gate with zero
failed mesh checks, collect solver VTK files with field summaries, parse
residual/force diagnostics, emit solver-neutral native adaptation targets, and
keep production-mesh acceptance explicitly blocked.

It does not prove full coupled CFD water-hammer physics. The current
implementation remains a one-way pressure-wave boundary preview. True coupled
transient pressure-wave CFD still requires production-grade 3D meshing,
compressible-liquid modeling, pipe-wall elasticity assumptions, and solver-side
coupling evidence.
