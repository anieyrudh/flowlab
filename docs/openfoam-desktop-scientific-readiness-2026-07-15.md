# OpenFOAM desktop execution and scientific-readiness checkpoint

Date: 2026-07-15

## Outcome

FlowLab's desktop job executor and straight-pipe scientific runner now use the
same default OpenFOAM image:

`flowlab/openfoam11-gmsh:2026-07-13`

Set `FLOWLAB_OPENFOAM_IMAGE` to exercise a replacement image through both
paths. Scientific results from different image digests must remain separate
campaigns.

## Desktop advanced-solver execution

The retained smoke report is:

`benchmarks/cases/openfoam-smoke/runs/2026-07-15-shared-image-v1/smoke-report.json`

The run completed through Docker with exit code 0. `checkMesh` passed with zero
failed checks, the strict completion gate passed, and five solver-produced VTK
results were collected. The report records zero inlet/outlet flow imbalance.

This proves that the local desktop execution path can generate, queue, run,
collect, and display an OpenFOAM result. It does not prove production mesh
readiness: the smoke report correctly retains the one-layer starter-mesh,
missing production prism/y-plus, partial diagnostic parsing, and CAD-quality
topology limitations.

## Reproduced periodic scientific baseline

The independent retained reproduction is:

`benchmarks/cases/straight-pipe/runs/2026-07-15-periodic-ogrid-reproduction-v1`

All serial gates passed on the 7,168, 20,480, and 65,536-cell levels:

- fine-grid analytical pressure-drop error: 0.2504238536% (limit 5%)
- fine-grid GCI: 0.2832664480% (limit 1%)
- observed order: 1.914615904
- flow-target relative error: 0
- mass relative imbalance: 0
- artifact-index recomputation: all retained hashes valid

The result remains `scientificStatus: analysis-only` and `validated: false`.
It verifies this laminar, fully developed, periodic-pressure-gradient case. It
does not validate the general open inlet/outlet formulation, CAD-exact
geometry, or performance on this ARM64 host because the amd64 image is
emulated.

## Forced-MMS outlet diagnosis and one-change coarse gate

The latest retained forced-MMS stage remains rejected at
`benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v10`. Its mesh,
mass, traction, and linear-residual checks pass, but velocity convergence is
non-monotonic and pressure order/GCI fail. The pressure pre-solve audit
localized the investigation to outlet `constrainPressure`/`updateCoeffs` and
the construction of outlet `phiHbyA` against the imposed outlet velocity flux.

That diagnostic has now been executed and retained at
`benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v10/trace-outlet-constrain`.
At the first pressure correction, the outlet fixed both pressure and velocity,
the pressure patch was not eligible for `fixedFluxPressure` handling, and
`constrainPressure` changed no patch coefficient. The predictor outlet flux was
`0.9985001759793257`, while the imposed velocity flux was
`0.9999999999999978`. This identifies the simultaneous fixed pressure and
fixed velocity as the incompatible contribution.

Exactly one boundary implementation change was then exercised on the coarse
grid: outlet `U` changed from `fixedValue` to
`pressureInletOutletVelocity`; outlet pressure, source, mesh, schemes,
tolerances, iteration count, and exact initialization remained fixed. The
retained run is:

`benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v11-outlet-velocity-coarse`

The change reduced coarse velocity L2 error from `1.2580722861e-3` to
`1.5748365513e-5` and pressure L2 error from `8.5390306564e-2` to
`2.6993419770e-2`. Mesh, mass, final linear residual, and both field-error
comparison checks passed. The advancement decision nevertheless remains
fail-closed because boundary-traction relative imbalance was
`1.3285161495e-6`, above the immutable `1e-6` limit. This is a computed viscous
force contribution in the retained full-precision force output, not output
rounding.

Therefore the three-level forced-MMS validation was not launched. The proposed
history experiment was subsequently executed at:

`benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v12-traction-history-coarse`

All solve-defining files are byte-identical to the v11 coarse control, and the
two `controlDict` files are identical after normalizing only the force
function-object write interval. The audit retained 101 force samples (initial
state plus iterations 1--100) and 100 complete equation-residual samples.

The predeclared final-window rule classifies the result as
`plateaued-or-oscillatory`, not incomplete convergence. From iterations 81 to
100, the maximum equation-initial residual fell 55.82%, from
`7.3805861117e-2` to `3.2607213229e-2`. Over the same window, traction
imbalance fell only 1.24%, from `1.3451739880e-6` to `1.3285161495e-6`, and
decreased on only 52.63% of steps because it alternated between even and odd
iterations. The fitted projection to iteration 3258 is retained as exploratory
only and is not authorization to change the frozen iteration count.

