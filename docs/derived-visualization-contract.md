# Derived visualization contract

Status: **visualization-only software feature; no scientific, promotion, or release state change**

FlowLab derives bounded presentation products from complete VTK/VTU solver
artifacts. It does not solve or change the governing equations, generate a new
CFD solution, reinterpret a campaign gate, or change retained solver evidence.

The backend is authoritative for parsing, hashes, topology, units, timestamps,
interpolation, source-cell provenance, deterministic pathlines, budgets, and
cache admission. The browser is authoritative only for bounded binary decoding,
GPU textures, transfer functions, raymarch presentation, internal cut planes,
presentation-only iso extraction, and animation.

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

A transient pathline request keeps the artifact order. It needs timestamps that
increase strictly:

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

Generated-job requests use `POST /api/jobs/{jobId}/derived`. At this endpoint,
every full source artifact must match exactly one explicit
`resultComponentMap` binding. The reserved `POST /api/derived/import` surface
fails closed with HTTP 409. Imported artifacts have no generated-case component
authority, thus imported results stay probe-only.

## Manifest and blobs

The response schema is `flowlab.derived_visualization_manifest.v1`. It records:

- the canonical request SHA-256;
- each source artifact path, byte size, SHA-256, geometry digest, cell-order
  digest, and optional timestamp;
- the generated-case unit contract;
- the narrowed explicit `resultComponentMap` used for every source artifact;
- grid bounds, dimensions, spacing, sample location, fields, gradients,
  pathline integration metadata, budgets, and visualization-only state;
- one descriptor per little-endian binary blob, including type, component
  count, tuple count, byte length, and SHA-256.

This is an abbreviated volume manifest. The real response also carries the full
field, interpolation, limit, and blob descriptor lists.

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
      "path": "postProcessing/flowlabNative/time_1.vtk",
      "time": 1.0,
      "size": 48192,
      "sha256": "415b7d9bd8edc464470de3868f5967445303b71cad7a3274d252e5c25b0bfc72",
      "geometryDigest": "fd6f86d5d06fd1ff39a231766860d03af87e86d8c7c17f20e00d982f4246cc38",
      "cellOrderDigest": "39e0c8f6025a511a9702f27b6290c47a4f656adcad5cfb1918c717f0f5e945aa"
    }
  ],
  "componentResolution": {
    "status": "source-cell-map",
    "reason": "Selections resolve only through source-cell IDs and explicit artifact-compatible bindings.",
    "map": { "version": 2, "artifactBindings": ["..."] }
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
vertex. Integration uses deterministic RK4 in space. It interpolates linearly
between compatible timestamped velocity frames.

Point fields use barycentric interpolation. That interpolation uses
prospectively fixed tetrahedral decompositions of linear tetrahedron,
hexahedron, wedge, and pyramid cells. Cell fields stay source-cell constant.

A sample can lie on more than one source cell. Such a sample has a
deterministic value, but FlowLab marks it as ambiguous and keeps it probe-only.
FlowLab permits schematic selection only if a non-ambiguous source cell has
exactly one owner in the explicit component map.

## Compatibility gates

Volume derivation rejects:

- sparse result previews;
- imported artifacts or any source without exactly one compatible explicit
  `resultComponentMap` binding;
- missing, malformed, non-finite, or incomplete topology or fields;
- surface cells, collapsed geometry, and planar SU2 output;
- unverified generated-case units;
- artifact paths outside the case or under `mesh/`;
- artifact sets or output residency above the declared budgets.

Transient pathlines also reject:

- timestamps that do not increase, and timestamps that are missing;
- geometry-digest drift;
- cell-order drift;
- an incompatible field location, kind, or unit; and
- a missing `U` or `Velocity` vector at any frame.

## Fixed budgets

| Surface | Limit | Behavior |
|---|---:|---|
| Default volume grid | 64³ | explicit request |
| Maximum volume grid | 96³ | reject above |
| Complete source artifact set | 48 MiB | reject above |
| Browser decoded residency | 96 MiB | backend and browser both reject above |
| Per-job derived cache | 256 MiB | reject new admission above |
| Seeds | 512 | reject above |
| Pathline vertices | 250,000 | reject the whole product above |
| Presentation-only iso triangles | 500,000 | reject the whole extraction above |

No limit silently clamps or truncates a requested derived product.

## Rendering and limitations

- WebGL2 uses 3D value, validity, source-cell, and ambiguity textures. A
  translucent raymarch, internal cut plane, optional presentation-only
  iso-surface, deterministic pathlines, and animated passive sprites share the
  same physical bounds.
- WebGL1 keeps the ordinary exterior VTK surface and reports
  `webgl2-required`. It does not fabricate a volume fallback.
- Iso triangles keep the contributing source-cell IDs. A triangle with more
  than one contributor or with an ambiguous vertex is probe-only.
- The current parser supports ASCII legacy VTK and ASCII VTU linear cells. It
  does not claim support for binary/appended VTU, polyhedra, higher-order cells,
  adaptive grids, arbitrary tensor fields, or a general-purpose visualization
  file format.
- Parsing, spatial indexing, voxelization, and pathline integration currently
  run synchronously in the local backend request. Large admitted grids can be
  CPU-intensive even though memory is bounded.
- The derived cache is process-local and ephemeral. If you restart the backend,
  FlowLab discards the cache. FlowLab must then regenerate it deterministically
  from the full source artifact.
- Derived output can visualize experimental CFD. It cannot validate that CFD,
  authorize a preset, change a benchmark pointer, or make a desktop candidate
  externally releasable.
