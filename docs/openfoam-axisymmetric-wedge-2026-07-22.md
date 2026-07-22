# Axisymmetric wedge pipe meshing — validated approach + integration plan (2026-07-22)

Goal: let a FlowLab "pipe" edge produce a **true 3D circular-pipe** mesh that satisfies
the 3D Hagen–Poiseuille law `Δp = 128·μ·L·Q/(π·D⁴)`, instead of the current 2D
one-cell-thick planar strip (`frontAndBack empty`, gap `H = diameter·3.6`).

## Status: INTEGRATED into the product pipeline and validated end-to-end.

`solver.meshMode: "axisymmetric"` is live. A straight circular pipe generated through
the FlowLab pipeline (`generate_case` -> `JobManager`, `runMode: "steady"`) passes
`validate_solver_case` with **0 issues**, runs (blockMesh + checkMesh, snappy skipped),
conserves mass to ~1e-8, and reports **Δp = 199.40 vs Hagen-Poiseuille 199.30 (0.1%)**.
Exposed as a UI toggle (Advanced solvers -> Mesh mode). The default `planar-2d` path is
byte-identical (full backend suite: 486 pass, only pre-existing evidence failures).

## What was validated

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

**Conclusion:** the axisymmetric wedge is the correct, low-code path to true 3D
circular-pipe physics. Full all-hex O-grid (as in `straight_pipe_runner.py`) is the
gold standard but far more code; defer it until non-axisymmetric geometry is needed.

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

## Integration plan (behind `solver.meshMode`, default `planar-2d`)

Keep the default `planar-2d` path byte-identical so all existing tests pass; branch
only when `meshMode == "axisymmetric"`.

1. **`mesh.py` `_mesh_controls`** — read `meshMode ∈ {planar-2d (default), axisymmetric}`.
2. **`mesh.py`** — add `axisymmetric_pipe_block_mesh_dict(mesh, controls)` that maps a
   single straight circular-pipe region to the wedge dict above: `R = diameter/2`
   (physical), `L` from the axial station extent, `nAxial = segmentCount`,
   `nRadial` from the transverse cell count (reuse `_transverse_fractions` for radial
   grading; uniform recommended).
3. **`adapters.py` (`OpenFOAMAdapter.generate_case`, ~line 4580)** — when axisymmetric:
   set `system/blockMeshDict` to the wedge dict, set `openfoam_mesh_files = {}` (skip
   the fitted `constant/polyMesh` so Allrun runs blockMesh), and emit wedge field BCs.
4. **`adapters.py` field writers** (`_openfoam_vector_field` ~323, `_openfoam_pressure_field`
   ~354) — emit `front`/`back` `type wedge` + `axis` `type empty` instead of
   `frontAndBack empty` when axisymmetric.
5. **`execution.py` `validate_solver_case` (~3352-3360)** — make the required patch set
   `meshMode`-aware: axisymmetric expects `inlet/outlet/wall/front/back` (not `frontAndBack`).
6. **Tests + validation** — new test for axisymmetric generation; validate end-to-end by
   generating an axisymmetric pipe through the pipeline and comparing the computed Δp to
   `128·μ·L·Q/(π·D⁴)` (harness: `scratchpad/wedge-proto/`).

## Scope notes / limitations
- Axisymmetric only (circular pipes/ducts). Non-axisymmetric geometry needs the O-grid.
- VTK preview still exports the 2D strip (`mesh_to_legacy_vtk` is strip-based); the 3D
  shape shows only in the solved fields, not the editor preview.
- The straight-pipe 3D Hagen–Poiseuille benchmark fixture becomes satisfiable by the
  product pipeline once this lands (today only the science runner can).
