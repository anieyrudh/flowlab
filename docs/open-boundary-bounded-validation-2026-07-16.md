# FlowLab bounded open-boundary validation — 2026-07-16

## Outcome

FlowLab now exposes a second desktop-only validated benchmark entry:

`laminar-open-boundary-all-hex-v1`

The entry is fail-closed and is loaded only when the affine regression, formal
non-affine MMS, and independent physical force benchmark reports are present,
accepted, and pass every recorded check. This is a bounded scientific regime,
not a general production-CFD claim.

## Frozen execution contract

- OpenFOAM image: `flowlab/openfoam11-gmsh:2026-07-13`
- Image digest:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Solver: OpenFOAM 11 `incompressibleFluid`
- Mesh family: structured Cartesian all-hex, 12/24/48 cells per axis
- Inlet/outlet pressure: exact `fixedValue` traces
- Inlet/outlet velocity: `pressureInletOutletVelocity`, exact tangential
  velocity, solver-compatible zero-gradient normal component
- Linear algebra: PBiCGStab/DILU for velocity; absolute tolerance declared by
  each campaign
- Performance claims: excluded because the AMD64 image ran under emulation

## Gate 1 — affine grid invariance

Accepted report:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v29-affine-grid-invariance/artifacts/affine-grid-invariance.json`

SHA-256:
`f1e9362669076e652f5118355981e7d78a6c261439baa4d5fd64e26f825ab920`

All 12/24/48 levels passed every inherited field, face-compatibility, pressure
equation, mass, and linear-residual gate. The affine result is correctly
reported as exact-state grid invariance, not as observed order: the affine
fields are exactly representable and the remaining errors are round-off
dominated.

The 100-iteration 12³ stability evidence remains pinned in v28; v29 adds one
exact-state step on all three grids.

## Gate 2 — non-affine MMS, observed order, and GCI

Exact fields:

```text
U = (U0 + A sin(pi y) sin(pi z), 0, 0)
p = G(1-x) + P sin(pi x) sin(pi y) sin(pi z)
```

The coded momentum source closes the steady strong form. The pressure
perturbation vanishes at inlet and outlet, the velocity has zero x-normal
gradient there, and the side-patch fixed-flux pressure gradients are initialized
from the analytic outward-normal gradient.

Accepted report:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v36-non-affine-mms/artifacts/non-affine-mms-report.json`

SHA-256:
`bd3749f956399a65b1a56d32d4e0fc68e3c71d98db903b24edda810e1cbf6e45`

| Quantity | 12³ error | 24³ error | 48³ error | Orders | Fine GCI |
|---|---:|---:|---:|---:|---:|
| Velocity relative L2 | 5.6341e-4 | 1.3741e-4 | 3.3602e-5 | 2.0357 / 2.0319 | 4.2003e-5 |
| Pressure relative L2 | 1.2686e-2 | 4.0824e-3 | 1.4411e-3 | 1.6357 / 1.5023 | 1.8013e-3 |

All mesh, solver-exit, mass, linear-residual, nonlinear-residual, monotonicity,
minimum-order, order-consistency, and fine-GCI gates passed.

Rejected diagnostic evidence was retained:

- v30: coded-model compilation rejected before solving because of an
  unnecessary include.
- v31: source-sign diagnostic rejected; it isolated OpenFOAM's coded
  `fvMatrix::source()` sign convention.
- v35: all spatial convergence gates passed, but the 24³ nonlinear residual
  was `1.56e-8`, above the predeclared `1e-8` limit. v36 used one uniform
  1000-iteration budget and passed all grids.

## Gate 3 — independent physical force benchmark

The independent case is plane Poiseuille flow:

```text
U = (G/(2 nu) y(H-y), 0, 0)
p = G(1-x/L)
G = 0.02 m2/s2
nu = 0.01 m2/s
Re_H = 16.67
```

It contains no manufactured body source. OpenFOAM force function objects are
compared with a separately compiled, read-only boundary-face integration
utility. Each CSV face is also compared with analytic pressure and viscous
traction. The integrated analytic targets are:

- inlet/outlet pressure force: `(-0.02, 0, 0)`
- wall viscous force: `(0.02, 0, 0)`
- integrated inlet/outlet viscous force: zero

Accepted report:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v40-laminar-force-benchmark/artifacts/laminar-force-benchmark.json`

SHA-256:
`d5d912e8e896b5d60db03b65bd2fc445632bb278c90a25dc0b406428407bebde`

Fine-grid results:

- OpenFOAM force object versus direct face integration: `4.34e-16` maximum
  absolute difference
- analytic pressure-force relative error: `9.89e-15`
- analytic wall-viscous-force relative error: `2.1758e-4` (0.0218%)
- maximum facewise analytic viscous-traction relative error: `2.2257e-4`
  (0.0223%)
- total pressure/viscous momentum imbalance: `2.1758e-4`
- velocity relative L2 error: `3.7210e-4`
- pressure relative L2 error: `3.7787e-6`
- transverse velocity relative L2 error: `1.3402e-7`

All 17 physical checks passed.

Rejected diagnostic evidence was retained:

- v37: initializer requested serialized face centres from symmetry patches;
  OpenFOAM correctly did not provide them, so the run stopped before solving.
- v38: the unrelaxed formulation was rejected after resolution-dependent SIMPLE
  instability.
- v39: the 24³ relaxed diagnostic established force accuracy and showed that
  combined relative residuals for analytically zero transverse equations are
  ill-conditioned. v40 therefore gates axial and pressure residuals plus the
  actual transverse velocity error separately.

## Desktop promotion and remaining boundary

`server/flowlab/validated_benchmark.py` verifies the three accepted report
schemas, statuses, every check, the physical `validated` flag, and explicit
desktop-promotion authorization before returning the bounded regime to
`GET /api/benchmarks/validated`.

The desktop renders it as `Validated bounded regime · bounded regime promoted`.
The pre-existing periodic benchmark remains analysis-only and promotion
blocked. General generated jobs remain experimental and their production-claim
promotion endpoint remains fail-closed.

The new regime does **not** validate CAD or curved geometry, hybrid meshes,
transients, turbulence, multiphase or compressible flow, materially different
boundary conditions, or native performance.
