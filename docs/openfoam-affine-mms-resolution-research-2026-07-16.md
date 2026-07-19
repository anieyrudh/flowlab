# Resolving the affine OpenFOAM pressure/velocity gate

Date: 2026-07-16
Scope: FlowLab desktop application, pinned OpenFOAM Foundation 11 backend
Status: boundary/coupling blocker resolved at one-step and 100-iteration coarse gates; formal scientific validation remains pending

## Outcome

The failure was not a force-function-object defect and was not curable by
another blind SIMPLE parameter sweep. The original inlet contract attempted to
retain an independently prescribed normal velocity and analytic pressure trace
through a segregated pressure-correction step. `fixedFluxPressure` correctly
chose flux consistency, so it changed the pressure gradient and lost the
analytic pressure trace.

The resolved verification contract is:

- inlet `p`: exact `fixedValue G`;
- inlet `U`: `pressureInletOutletVelocity`, with exact tangential velocity and
  a zero-gradient normal component;
- outlet `p`: exact `fixedValue 0`;
- outlet `U`: `pressureInletOutletVelocity`;
- `yMin`/`yMax` pressure: `fixedFluxPressure`;
- `U`: `PBiCGStab/DILU`;
- both `U` and `p` linear-solver absolute tolerance: `1e-14`;
- all scientific limits unchanged.

This does not prescribe the inlet-normal velocity. It verifies that the solver
recovers and retains the exact inlet-normal velocity. That distinction is the
necessary correction to the over-specified boundary contract.

The final one-step report is `authorized`. The final 100-iteration coarse
report is also `authorized`, with all 13 checks passing. The three-grid stage is
authorized but deliberately not executed in this work.

## Root-cause chain

