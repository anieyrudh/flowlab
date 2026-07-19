# OpenFOAM Water-Hammer Handoff Smoke Evidence - 2026-06-14

This document records the 2026-06-14 FlowLab water-hammer advanced-mode
evidence from `/tmp/flowlab-openfoam-water-hammer-current-smoke.json`.
The newer 2026-06-15 evidence supersedes this run for current README claims.

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

This run uses the updated starter mesh with one default transverse strip layer
and connector cells across two-port components. `checkMesh -allGeometry
-allTopology` passed, so the job was accepted as a clean solve-through.

## Case Evidence

The case manifest reported `32` generated files and SHA-256/size evidence for:

- `Allrun`
- `0/p`
- `constant/polyMesh/*`
- `constant/waterHammerPreview.json`
- `constant/waterHammerWaveform.csv`
- `mesh/controls.json`
- `mesh/quality.json`
- `mesh/boundary_layer_plan.json`
- `mesh/flowlab_mesh.vtk`
- `mesh/flowlab_mesh.vtu`

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
  "mesh/flowlab_mesh.vtk",
  "mesh/flowlab_mesh.vtu"
]
```

## Handoff Summary

The smoke report now exposes `caseValidation.waterHammerHandoff` so the
handoff can be checked without opening the materialized case directory:

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
  "waveformEnd": {
    "absolutePressure": 101325.0,
    "kinematicPressure": 0.0,
    "time": 0.006274516
  },
  "waveformPeak": {
    "absolutePressure": 1583229.180438,
    "kinematicPressure": 1484.576417991,
    "time": 0.001626726
  },
  "waveformRows": 4,
  "waveformStart": {
    "absolutePressure": 101325.0,
    "kinematicPressure": 0.0,
    "time": 0.0
  }
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

The generated `0/p` field used an inlet `uniformFixedValue` pressure table,
mapping the Tier 1 MOC preview into an OpenFOAM pressure-wave boundary input.

## Solver Outputs

The completed run collected these OpenFOAM result files:

- `VTK/case_50.vtk`
- `VTK/inlet/inlet_50.vtk`
- `VTK/outlet/outlet_50.vtk`
- `VTK/walls/walls_50.vtk`

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
  "viscousFx": 0.0195484889,
  "viscousFy": 1.12692782e-08,
  "viscousFz": 0.0
}
```

## Interpretation

Current FlowLab water-hammer generation now proves these narrower facts:

- The OpenFOAM water-hammer mode emits a schema-tagged Tier 1
  Method-of-Characteristics handoff.
- The handoff includes computed wave speed, Joukowsky pressure rise, closure
  timing, waveform start/peak/end rows, waveform CSV rows, and OpenFOAM inlet
  pressure-table mapping.
- The job runner preserves the handoff evidence in the case manifest and
  smoke report.
- The default starter mesh now includes one transverse strip layer plus a
  connector region through the two-port throat component, which is enough for
  this smoke case to pass OpenFOAM `checkMesh`.
- Docker execution reaches OpenFOAM output collection and returns
  `status: complete`.

This still does not prove a full coupled CFD water-hammer pressure-wave solver.
The current implementation is a one-way pressure-wave boundary preview, and
full coupling remains pending behind production-grade meshing and transient
compressible-liquid modeling work.