The pressure-outlet/velocity-outlet change is therefore scientifically blocked
under the current unrelaxed SIMPLE formulation. Any next run must be a new,
coarse-only, one-change stabilization experiment aimed at the observed
two-cycle; it must not extend the iteration count, loosen the traction limit,
or launch the three-grid campaign.

That authorized one-change stabilization experiment is retained at:

`benchmarks/cases/open-boundary/runs/2026-07-15-forced-mms-v13-u-relaxation-09-coarse`

It changed only the `U` equation relaxation from `1.0` to `0.9`, using the
value already established by the reproduced FlowLab straight-pipe runner. All
other solve files are byte-identical to v12, and `fvSolution` is identical
after normalizing only that relaxation value. The change removed the late
even/odd traction alternation but did not improve the gate: traction converged
monotonically upward from `1.3333650963e-6` at iteration 81 to
`1.3334910509e-6` at iteration 100. This is 0.3745% worse than the unrelaxed
v12 result and still above the immutable `1e-6` limit. Velocity and pressure
L2 errors improved modestly by 3.35% and 2.25%, respectively.

The stabilized case also retained the first-iteration `Ux` linear-solve
failure: it reached 1,000 linear iterations with final residual
`1.1667416977`, then recovered on later outer iterations. That first-step
failure is now the next defensible diagnostic target. Before changing another
solver parameter, audit the first `Ux` matrix and normalization under the
`pressureInletOutletVelocity` outlet implementation. The three-grid campaign
remains blocked.

## Full-factorial diagnostic matrix

Rather than continue serial one-factor experiments, a bounded `2^4`
coarse-grid matrix was executed at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v14-factorial-matrix`

The factors were outlet `U` (`pressureInletOutletVelocity` or
`zeroGradient`), consistent SIMPLE (`yes` or `no`), the `U` linear solver
(`smoothSolver/symGaussSeidel` or `PBiCGStab/DILU`), and `U` equation
relaxation (`1.0` or `0.9`). Two matching retained cells were reused and 14
new cells were run serially in the same pinned image. All 16 cells retained
the same mesh, 100 iterations, exact initialization, source, pressure
boundary, schemes, tolerances, and scientific limits.

No cell passed the coarse gate. Every cell passed its other hard checks and
failed only boundary traction. The best value remained
`1.3285161495e-6`, above the immutable `1e-6` limit, so no independent
confirmation or three-grid forced-MMS run was launched.

The balanced main-effect and two-factor interaction analysis substantially
narrows the fault:

- `pressureInletOutletVelocity` and `zeroGradient` produced exactly identical
  fields and traction in this pure-outflow case. Retain
  `pressureInletOutletVelocity` for product backflow behavior.
- `PBiCGStab/DILU` fixed the first `Ux` solve from 1,000 iterations and final
  residual above 1 to two iterations and residual near `2e-15`, but its
  traction effect was numerical zero. The first-step linear failure is
  therefore not the traction cause.
- Turning consistent SIMPLE off slightly worsened traction and pressure error.
  Retain consistent SIMPLE.
- Relaxation `0.9` modestly improved velocity and pressure errors but worsened
  traction on average. Retain relaxation `1.0` for this gate.

The screened solver and outlet controls cannot clear the traction floor. The
next audit should decompose the final integrated viscous traction and
outlet-normal velocity gradient by patch face and adjacent cell, then compare
the force function-object result with a direct field integration and the
analytic zero-viscous-traction state. This replaces the earlier hypothesis
that the first `Ux` linear failure was causal.

## Face-by-face viscous-traction audit

That read-only audit has now been executed against the retained v12 time-100
field, which is field-identical to the best v14 matrix cells. No solver step or
case input was rerun or changed. The retained evidence is:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v15-face-traction-audit`

The custom OpenFOAM 11 utility reproduced the force function object's laminar
viscous expression, `Sf & [-nu dev(twoSymm(fvc::grad(U)))]`, for every inlet
and outlet face. Its direct integrated viscous force was
`(-1.328516151801752e-9, 3.220763732510558e-18,
1.664976858454951e-17)`. The function-object value was
`(-1.328516151801862e-9, 3.220763732510381e-18,
1.664976858454983e-17)`. The viscous x-force difference was
`1.1022181662e-22`; the maximum difference including the independently summed
pressure force was `2.3852447795e-18`, within the declared `5e-18` reduction
tolerance. The function object is therefore not misreporting the field.

The face decomposition reverses the outlet-causality hypothesis:

- all 144 outlet faces have exactly zero `snGrad(U)` and zero outlet-normal
  velocity gradient;
