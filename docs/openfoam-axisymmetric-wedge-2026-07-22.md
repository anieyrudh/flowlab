# Axisymmetric wedge pipe meshing — product status and remaining gates (updated 2026-07-23)

Goal: let a FlowLab "pipe" edge produce a **true 3D circular-pipe** mesh that satisfies
the 3D Hagen–Poiseuille law `Δp = 128·μ·L·Q/(π·D⁴)`, instead of the current 2D
one-cell-thick planar strip (`frontAndBack empty`, gap `H = diameter·3.6`).

## Status: integrated software path; candidate campaign passed; controlled review pending

`solver.meshMode: "axisymmetric"` is live for incompressible flow. It now compiles
one straight, collinear, non-branching circular source-to-sink path with pipe,
Venturi, expansion, contraction, and nozzle edges into a conformal multi-block
wedge. Invalid topology, unsupported physics, and diameter discontinuities fail
closed. `validate_solver_case` checks the canonical SI profile and the non-planar
3D inspection artifact before execution.

The software path has passed local `blockMesh`, `checkMesh`, and steady solver
smokes in the pinned OpenFOAM container. The governed product-path campaign
`2026-07-23-axisymmetric-product-v1` has also completed its frozen coarse,
medium, and fine levels. Its immutable package passes the candidate numerical
gates, but controlled independent review is still pending. The fixture and
registry therefore remain unchanged.

### Governed candidate result

| Quantity | Coarse | Medium | Fine / result |
| --- | ---: | ---: | ---: |
| Logical grid | 16 x 4 | 32 x 8 | 64 x 16 |
| Pressure drop | 0.457474056 Pa | 0.467655816 Pa | 0.470355072 Pa |
| Mass-flow imbalance | 0 | 0 | 0 |
| Observed order |  |  | 1.9153532495650476 |
| Fine-grid GCI |  |  | 0.25877682173974026% |
| Fine pressure-drop error |  |  | 0.25767738276188347% |

The retained campaign lives under
`benchmarks/cases/straight-pipe/campaigns/2026-07-23-axisymmetric-product-v1/`.
The read-only package has tree digest
`d57fc73f4fc2727c8cdb6fea1ff2aba32fed862e0216e758b66f9eb8726be056`
and is bound to source commit
`4fa840bb852be23ccd9445c8a3f5283ef335ee8f` plus the immutable container
digest recorded in its manifest. These hashes establish identity, not
independent approval. The
[independent AI technical review](axisymmetric-straight-pipe-independent-ai-review-2026-07-23.md)
reproduced the metrics and hashes, but placed promotion on hold because the
fixture's patch-average pressure operator does not match the periodic forcing
operator used by the campaign. That conflict requires a new prospective
contract decision; it is not being reinterpreted post hoc.

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
  running or promoting them. Its separate `--run-and-package` action requires
  clean frozen source plus an inspectable pinned Docker image, executes all
  three cases through `JobManager`, evaluates the retained runtime evidence,
  and writes a content-hashed read-only candidate package. It never edits the
  fixture or registry.

## Scope notes / limitations
- Axisymmetric paths must remain circular, straight, collinear, non-branching, and
  incompressible. Elbows, branches, and non-circular sections need a full 3D
  topology such as the O-grid/native CAD meshing path.
- The editor/result canvas displays the real 3D wedge surface. A stationary
  click now ray-casts that exterior surface, preserves the owning solver cell,
  samples the active point/cell field, and reports solver-space XYZ. Object
  editing retains pointer priority when a node or port overlaps the surface.
- The straight-pipe fixture now formally requires this product path and its 3D
  runtime evidence. The three cases and frozen numerical gates are complete,
  but the fixture remains `pending-real-run` until a controlled independent
  reviewer approves the exact immutable package.
- The non-axisymmetric full O-grid is a separate geometry milestone with its own
  topology, verification, evidence, and review contract. See
  [non-axisymmetric O-grid milestone](non-axisymmetric-o-grid-milestone-2026-07-23.md).
