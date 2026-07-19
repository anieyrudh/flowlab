# CAD Cylinder Geometry Gate

## Decision

Do not promote the fixed 64-facet O-grid as circular-pipe geometry validation.
The captured `ogrid-v2` result is a useful fixed-geometry regression candidate,
but its 0.1605607% polygonal area deficit and fixed core/wall resolution remain
outside its two-direction spatial GCI.

The attempted 128-facet/32-by-32-core O-grid suite at the existing three
spacings is retained at
`benchmarks/cases/straight-pipe/runs/2026-07-13-ogrid-128-v1` as an invalid
preflight. Its coarse mesh failed `checkMesh` before solving because 2,048
faces had interpolation weight below 0.05. Increasing wall facets and core
cells co-refines wall representation, tangential resolution, and core
resolution; it is a combined sensitivity, not isolated geometry error.

The production-quality next path is a distinct `gmsh-cad-cylinder-v1` family.
It must not be merged into or used to reinterpret existing periodic O-grid
evidence.

## Required mesh contract

1. Create one CAD cylinder surface master at a declared surface resolution.
   Start with 256 circumferential chords: its inscribed-polygon area deficit is
   about 0.01004%, compared with 0.16056% at 64. Archive the source `.geo`,
   surface mesh, point/connectivity SHA-256, area, perimeter, chord length,
   and sagitta/error metrics.
2. Freeze that exact surface master across all three volume meshes. Evidence
   packaging must reject a suite when surface SHA-256 or declared geometry
   metrics differ between levels.
3. Run spatial refinement only in the volume behind the frozen surface, then
   calculate GCI from the three pressure-drop QoIs.
4. At the selected volume grid, run a separate 128/256/(preferably 512)
   surface-master sensitivity. Report it as geometry-representation sensitivity;
   never call it pure geometry error if tangential/volume resolution changes
   with the surface.

## Foundation OpenFOAM v11 preflight

The pinned image contains Gmsh 4.4.1 and `gmshToFoam`. It supports a CAD
OpenCASCADE cylinder exported as MSH 2.2, converted to ordinary `wall`,
`inlet`, and `outlet` OpenFOAM patches. `cyclicAMI` is unavailable in this
Foundation v11 image, so v1 must use a fully developed parabolic inlet and a
pressure outlet rather than the periodic `meanVelocityForce` O-grid
formulation.

The generic all-h Gmsh template is insufficient for frozen-geometry GCI by
itself. A probe showed that default 3-D meshing left 52 under-determined cells.
The following options are mandatory in the preflight before any solver result
is considered:

```text
Mesh.Algorithm3D = 4
Mesh.Optimize = 1
Mesh.OptimizeNetgen = 1
Mesh.OptimizeThreshold = 0.2
Mesh.Smoothing = 20
Mesh.MshFileVersion = 2.2
```

With those controls, a representative Gmsh-to-OpenFOAM probe passed
`checkMesh` (3,372 tetrahedra; max non-orthogonality 52.325, maximum skewness
0.6595, minimum determinant 0.05894). Those are preflight observations, not
acceptance thresholds for the future V&V suite.

The retained local preflight source, MSH2 volume mesh, imported `polyMesh`,
logs, report, and hash index are in
`benchmarks/cases/straight-pipe/runs/2026-07-13-gmsh-cad-preflight-v1`.

## Current blockers

The CAD route is not yet ready to implement as a three-grid V&V runner.
Evidence for both blockers is retained in
`benchmarks/cases/straight-pipe/runs/2026-07-13-cad-smoke-and-surface-blockers-v1`.

- Gmsh 4.4.1 did not create volume tetrahedra after importing a frozen MSH2
  surface and running `CreateTopology; Mesh 3`. A newer, isolated Gmsh 4.15.2
  Python-API experiment now proves that an explicit volume around a normalized
  256-chord, physical-tagged discrete surface can preserve the exact canonical
  patch-aware surface hash across three interior resolutions. The generated
  tetrahedral meshes nevertheless fail `checkMesh` quality checks and remain
  rejected. Its retained evidence is
  `benchmarks/cases/straight-pipe/runs/2026-07-13-gmsh415-immutable-probe-v1`.
- A subsequent Gmsh 4.15.2 prism-shell/core screen retained that same frozen
  master exactly across all eight executable 2/4/8-layer by 0.5/1/2-chord
  candidates. Every candidate nonetheless failed `checkMesh` on small
  determinants and low interpolation weights; the closest four-layer,
  one-chord candidate still had 525 and 11 respectively. The ninth candidate
  was rejected before Gmsh because its shell consumed more than half the pipe
  radius. This is a feasibility rejection under the current exact surface,
  not a basis to alter it silently. Evidence is retained in
  `benchmarks/cases/straight-pipe/runs/2026-07-13-layered-immutable-screen-v1`.