- outlet viscous x-force is `-7.4014861844e-23`, only
  `5.5712429046e-14` of the net x-force, and its signed face contributions
  cancel to numerical noise;
- inlet viscous x-force is `-1.328516151801678e-9`, or
  `0.9999999999999443` of the net; every inlet face contributes with the same
  sign;
- the inlet fixed boundary velocity is analytically exact, but the adjacent
  owner-cell `Ux` differs by as much as `4.1516269044e-5`, producing a mean
  inlet-normal velocity gradient of `-9.9638711385e-4`;
- `(4/3) nu A mean(snGradNormalVelocity)` predicts
  `-1.328516151801863e-9`, within `1.8487486691e-22` of the directly integrated
  inlet value.

The analytic manufactured state has constant `U=(1,0,0)`, zero velocity
gradient, and zero viscous traction on every face. The retained numerical field
does not reproduce that state at the inlet-adjacent cells, so its viscous-force
departure remains `1.3285161518e-6` relative to the source force and the
immutable `1e-6` gate still fails.

The next defensible coarse-only, one-change experiment is therefore at the
inlet pressure boundary, not the outlet velocity or linear solver: retain the
exact fixed inlet velocity and fixed outlet pressure, but replace simultaneous
fixed inlet pressure with a flux-consistent pressure treatment such as
`fixedFluxPressure`, initialized at the exact pressure. It must first prove
that the inlet owner-cell gradient and viscous traction fall without degrading
mass, field errors, or residual gates. The three-grid campaign remains blocked
until that coarse result passes; the traction threshold is unchanged.

## Fixed-flux inlet-pressure coarse gate

That one-change experiment was executed at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v16-inlet-fixed-flux-pressure-coarse`

The retained equivalence audit proves that all other solve-defining files are
byte-identical to v12. The `0/p` files are identical after normalizing only the
inlet patch type: `fixedValue` became `fixedFluxPressure`, and both retain the
analytic initial value `0.001`. Exact fixed inlet velocity, fixed outlet
pressure, `pressureInletOutletVelocity` at the outlet, mesh, source, schemes,
solver controls, force sampling, exact initialization, 100 iterations, and all
limits were unchanged.

The coarse gate blocked. `checkMesh`, mass imbalance
(`9.2703622556e-14`), final linear residual (`9.8192713990e-11`), and the
strict velocity-error comparison passed. Velocity L2 error improved by only
`0.0025986%`, from `1.5748365513e-5` to `1.5747956268e-5`. Two checks failed:

- pressure L2 error worsened `1.15918%`, from `0.02699341977` to
  `0.02730632149`;
- traction imbalance worsened by a factor of `9.01963`, from
  `1.3285161495e-6` to `1.1982724130e-5`, versus the unchanged `1e-6` limit.

The failure is now pressure-dominated. At iteration 100 the force object
reported pressure force `-0.0010000106491046422`, a departure of
`1.0649104642e-5` relative to the analytic source. The viscous contribution
was `-1.3336194881e-9`, itself `1.3336194881e-6` relative to the source and
still above the gate. The last-20 traction value increased by `0.24918%` while
the maximum equation-initial residual fell `85.46%`; this is classified
`plateaued-or-oscillatory`, not incomplete convergence.

`fixedFluxPressure` is therefore rejected for this manufactured boundary while
the exact inlet velocity is retained. It trades the simultaneous Dirichlet
condition for a computed inlet pressure that no longer reproduces the analytic
pressure force, without removing the viscous-traction floor. No confirmation
or three-grid execution was launched.

If another boundary-only coarse experiment is authorized, the next analytic
candidate is an inlet `fixedGradient` pressure condition using the manufactured
outward-normal gradient `+0.001`, with fixed inlet velocity and the outlet
pressure datum retained. That is a new hypothesis, not a validated fix; it
must use the same fail-closed gate and must not weaken the traction limit.

## Analytic fixed-gradient inlet-pressure coarse gate

That final boundary-only candidate was executed at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v17-inlet-fixed-gradient-pressure-coarse`

The retained equivalence audit proves that all other solve-defining files are
byte-identical to v12. In `0/p`, only the inlet condition changed: the exact
`fixedValue 0.001` became `fixedGradient` with the manufactured
outward-normal gradient `+0.001`. Exact fixed inlet velocity, fixed outlet
pressure, `pressureInletOutletVelocity` at the outlet, mesh, source, schemes,
solver controls, force sampling, exact initialization, 100 iterations, and all
scientific limits were unchanged.

The coarse gate blocked. `checkMesh`, mass imbalance
(`7.4461354583e-13`), and final linear residual (`9.3404700155e-11`) passed.
Three checks failed:

