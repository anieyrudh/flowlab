# Solver-derived steady streamlines

FlowLab can integrate steady streamlines through a loaded solver velocity field
`U(x,y,z)`. The moving dots are passive visual tracers that advance along those
already-computed steady curves; they are not transient pathlines and do not add
CFD physics.

## Data and provenance contract

- Browser integration accepts only a complete VTK/VTU dataset whose rendered
  cells have an identity source-cell map. A thinned preview fails with
  `Full result required.`
- The backend endpoint `POST /api/jobs/{job_id}/artifact/streamlines` rereads the
  complete on-disk artifact. Sending `sourceRepresentation: "preview"` fails
  with the same message.
- Triangle and quad point fields use barycentric triangle interpolation.
  Tetrahedron, hexahedron, wedge, and pyramid point fields use deterministic
  barycentric tetrahedral decompositions. Cell fields are labelled and sampled
  as piecewise constant.
- Every retained vertex records its rendered cell ID, source cell ID,
  interpolation point IDs, weights and method, plus its termination reason.
  A deterministic AABB index only narrows candidate cells; exact barycentric
  membership selects the rendered cell. It never establishes source ownership.
- Imported full artifacts keep artifact-local cell IDs and remain unlinked.
  Generated OpenFOAM results are linked only when their explicit case result map
  proves the complete solver-cell order. SU2 user-plane support remains
  explicitly two-dimensional and unlinked until stable solver-cell identity is
  proven.

Velocity, pressure, temperature, phase fraction, and an explicitly loaded
vorticity field can colour the curves. FlowLab does not derive vorticity when
the result does not contain it. The response's `fieldInterpolations` map records
whether each available field is barycentric or piecewise constant.

## Seeds and limits

User-plane seeds are deterministic. The defaults and hard limits are:

| Resource | Default | Maximum |
| --- | ---: | ---: |
| Seeds | 64 | 256 |
| Vertices per line | 1,024 | 1,024 |
| Total vertices | 65,536 | 65,536 |
| Passive sprites | up to one per seed | 256 |

Automatic inlet seeds are unavailable unless the case generator authored
`mesh/flowlab_boundary_faces.json` with schema
`flowlab.boundary_faces.v1`. The manifest contains source cell IDs, face point
IDs, and centers captured while the mesh is generated; FlowLab does not
reconstruct inlet ownership from result geometry.

## Execution boundary

Browser integration runs deterministic RK4 in a dedicated worker and can be
cancelled by terminating that worker. Backend derivation exists for complete
artifacts that exceed browser ingestion limits. Reduced-motion mode freezes
passive tracer motion while retaining the steady lines.

This visualization capability does not change a result's validation,
promotion, deployment, or release status.