- The separately versioned v2 master replaces the cap centre fan with
  concentric rings, uses chord-matched axial wall spacing, and adds an
  orientation-sensitive surface fingerprint. Its full one-level shell/core
  screen selected the 8-layer, half-chord family with zero `checkMesh`
  failures. Evidence is retained in
  `benchmarks/cases/straight-pipe/runs/2026-07-13-layered-immutable-screen-v2-r3`.
  The requested 0.003/0.002/0.0015 m continuation also passed individual mesh
  gates, but every output had the same 5,204,514 cells and identical quality
  metrics: current Gmsh sizing is dominated by the frozen interface and did
  not produce distinct refinement levels. It is therefore rejected for smoke,
  GCI, and timing until a new volume-size-control path proves monotonically
  increasing cells behind the unchanged v2 surface. See
  `benchmarks/cases/straight-pipe/runs/2026-07-13-layered-immutable-three-core-v2-r1/artifacts/refinement-validity-report.json`.
- The next, separately versioned volume campaign is required to use the
  `coarsened-inner-interface` v1 strategy: exact frozen v2 surface, dense
  prism shell, a nonphysical coarsened cylindrical interface, a Gmsh annular
  transition, then a separately meshed core. Its one-level screen must bind
  the interface chord count and transition thickness. A three-level report is
  accepted only if imported cell counts and measured core tetrahedron counts
  strictly increase while the effective core spacing measured from the
  retained core-debug MSH2 strictly decreases. The former v2 screen cannot be
  reused for this strategy.
- The v3 `coarsened-inner-interface` v1 continuation was also rejected: the
  fixed 64-chord inner interface produced identical 1,724,219-cell outputs at
  all three requested core sizes. The v4 campaign preserves the exact frozen
  v2 surface and selected shell controls, but binds a separately versioned,
  core-size-dependent internal-interface schedule of 64/96/128 chords for
  0.003/0.002/0.0015 m respectively. Its fresh one-level screen selected the
  four-layer, one-wall-chord configuration, and its rebuilt three-level family
  passed all strict mesh gates: 1,724,219/1,896,949/2,410,917 imported cells;
  82,644/189,160/602,497 measured core tetrahedra; and
  0.000551604/0.000418682/0.000284591 m effective core spacing. Every level
  retained the exact canonical and orientation-sensitive v2 surface hashes,
  three required patches, and zero `checkMesh` failures. Evidence is retained
  under `benchmarks/cases/straight-pipe/runs/2026-07-14-layered-immutable-screen-v4-r1`
  and `benchmarks/cases/straight-pipe/runs/2026-07-14-layered-immutable-three-core-v4-r1`.
  This advances only to the parabolic-inlet smoke; it is not CFD V&V, GCI, or
  native-performance evidence. The v3 evidence cannot be reused or
  reinterpreted as v4 evidence.
- A Foundation v11 `codedFixedValue` parabolic inlet compiled and imposed the
  exact discrete flow on the passing preflight mesh, but the steady smoke did
  not meet outer residual or pressure-QoI plateau criteria and its pressure
  drop had the wrong sign. It is a failed converged-CFD smoke, not V&V data.

Do not add a CAD-cylinder three-grid runner until the demonstrated
in-process 2-D-once/3-D-volume workflow also produces a mesh that passes all
required `checkMesh` gates and then passes the serial pressure-QoI smoke.

## Execution order

1. Generate the CAD surface master, then run `gmsh -3 -format msh2`,
   `gmshToFoam`, and `checkMesh -allGeometry -allTopology` for each planned
   volume configuration. Retain every source, mesh, conversion log, imported
   `polyMesh`, and mesh report.
2. Gate patch names, positive determinants, no concavity, and `Mesh OK`; hash
   and compare frozen-surface artifacts across the three volume levels.
3. Generate the parabolic inlet profile from the declared volumetric flow rate
   and verify its analytic integral. Use area-averaged inlet/outlet pressure
   difference for pressure drop, not periodic forcing gradient.
4. Run one serial smoke solve. Require residual/continuity convergence, QoI
   plateau evidence, mass balance, flow-target agreement, and all retained
   fields/logs before launching the three-grid suite.
5. Run the frozen-surface three-grid V&V, evaluate analytical error and GCI,
   then independently review it as analysis-only. Only an accepted geometry
   suite may enter the native repeated-timing protocol.

## After geometry is accepted

Expand physics in this order: a fully developed non-circular duct with a
separate reference, then a developing-flow case with an entrance-length or
experimental reference, then a transient with time-step refinement. Each case
gets separate geometry, QoI, conservation, convergence, and GCI gates.
