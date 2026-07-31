# Derived visualization contract

Status: **visualization-only software feature; no scientific, promotion, or release state change**

FlowLab derives bounded presentation products from complete VTK/VTU solver
artifacts. It does not solve or alter the governing equations, generate a new
CFD solution, reinterpret a campaign gate, or modify retained solver evidence.
The backend is authoritative for parsing, hashes, topology, units, timestamps,
interpolation, source-cell provenance, deterministic pathlines, budgets, and
cache admission. The browser is authoritative only for bounded binary
decoding, GPU textures, transfer functions, raymarch presentation, internal
cut planes, presentation-only iso extraction, and animation.

## Request examples

A steady volume request uses exactly one complete artifact:

```json
{
  "schema": "flowlab.derived_visualization_request.v1",
  "operation": "volume",
  "artifacts": [
    {
      "path": "postProcessing/flowlabNative/time_1.vtk",
      "time": 1.0
    }
  ],
  "fields": [
    {
      "name": "U",
      "location": "cell",
      "kind": "vector",
      "unit": "m/s"
    },
    {
      "name": "p",
      "location": "cell",
      "kind": "scalar",
      "unit": "m2/s2"
    }
  ],
  "grid": {
    "dimensions": [64, 64, 64],
    "gradients": ["speed", "pressure"]
  }
}
```

A transient pathline request preserves artifact order and requires strictly
increasing timestamps:

```json
{
  "schema": "flowlab.derived_visualization_request.v1",
  "operation": "pathlines",
  "artifacts": [
    {
      "path": "postProcessing/flowlabNative/time_0.vtk",
      "time": 0.0
    },
    {
      "path": "postProcessing/flowlabNative/time_1.vtk",
      "time": 1.0
    }
  ],
  "fields": [
    {
      "name": "U",
      "location": "point",
      "kind": "vector",
      "unit": "m/s"
    }
  ],
  "pathlines": {
    "seeds": [[0.0, 0.005, 0.0]],
    "stepSeconds": 0.01,
    "maxVertices": 250000
  }
}
```

Generated-job requests use `POST /api/jobs/{jobId}/derived`. Imported results
use `POST /api/derived/import` with the same request plus the exact full
artifact text. Imported results never acquire generated-case schematic
authority: their derived selections remain probe-only.

## Manifest and blobs

The response schema is `flowlab.derived_visualization_manifest.v1`. It records:

- the canonical request SHA-256;
- each source artifact path, byte size, SHA-256, geometry digest, cell-order
  digest, and optional timestamp;
- whether units came from a generated case contract or an explicit user
  declaration for an import;
- the explicit `resultComponentMap`, when one is available;
- grid bounds, dimensions, spacing, sample location, fields, gradients,
  pathline integration metadata, budgets, and visualization-only state;
- one descriptor per little-endian binary blob, including type, component
  count, tuple count, byte length, and SHA-256.

An abbreviated volume manifest looks like this (the real response also carries
the full field, interpolation, limit, and blob descriptor lists):

```json
{
  "schema": "flowlab.derived_visualization_manifest.v1",
  "requestSchema": "flowlab.derived_visualization_request.v1",
  "requestSha256": "b7826a80c48632d93152eb2a512a6f81d1c4bb27fc1f5ca4b35fa5bb5d8ec72c",
  "operation": "volume",
  "visualizationOnly": true,
  "scientificStateEffect": "none",
  "releaseStateEffect": "none",
  "sourceArtifacts": [
    {
      "path": "VTK/result.vtk",
      "time": 1.0,
      "size": 48192,
      "sha256": "415b7d9bd8edc464470de3868f5967445303b71cad7a3274d252e5c25b0bfc72",
      "geometryDigest": "fd6f86d5d06fd1ff39a231766860d03af87e86d8c7c17f20e00d982f4246cc38",
      "cellOrderDigest": "39e0c8f6025a511a9702f27b6290c47a4f656adcad5cfb1918c717f0f5e945aa"
    }
  ],
  "componentResolution": {
    "status": "probe-only",
    "reason": "Imported artifacts have no generated-case component authority.",
    "map": null
  },
  "grid": {
    "dimensions": [64, 64, 64],
    "voxelCount": 262144,
    "sampleLocation": "voxel-center"
  },
  "provenance": {
    "sourceCellIds": {
      "schema": "flowlab.derived_visualization_blob.v1",
      "name": "source-cell-ids.bin",
      "dtype": "uint32",
      "components": 1,
      "count": 262144,
      "byteOrder": "little-endian",
      "byteLength": 1048576,
      "sha256": "50bc5e4c4a3b7ae3100925b63d4f578cd8d23f77e4d69449359258f8d75ef43f"
    },
    "ambiguousSelections": "probe-only"
  }
}
```

