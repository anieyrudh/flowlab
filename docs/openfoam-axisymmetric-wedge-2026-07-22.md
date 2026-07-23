# Axisymmetric wedge pipe meshing — product status and remaining gates (updated 2026-07-23)

Goal: let a FlowLab "pipe" edge produce a **true 3D circular-pipe** mesh that satisfies
the 3D Hagen–Poiseuille law `Δp = 128·μ·L·Q/(π·D⁴)`, instead of the current 2D
one-cell-thick planar strip (`frontAndBack empty`, gap `H = diameter·3.6`).

## Status: integrated software path; scientific fixture still pending

`solver.meshMode: "axisymmetric"` is live for incompressible flow. It now compiles
one straight, collinear, non-branching circular source-to-sink path with pipe,
Venturi, expansion, contraction, and nozzle edges into a conformal multi-block
wedge. Invalid topology, unsupported physics, and diameter discontinuities fail
closed. `validate_solver_case` checks the canonical SI profile and the non-planar
3D inspection artifact before execution.

The software path has passed local `blockMesh`, `checkMesh`, and steady solver
smokes in the pinned OpenFOAM container. Those runs are experimental smoke
evidence only. They are not the governed three-grid campaign, independent
validation, promotion authorization, or external release evidence.

## What the smoke established

A minimal **axisymmetric wedge** (a ~5° wedge revolved about the pipe axis, OpenFOAM
`wedge` front/back patches, collapsed axis) was run through the pinned container
`flowlab/openfoam11-gmsh:2026-07-13` (amd64 emulated):

- `blockMesh` + `checkMesh`: **Mesh OK** (5000 cells, max non-orthogonality 0,
  max aspect ratio 20, max cell openness 1.6e-16). The `wedge` patches and singular
  axis are handled natively by blockMesh.
- Steady `foamRun -solver incompressibleFluid` (laminar, Re=10) converges.
- Result: kinematic **Δp = 0.337 m²/s²** vs analytical Hagen–Poiseuille
  `32·ν·U·L/D² = 0.32` (+5% entrance excess, expected for a plug inlet at Re=10).
  The 2D plane-channel law for the same inputs gives 0.12 — so the wedge is
  unambiguously producing the **circular** `πD⁴` result, not the planar one.

**Conclusion:** the axisymmetric wedge is a viable true-3D circular-pipe software
path. The full all-hex O-grid remains an independent topology reference and the
natural route for non-axisymmetric geometry. Neither topology is promoted by this
smoke.

## Proven reference: the wedge blockMeshDict (single straight pipe, R, L)

Axis along x; wedge symmetric about z=0; wall at y=R; `t = tan(half_angle)`.
For R=0.05, L=1.0, half=2.5° (`R·t = 0.002183045`):

```
vertices
(
    (0  0     0)   (1  0     0)   (1  R  -R*t)  (0  R  -R*t)
    (0  0     0)   (1  0     0)   (1  R   R*t)  (0  R   R*t)
);
blocks ( hex (0 1 2 3 4 5 6 7) (nAxial nRadial 1) simpleGrading (1 1 1) );
defaultPatch { name axis; type empty; }
boundary
(
    inlet  { type patch; faces ( (0 4 7 3) ); }
    outlet { type patch; faces ( (1 2 6 5) ); }
    wall   { type wall;  faces ( (2 3 7 6) ); }
    front  { type wedge; faces ( (4 5 6 7) ); }
    back   { type wedge; faces ( (0 3 2 1) ); }
);
```

Field BCs (`0/U`, `0/p`): `front`/`back` → `type wedge`; `axis` → `type empty`;
inlet/outlet/wall as usual (no `frontAndBack`).

## Current implementation

- `adapters.py` emits a canonical `flowlab.axisymmetric-profile.v1` SI profile,
  conformal multi-block `blockMeshDict`, wedge-aware fields and physical probes.
- `mesh.py` retains the original 2D source strip while exporting a 3D
  blockMesh-equivalent VTK/VTU preview with hexahedral cell types.
- `execution.py` skips the planar surface/snappy stage, requires the
  inlet/outlet/walls/front/back/axis patch contract, and validates the 3D preview.
- The result renderer extracts only exterior faces from hex, wedge, tetrahedron,
  pyramid, quad, triangle, and polygon cells instead of drawing internal faces.
- `axisymmetricAxialCells` and `axisymmetricRadialCells` freeze exact single-edge
  logical refinements.
- `axisymmetricBenchmark` switches the frozen straight-pipe candidate to cyclic
  inlet/outlet patches with `meanVelocityForce`, records pressure/flow unit and
  full-circle scaling rules, and remains explicitly `pending-real-run`.
- `axisymmetric_straight_pipe_campaign.py --materialize-only` produces coarse
  16x4, medium 32x8, and fine 64x16 cases through the product adapter without
  running or promoting them.

## Scope notes / limitations
- Axisymmetric paths must remain circular, straight, collinear, non-branching, and
  incompressible. Elbows, branches, and non-circular sections need a full 3D
  topology such as the O-grid/native CAD meshing path.
- The editor/result canvas displays the real 3D wedge surface, but XYZ-aware
  ray-cast probing is still pending.
- The straight-pipe fixture now formally requires this product path and its 3D
  runtime evidence. It stays `pending-real-run` until the three cases execute,
  the postprocessor reconstructs full-circle QoIs, every frozen gate passes, and
  an independent review approves the immutable evidence package.
