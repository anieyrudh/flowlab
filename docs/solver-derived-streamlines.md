# Solver-derived steady streamlines

FlowLab can integrate steady streamlines through a loaded solver velocity field
`U(x,y,z)`. The moving dots are passive visual tracers. They advance along
those already-computed steady curves. They are not transient pathlines and they
do not add CFD physics.

## Data and provenance contract

- Browser integration accepts only a complete VTK/VTU dataset. The rendered
  cells of that dataset must have an identity source-cell map. A thinned
  preview fails with the message `Full result required.`
- The backend endpoint `POST /api/jobs/{job_id}/artifact/streamlines` rereads
  the complete on-disk artifact. If you send
  `sourceRepresentation: "preview"`, it fails with the same message.
- Triangle and quad point fields use barycentric triangle interpolation.
  Tetrahedron, hexahedron, wedge, and pyramid point fields use deterministic
  barycentric tetrahedral decompositions. FlowLab labels the cell fields and
  samples them as piecewise constant.
- Every retained vertex records its rendered cell ID, source cell ID,
  interpolation point IDs, weights and method, and termination reason.
- A deterministic AABB index only narrows the candidate cells. Exact
  barycentric membership selects the rendered cell. The index never establishes
  source ownership.
- Imported artifacts, sparse previews, stale or ambiguous case links, and SU2
  results without a stable solver-cell identity stay probe-only.
- Streamline controls fail closed. They operate only if the full artifact has
  exactly one matching `resultComponentMap` cell-range binding. That binding
  must prove the complete source-cell order.

Velocity, pressure, temperature, phase fraction, and an explicitly loaded
vorticity field can colour the curves. FlowLab does not derive vorticity if the
result does not contain it. The `fieldInterpolations` map in the response
records whether each available field is barycentric or piecewise constant.

## Seeds and limits

User-plane seeds are deterministic. The defaults and hard limits are:

| Resource | Default | Maximum |
| --- | ---: | ---: |
| Seeds | 64 | 256 |
| Vertices per line | 1,024 | 1,024 |
| Total vertices | 65,536 | 65,536 |
| Passive sprites | up to one per seed | 256 |

Automatic inlet seeds are available only if the case generator wrote
`mesh/flowlab_boundary_faces.json`. That file must use the schema
`flowlab.boundary_faces.v1`. The manifest contains source cell IDs, face point
IDs, and centers. FlowLab captures these values while it generates the mesh.
FlowLab does not reconstruct inlet ownership from the result geometry.

## Execution boundary

Browser integration runs deterministic RK4 in a dedicated worker. To cancel the
integration, terminate that worker. Backend derivation is available for
complete artifacts that are larger than the browser ingestion limits.
Reduced-motion mode stops the passive tracer motion. It keeps the steady lines.

This visualization capability does not change the validation, promotion,
deployment, or release status of a result.