- traction imbalance was `0.16666934381`, or about `125,455` times the
  retained v12 value and `166,669` times the immutable `1e-6` limit;
- velocity L2 error worsened by a factor of `10.2175`, from
  `1.5748365513e-5` to `1.6090942613e-4`;
- pressure L2 error worsened by a factor of `3.17671`, from
  `0.02699341977` to `0.08575036653`.

The failure is overwhelmingly pressure-dominated. At iteration 100 the force
object reported pressure force `-0.0011666693436128076`, a relative departure
of `0.1666693436` from the analytic `-0.001` source force. Viscous force was
only `-1.9340516707e-13`, a relative contribution of `1.9340516707e-10`.
Across the final 20 iterations traction fell by only `2.5916e-5` relative and
the residual history ended with an oscillatory spike, so the result is
classified `plateaued-or-oscillatory`; extending the iteration count is not
authorized.

No independent confirmation or three-grid execution was launched. The
`fixedValue`, `fixedFluxPressure`, and analytic `fixedGradient` inlet-pressure
forms have now all failed the frozen coarse formulation for distinct reasons.
Boundary-condition tuning therefore stops here. The next defensible scientific
work is a re-derivation of the manufactured continuum and discrete pressure
equations—including source sign, pressure datum/reference handling, and the
boundary operator actually assembled by OpenFOAM—followed by a new MMS design
that is proven discretely self-consistent before another coarse solve.

## Non-degenerate affine MMS redesign and exact-state gate

The redesign preflight is retained at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v18-affine-redesign-preflight`

The v10 exact-UEqn evidence already proves that the original source sign is
correct and its pressure reference cell is inactive. The design defect is
instead the original near-perfect SIMPLE split: constant velocity makes its
convection and viscous operators zero, leaving the pressure gradient and body
source to cancel exactly. The replacement field is affine and divergence-free:

`U=(1 + 0.1*y, 0.1, 0)`, `p=0.001*(1-x)`.

Its convective term is `(0.01,0,0)`, pressure-gradient term is
`(-0.001,0,0)`, and required source is `(0.009,0,0)`. The source magnitude is
`0.81818` of the sum of those two component magnitudes, clearing the declared
`0.25` non-degeneracy limit. The full integral QoI now includes convective,
viscous, and pressure surface fluxes; the legacy comparison between only
open-x traction and body source is explicitly rejected for this multi-open
field.

All six offline checks passed. Gauss-linear face reconstruction produced
exactly zero discrete divergence on the 12^3, 24^3, and 48^3 meshes. The
largest normalized cellwise momentum-identity residual was `6.3218e-14`,
below the `1e-12` tolerance. Fixed outlet pressure is the sole datum and no
reference cell is required. This authorized only a one-iteration OpenFOAM
exact-state probe, not coarse validation.

That pinned probe is retained at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v19-affine-one-iteration-probe`

The real OpenFOAM assembly blocked the redesign. Mesh, solver completion, and
four-patch mass balance (`9.9546e-14`) passed, but velocity relative L2 error
became `1.26233e-4`, pressure relative L2 error became `1.55911`, and the
largest final linear residual was `2.03423e-3` after one SIMPLE iteration.

The source-direction split is decisive: the live `Ux` predictor initial
residual was `3.6687e-15`, so the affine convection and source sign close at
round-off before pressure correction. The pressure equation initial residual
was `6.3227e-3`; after correction its fitted pressure slope was
`-0.00279261`, or `2.79261` times the analytic `-0.001`. The exact affine
state is therefore not a fixed point of this segregated SIMPLE pressure and
boundary assembly.

No 100-iteration coarse or three-grid execution was launched. MMS amplitude
or boundary-value tuning is not authorized. The next implementation target is
an affine first-step trace of `phiHbyA`, pressure-correction flux, and pEqn
boundary coefficients, or a coupling formulation that demonstrates
exact-state preservation before entering the scientific campaign.

## Affine flux-pressure one-step compatibility gate

