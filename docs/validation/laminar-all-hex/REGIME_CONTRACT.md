# Regime contract

## Included

- Steady, incompressible, Newtonian, laminar flow
- Structured Cartesian all-hex meshes
- Height Reynolds numbers `4.17`, `16.67`, and `66.7`
- Forward and reversed pressure gradients
- Length/height ratios `1` and `4`
- Axial/transverse cell-aspect ratios `1` and `2`
- `12`, `24`, and `48` cells per channel height
- Exact analytic initialization
- The pinned pressure/velocity boundary and numerical contract

## Excluded

- CAD or curved geometry
- Hybrid, prism/tet, polyhedral, or materially distorted meshes
- Turbulence, transition, transients, multiphase flow, compressibility, heat
  transfer, moving boundaries, or non-Newtonian rheology
- Materially different boundary conditions or solvers
- Performance claims from emulated or concurrently contended execution

## Physical definition

For channel height `H`, length `L`, viscosity `nu`, and total kinematic
pressure drop `Delta p`:

```text
dp/dx = -Delta p/L
U_x = Delta p/(2 nu L) y(H-y)
Re_H = |U_mean| H/nu
```

The physical matrix varies the target Reynolds number by changing the pressure
gradient while keeping viscosity fixed. Reversing the gradient reverses the
analytic velocity and force signs without changing the expected error limits.
