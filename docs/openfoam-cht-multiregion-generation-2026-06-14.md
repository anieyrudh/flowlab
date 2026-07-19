# OpenFOAM CHT Multi-Region Generation Evidence - 2026-06-14

This document records the current FlowLab conjugate heat-transfer case-generation
evidence from `/tmp/flowlab-openfoam-cht-multiregion-smoke.json`.

## Command

```bash
npm run smoke:openfoam -- --advanced-mode conjugate-heat-transfer --runtime-root /tmp/flowlab-openfoam-cht-runtime --output /tmp/flowlab-openfoam-cht-multiregion-smoke.json --timeout 120
```

## Observed Result

- Smoke: `openfoam-conjugate-heat-transfer-solve-through`
- Advanced mode: `conjugate-heat-transfer`
- Status: `blocked`
- Completed: `false`
- Execution: `none`
- Exit code: n/a
- Generated file count: `69` materialized files, `68` manifest-tracked generated files
- Manifest status: `generated`

## Generated Multi-Region Files

The generated OpenFOAM case now includes a `foamMultiRun`-style fluid/solid
region bundle:

- `system/controlDict` with `application foamMultiRun` and `regionSolvers`
  mapping `fluid` to `fluid` and `solid` to `solid`.
- `0/fluid/U`
- `0/fluid/p`
- `0/fluid/p_rgh`
- `0/fluid/T`
- `0/fluid/rho`
- `0/fluid/k`
- `0/fluid/omega`
- `0/fluid/nut`
- `0/fluid/alphat`
- `0/solid/T`
- `constant/fluid/g`
- `constant/fluid/momentumTransport`
- `constant/fluid/physicalProperties`
- `constant/solid/physicalProperties`
- `system/fluid/fvSchemes`
- `system/fluid/fvSolution`
- `system/solid/fvSchemes`
- `system/solid/fvSolution`
- `AllmeshCheck`
- `constant/flowlab_cht_interface.json`
- `constant/fluid/polyMesh/points`
- `constant/fluid/polyMesh/faces`
- `constant/fluid/polyMesh/owner`
- `constant/fluid/polyMesh/neighbour`
- `constant/fluid/polyMesh/boundary`
- `constant/solid/polyMesh/points`
- `constant/solid/polyMesh/faces`
- `constant/solid/polyMesh/owner`
- `constant/solid/polyMesh/neighbour`
- `constant/solid/polyMesh/boundary`

The fluid physical properties use `heRhoThermo`. The solid physical properties
use `heSolidThermo`. Region temperature fields include `coupledTemperature`
starter boundaries to preserve the intended CHT interface semantics. The split
region mesh exports paired `mappedWall` patches:

- Fluid patch `fluid_to_solid` maps to solid patch `solid_to_fluid`.
- Solid patch `solid_to_fluid` maps to fluid patch `fluid_to_solid`.

`AllmeshCheck` records the native OpenFOAM evidence commands required before
the CHT runtime guard can be relaxed:

```bash
checkMesh -region fluid -allGeometry -allTopology
checkMesh -region solid -allGeometry -allTopology
```

FlowLab now parses matching region-scoped `checkMesh` stdout into
`logSummary.checkMeshRegions`. When those logs are available, the summary can
record separate `fluid` and `solid` entries with point/face/cell counts, max
aspect ratio, max/average non-orthogonality, max skewness, failed-check count,
and pass/completion flags.

The interface manifest now records a structured readiness report, not just a
boolean guard:

```json
{
  "schema": "flowlab.openfoam_cht_interface.v1",
  "interfaceApproximation": "outer-wall-offset-starter-sleeve",
  "productionReady": false,
  "readiness": {
    "multi-region-dictionaries": "pass",
    "paired-mapped-wall-patches": "pass",
    "source-mesh-quality": "pass",
    "non-overlapping-solid-jacket": "pass",
    "region-checkmesh-plan": "pass",
    "cht-boundary-layer-evidence": "fail",
    "region-checkmesh-evidence": "fail"
  },
  "regionMeshChecks": {
    "script": "AllmeshCheck",
    "evidenceStatus": "planned-not-executed",
    "commands": [
      "checkMesh -region fluid -allGeometry -allTopology",
      "checkMesh -region solid -allGeometry -allTopology"
    ]
  },
  "prismLayerPlan": {
    "file": "mesh/prism_layer_plan.json",
    "schema": "flowlab.prism_layer_plan.v1",
    "productionReady": false
  },
  "patches": {
    "fluid": {
      "name": "fluid_to_solid",
      "type": "mappedWall",
      "neighbourRegion": "solid",
      "neighbourPatch": "solid_to_fluid",
      "faceCount": 26
    },
    "solid": {
      "name": "solid_to_fluid",
      "type": "mappedWall",
      "neighbourRegion": "fluid",
      "neighbourPatch": "fluid_to_solid",
      "faceCount": 26
    }
  },
  "solidJacket": {
    "strategy": "outer-wall-offset-starter-sleeve",
    "cellCount": 26,
    "innerInterfaceFaceCount": 26,
    "outerWallFaceCount": 26,
    "nonOverlapping": true
  }
}
```

The manifest also records the FlowLab source mesh quality summary:

```json
{
  "qualityStatus": "ok",
  "pointCount": 56,
  "cellCount": 39,
  "regionCount": 3,
  "boundaryLayerLayers": 1,
  "transverseFractions": [0.0, 0.222222222, 0.777777778, 1.0]
}
```

## Runtime Guard

The job is intentionally blocked before Docker/native execution with:

```text
openfoam conjugate-heat-transfer runtime blocked: FlowLab generated split fluid/solid region polyMesh files with mapped-wall starter patches, but the interface manifest is not production-ready. Promote the starter solid sleeve to CAD-quality topology, add 3D boundary-layer/y-plus evidence, and collect per-region mesh-quality evidence before running full CHT.
```

## Interpretation

This replaces the previous missing-dictionary and missing-region-mesh CHT
guardrails. FlowLab now emits a real OpenFOAM v11 multi-region starter bundle
patterned after the Foundation `multiRegion/CHT` layout, plus split region
polyMesh directories with paired mapped-wall starter patches. The generated
`AllmeshCheck` and `caseValidation.chtInterface.regionMeshChecks` now make the
required per-region native mesh checks explicit. The job runner can execute
only `bash AllmeshCheck` as an OpenFOAM CHT mesh preflight when Docker or native
`checkMesh` is available, then records `logSummary.checkMeshRegions` while
leaving the job `blocked` before any `foamMultiRun` solve. The remaining
blocker is still auditable: region
dictionaries, mapped-wall pairing, source mesh quality, and a per-region check
plan are present, and the solid region is now an outward offset starter sleeve
rather than a duplicate fluid strip. `mesh/prism_layer_plan.json` records native
prism-layer sizing inputs for review, but there is still no generated production
3D prism-layer mesh or solver y-plus evidence, and per-region OpenFOAM
`checkMesh` evidence is not collected.

This does not yet prove a runnable CHT CFD solve. `foamMultiRun` remains blocked
until FlowLab promotes the starter solid sleeve into CAD-quality solid topology,
generates production boundary layers with y-plus evidence, collects and accepts
per-region mesh-quality checks, and records evidence that the interface is
production-ready.