OpenFOAM Foundation 11 constructs `rAU`, `HbyA`, and `phiHbyA`, calls
`constrainPressure`, solves the pressure equation, then corrects both flux and
velocity in the segregated `correctPressure` path. This sequence is explicit
in the [Foundation 11 `correctPressure.C` source](https://cpp.openfoam.org/v11/incompressibleFluid_2correctPressure_8C_source.html).

The Foundation user guide describes `fixedFluxPressure` as a pressure-gradient
condition that adjusts the gradient so the boundary flux is consistent with
the velocity boundary and body forces. It also identifies a specified-pressure
plus `pressureInletOutletVelocity` pair as the intended pressure-boundary
contract when the normal velocity is not independently known
([OpenFOAM 11 derived boundary conditions](https://doc.cfd.direct/openfoam/user-guide-v11/derived-boundary-conditions)).

The repository evidence matches that design exactly:

1. v19's fixed-gradient inlet retained the momentum predictor but did not
   satisfy the live pressure equation.
2. v20's `fixedFluxPressure` inlet satisfied the live pressure equation and
   exact velocity-flux correction, but changed the inlet normal pressure
   gradient and pressure trace.
3. v21's pressure/tangential-velocity inlet passed every one-step face and
   field gate, proving that the remaining issue was the boundary contract.
4. v24 retained the resolved contract for 100 iterations but failed two
   `1e-12` face gates because the linear systems were solved only to `1e-10`.
5. v27/v28 changed only the algebraic tolerance to `1e-14`; all unchanged gates
   then passed.

The projection-method literature reaches the same structural conclusion:
pressure-correction schemes require pressure boundary data consistent with the
normal momentum/velocity treatment, and the intermediate velocity does not in
general satisfy the final velocity boundary. See
[Matsui's pressure-Poisson analysis](https://doi.org/10.1007/s00211-022-01323-x),
[Vreman's pressure boundary analysis](https://doi.org/10.1016/j.jcp.2014.01.035),
and [Fehn, Wall, and Kronbichler's projection stability analysis](https://arxiv.org/abs/1706.09252).

## Research lanes

Five independent evidence lanes were used. No source was shared between lanes,
so the source-overlap rate was 0%, below the 25% research limit.

### 1. Pinned solver implementation

Primary sources:

- [OpenFOAM Foundation 11 `correctPressure.C`](https://cpp.openfoam.org/v11/incompressibleFluid_2correctPressure_8C_source.html)
- [OpenFOAM Foundation 11 `constrainPressure`](https://cpp.openfoam.org/v11/constrainPressure_8H.html)
- [OpenFOAM Foundation 11 `fvSolution`](https://doc.cfd.direct/openfoam/user-guide-v11/fvsolution)

Finding: the pinned backend is segregated. The convective momentum matrix is
asymmetric, for which the official guide supports PBiCGStab and DILU. A
single-equation solver change can improve algebraic accuracy but cannot repair
an incompatible pressure/velocity boundary contract.

### 2. Pressure/velocity boundary theory

Primary sources:

- [Matsui, pressure-Poisson formulation with total-pressure boundary](https://doi.org/10.1007/s00211-022-01323-x)
- [Vreman, pressure near incompressible boundaries](https://doi.org/10.1016/j.jcp.2014.01.035)
- [Pekker, pressure and tangential-velocity boundary conditions](https://arxiv.org/abs/2405.11691)

Finding: pressure plus tangential velocity is a defensible boundary pairing;
pressure plus all velocity components is not a general pressure-correction
contract. Normal pressure information must be consistent with normal momentum.

### 3. MMS boundary verification

Primary sources:

- [Eca and Hoekstra, incompressible CFD verification with MMS](https://www.marin.nl/en/publications/cfd-code-verification-with-the-method-of-manufactured-solutions)
- [Bond et al., manufactured solutions for CFD boundary-condition verification](https://doi.org/10.2514/1.28099)
- [Design and verification methodology for finite-volume boundary conditions](https://doi.org/10.1016/j.compfluid.2014.03.028)

Finding: the manufactured solution must be tailored to the actual boundary
equations. An exact discrete fixed point is a strong algebra/BC preflight, but
formal order verification needs a nontrivial solution whose truncation error is
measurable across grids.

### 4. Coupled alternatives

Primary project/research sources:

- [foam-extend pressure/velocity block-coupled solver distribution](https://sourceforge.net/projects/openfoam-extend/files/foam-extend-3.1/)
- [PETSc `petsc4foam`](https://gitlab.com/petsc/petsc4foam)
- [Monolithic incompressible-flow formulation](https://arxiv.org/abs/2108.12073)

Finding: a block-coupled `U-p` solver is technically viable but is not present
in the pinned Foundation 11 backend. `petsc4foam` converts individual OpenFOAM
matrices; it does not by itself make the segregated `U-p` system monolithic.
Importing foam-extend or another OpenFOAM fork would change solver provenance
and carries materially higher delivery and validation risk.

### 5. Validation architecture

Primary sources:

- [Sandia boundary-condition manufactured solution](https://www.sandia.gov/research/publications/details/a-manufactured-solution-for-verifying-cfd-boundary-conditions-part-ii-2005-01-01/)
- [NASA verification assessment and grid-convergence guidance](https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html)
- [SU2 finite-volume MMS verification](https://su2code.github.io/vandv/MMS_FVM_Navier_Stokes/)

Finding: solver/operator MMS, boundary-specific MMS, and physical validation
should be separate evidence tracks. Grid convergence and observed order must be
measured before a physical benchmark can support a validation claim.

## Solution matrix

| Option | What it resolves | Time/risk | Decision |
| --- | --- | --- | --- |
| Exact pressure + tangential inlet velocity | Removes the incompatible normal-velocity/pressure pairing while retaining the full analytic pressure drop | Low; pinned Foundation 11 | Selected and passed |
| Align algebraic tolerance to the face gate | Prevents `1e-10` solve noise from accumulating past a `1e-12` face gate | Low; no scientific gate change | Selected and passed at `1e-14` |
| Retain fixed inlet velocity and accept natural inlet pressure | Preserves a flow-rate inlet, but cannot independently verify the old analytic inlet pressure trace | Low | Use as a separate product-BC benchmark, not this pressure-drop MMS |
| Discrete MMS source/boundary forcing | Can manufacture an exact fixed point for the exact segregated algebra | Medium | Useful regression track; does not replace continuum order verification |
| Periodic interior MMS plus boundary-specific MMS | Separates interior discretization from boundary implementation | Medium | Recommended next verification architecture |
| Project-local monolithic `U-p` solver | Removes segregated pressure/velocity splitting | High | Long-term only after the current validation track |
| foam-extend/OpenFOAM fork migration | Provides existing block-coupled implementations | High provenance/migration risk | Rejected for ASAP desktop delivery |
| PIMPLE/transient projection | Adds temporal splitting; uses related pressure-correction machinery | Medium and indirect | Not a root-cause fix |
| Relax the `1e-12` face gates | Hides algebraic drift | Low but scientifically invalid | Rejected |

## Executed evidence

### Final one-step gate: v27

Artifact:
`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v27-affine-pressure-tangential-final-one-step/artifacts/affine-pressure-tangential-one-step.json`

- status: `authorized`
- checks: 13/13 passed
- audited faces: 5,616
- velocity relative L2 error: `3.917588761986068e-16`
- pressure relative L2 error: `1.7386855996814716e-13`
- maximum pressure-correction mismatch: `1.465076889965505e-16`
- maximum normal-pressure-gradient error: `1.1082714607146826e-14`
- maximum boundary-pressure error: `5.413421447220124e-16`
- mass relative imbalance: `2.654881145842765e-16`
- maximum final linear residual: `7.115284518912037e-15`
- report SHA-256: `ec036374460f20642d590c52c304920c349cb91913e9f1db6ab1fffe71f446ae`

### Final coarse gate: v28

Artifact:
`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v28-affine-pressure-tangential-final-coarse/artifacts/affine-pressure-tangential-coarse.json`

- mesh/iterations: `12^3`, 100
- status: `authorized`
- checks: 13/13 passed
- audited faces: 5,616 at iteration 100
- velocity relative L2 error: `7.510241157129346e-15`
- pressure relative L2 error: `3.140096941083474e-13`
- maximum pressure-correction mismatch: `1.7736615805620873e-16`
- maximum normal-pressure-gradient error: `1.6115581091824538e-14`
- maximum boundary-pressure error: `1.139062802413271e-15`
- mass relative imbalance: `1.8101462358018932e-16`
- maximum final linear residual: `9.581840709876875e-15`
- report SHA-256: `c8082f4fc585653319f42da060d884ee8dc6d1f6cad5f60397d671c3745b27f0`
- next stage: three-grid execution `authorized`, not executed

Both runs used image
`flowlab/openfoam11-gmsh:2026-07-13` at digest
`sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
and instrumented library SHA-256
`157ac7086c82dcf5bc7fd1fde1a3ff83e8cc3710ae9b83d9d085711a97f076de`.

## What is resolved and what is not

Resolved:

- the OpenFOAM pressure/velocity boundary incompatibility;
- exact one-step preservation;
- 100-iteration coarse preservation;
- face-by-face pressure correction, pressure trace, and pressure-gradient gates;
- algebraic accuracy relative to the existing face tolerance;
- fail-closed staged execution.

Not yet resolved:

- formal spatial order for a non-affine manufactured solution;
- grid-convergence uncertainty for physical quantities of interest;
- physical validation against an independent experiment or analytic benchmark;
- production promotion in the desktop application.

## Required next sequence

1. Run 12/24/48 exact-state invariance with the v28 contract. Call this a
   discrete fixed-point/grid-invariance check, not an observed-order result.
2. Add a non-affine divergence-free MMS tailored to the same pressure plus
   tangential-velocity boundary equations. Predeclare L1/L2/Linf error norms,
   expected observed order, and monotonic convergence gates.
3. Run the non-affine MMS on at least three systematically refined grids and
   compute observed order and GCI/uncertainty.
4. Run an independent laminar physical benchmark. Compare OpenFOAM's force
   function object with direct face integration, analytic pressure force,
   analytic viscous traction, mass flow, and pressure drop.
5. Only after steps 1-4 pass, mark the solver backend scientifically validated
   for that bounded regime and expose the validated regime in the desktop UI.

The affine case is intentionally not promoted as physical validation. Because
its fields and source are exactly representable on the Cartesian mesh, its
near-roundoff error is excellent regression evidence but cannot establish a
meaningful truncation-error order by itself.