The flux-consistent follow-up is retained at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v20-affine-flux-pressure-one-step`

The analytic velocity conditions and fixed outlet pressure datum remained
unchanged. The inlet, `yMin`, and `yMax` pressure patches became
`fixedFluxPressure`, initialized with exact face pressure values from
OpenFOAM's generated face centres and with the analytic initial normal
gradients. The diagnostic module retained the live `phiHbyA`, pressure
correction, pressure-normal gradient, pressure matrix, and corrected flux.
The audit reconstructed all 5,616 internal and boundary faces directly from
`polyMesh`; it did not inject analytic flux into the solve.

The flux-consistent pressure assembly passed several previously failing
checks. The live initialized-pressure matrix residual was `6.0072e-18`, the
internal pressure relative L2 error was `3.7026e-17`, mass imbalance was
`2.6549e-16`, and `phi = phiHbyA - pCorrectionFlux` closed to
`1.7347e-18`. Across every face, the correction required to recover the exact
velocity flux matched the actual pressure correction to `1.4658e-16`.

The complete one-step gate nevertheless blocked on four checks:

- `fixedFluxPressure` changed the inlet normal pressure gradient from the
  analytic `+0.001` to a mean of about `-0.000852553`; the maximum face error
  was `0.001999739`;
- the resulting inlet boundary pressure departed from the analytic value by
  as much as `8.33225e-5`, even though the internal pressure remained exact;
- velocity relative L2 error was `1.85289e-5`, above the `1e-8` gate;
- the unchanged `Uy` predictor hit 1,000 iterations and ended at residual
  `0.002034228`, above the `1e-8` gate. `Ux` remained exact to round-off.

This separates two compatibility notions that cannot be conflated. The
computed pressure correction is exactly compatible with the imposed velocity
flux, but that required correction is not compatible with the manufactured
inlet pressure trace. The segregated affine state therefore remains unsuitable
for scientific promotion. No 100-iteration coarse or three-grid execution was
launched.

The next defensible implementation target is no longer another boundary
condition or solver-parameter sweep. It is a project-local coupled validation
formulation—or a revised affine manufactured field—whose momentum predictor,
pressure trace, and face flux are proven to share the same exact discrete
fixed point before any coarse campaign.

Promotion order remains fail-closed:

1. Forced MMS: three refinements at ratio 2; `checkMesh` passes; mass and
   traction imbalance at most 1e-6; final linear residual at most 1e-8;
   velocity and pressure order at least 1.5; both GCIs at most 1%.
2. Open pipe: the same common gates, static pressure-drop error at most 1%,
   wall-force imbalance at most 1e-6, and no QoI plateau.
3. Frozen surface: only after both stages pass, run immutable 64/96/128
   exact-init and V&V/GCI checks.
4. Performance: only after scientific gates pass, and only on native amd64
   hardware for a defensible timing claim.

## Resolved pressure/tangential-velocity inlet and coarse gate

The comprehensive resolution research and solution matrix are recorded in:

`docs/openfoam-affine-mms-resolution-research-2026-07-16.md`

The OpenFOAM source and boundary-condition literature confirmed that the v20
inlet contract was over-specified for the segregated pressure correction. The
resolved affine verification contract fixes the analytic pressure at both
x-normal ends, specifies only the analytic tangential inlet velocity, and lets
`pressureInletOutletVelocity` derive the normal component with zero normal
gradient. The solver must still recover the analytic inlet-normal velocity;
that value is now an audited result rather than a second imposed condition.

The first resolved one-step run passed every gate, but its 100-iteration
follow-up exposed algebraic drift: a `1e-10` linear-solver tolerance cannot
reliably preserve a `1e-12` face gate over repeated solves. The final contract
therefore uses `PBiCGStab/DILU` for the asymmetric velocity equation and a
`1e-14` absolute tolerance for both velocity and pressure. No scientific limit
was relaxed.

The provenance-correct final one-step result is retained at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v27-affine-pressure-tangential-final-one-step`

All 13 checks passed. Across all 5,616 faces, the largest pressure-correction
mismatch was `1.46508e-16`, normal-pressure-gradient error was `1.10827e-14`,
and boundary-pressure error was `5.41342e-16`. Velocity and pressure relative
L2 errors were `3.91759e-16` and `1.73869e-13`, respectively.

That result authorized and was followed only by the 100-iteration `12^3`
coarse gate retained at:

`benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v28-affine-pressure-tangential-final-coarse`

All 13 coarse checks passed. At iteration 100, the largest face correction,
normal-gradient, and boundary-pressure errors were `1.77366e-16`,
`1.61156e-14`, and `1.13906e-15`. Velocity and pressure relative L2 errors
were `7.51024e-15` and `3.14010e-13`; mass relative imbalance was
`1.81015e-16`; the largest final linear residual was `9.58184e-15`.

The coarse report authorizes three-grid execution, but no three-grid run was
launched here. The affine case is an exact discrete fixed-point regression and
cannot by itself produce a meaningful truncation-error order. Scientific
promotion still requires a non-affine boundary-compatible MMS with observed
order/GCI, followed by an independent physical benchmark with force-function,
direct-face-integration, analytic pressure-force, and analytic viscous-traction
agreement.