Volume products contain:

- field values as `float32`;
- a `uint8` validity mask;
- `uint32` source-cell IDs;
- `uint8` tetrahedral subcell IDs;
- four `float32` barycentric weights per voxel;
- a separate `uint8` ambiguity mask;
- gradient values and a separate gradient-validity mask when requested.

Pathline products contain positions, timestamps, path offsets, source-cell IDs,
subcell IDs, barycentric weights, and ambiguity flags for every retained
vertex. Integration is deterministic RK4 in space with linear interpolation
between compatible timestamped velocity frames.

Point fields use barycentric interpolation on prospectively fixed tetrahedral
decompositions of linear tetrahedron, hexahedron, wedge, and pyramid cells.
Cell fields remain source-cell constant. A sample that lies on more than one
source cell has a deterministic value but is marked ambiguous and remains
probe-only. Schematic selection is permitted only when a non-ambiguous source
cell has exactly one owner in the explicit component map.

## Compatibility gates

Volume derivation rejects:

- sparse result previews;
- missing, malformed, non-finite, or incomplete topology or fields;
- surface cells, collapsed geometry, and planar SU2 output;
- unverified generated-case units or placeholder imported units;
- artifact paths outside the case or under `mesh/`;
- artifact sets or output residency above the declared budgets.

Transient pathlines additionally reject non-increasing or missing timestamps,
geometry-digest drift, cell-order drift, incompatible field location/kind/unit,
or a missing `U`/`Velocity` vector at any frame.

## Fixed budgets

| Surface | Limit | Behavior |
|---|---:|---|
| Default volume grid | 64³ | explicit request |
| Maximum volume grid | 96³ | reject above |
| Complete source artifact set | 48 MiB | reject above |
| Browser decoded residency | 96 MiB | backend and browser both reject above |
| Per-job/import derived cache | 256 MiB | reject new admission above |
| Seeds | 512 | reject above |
| Pathline vertices | 250,000 | reject the whole product above |
| Presentation-only iso triangles | 500,000 | reject the whole extraction above |

No limit silently clamps or truncates a requested derived product.

## Rendering and limitations

- WebGL2 uses 3D value, validity, source-cell, and ambiguity textures. A
  translucent raymarch, internal cut plane, optional presentation-only
  iso-surface, deterministic pathlines, and animated passive sprites share the
  same physical bounds.
- WebGL1 retains the ordinary exterior VTK surface and reports
  `webgl2-required`; it does not fabricate a volume fallback.
- Iso triangles retain contributing source-cell IDs. A triangle with multiple
  contributors or any ambiguous vertex is probe-only.
- The current parser supports ASCII legacy VTK and ASCII VTU linear cells. It
  does not claim support for binary/appended VTU, polyhedra, higher-order cells,
  adaptive grids, arbitrary tensor fields, or a general-purpose visualization
  file format.
- Parsing, spatial indexing, voxelization, and pathline integration currently
  run synchronously in the local backend request. Large admitted grids can be
  CPU-intensive even though memory is bounded.
- The derived cache is process-local and ephemeral. Restarting the backend
  discards it and requires deterministic regeneration from the full source
  artifact.
- Imported unit declarations are presentation metadata supplied by the user,
  not independent scientific verification.
- Derived output can visualize experimental CFD. It cannot validate that CFD,
  authorize a preset, change a benchmark pointer, or make a desktop candidate
  externally releasable.

## Integration conflict notes

This branch starts directly from the Prompt 1 provenance baseline and does not
merge sibling prompt work. The active solver-streamline worktree also changes
`server/app.py`, schemas, `src/App.tsx`, `SimulationCanvas.tsx`,
`cinemaRenderer.ts`, backend services, CSS, and E2E tests. The active preview
governance worktree overlaps `src/App.tsx`, `SimulationCanvas.tsx`, CSS, and E2E
tests. The committed axisymmetric-geometry qualification branch overlaps result
parsing, schemas, provenance, App/types, and E2E surfaces. Those branches need a
deliberate semantic integration: keep backend-derived provenance and budgets
authoritative, retain preview exclusion, and avoid installing two independent
streamline/pathline implementations.
