# Campaign plan

## Objective

Determine which predeclared portion of the steady incompressible Newtonian
laminar, structured Cartesian all-hex, open-pressure-boundary envelope is
supported by code verification, solution verification, analytic force
reconciliation, reproducibility, negative controls, and experimental data.

## Immutable execution boundary

- Campaign ID: `laminar-all-hex-v2`
- OpenFOAM image: `flowlab/openfoam11-gmsh:2026-07-13`
- Image digest:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Solver: OpenFOAM 11 `incompressibleFluid`
- Velocity solver: PBiCGStab/DILU
- Boundary contract: exact fixed inlet/outlet kinematic pressure traces;
  `pressureInletOutletVelocity` with exact tangential velocity and a
  solver-compatible normal component
- Mobile: out of scope
- Performance claims: disabled during concurrent or emulated execution

## Lanes

The affine three-grid, non-affine MMS three-grid, 72-cell physical matrix,
reproducibility checks, negative controls, experimental/UQ work, and desktop
promotion tests may execute concurrently after preflight. Results remain
provisional until their declared acceptance dependencies pass.

No campaign failure authorizes a threshold, solver, mesh, boundary-condition,
or analytic-definition change. Such a change requires a new campaign ID.

## Promotion policy

The nested claims are predeclared to prevent post-hoc cherry-picking:

1. baseline point;
2. Reynolds extension at baseline geometry;
3. geometry and cell-aspect extension;
4. direction-reversal extension;
5. experimentally validated envelope.

Only a complete predeclared inner claim may be accepted when an outer claim
fails. Missing evidence is `incomplete-infrastructure`, not a scientific pass.
