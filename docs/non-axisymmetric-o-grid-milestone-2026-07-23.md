# Non-axisymmetric full O-grid milestone

Status: **software path complete; v2 three-level numerical-verification
candidate passed; controlled independent review pending**

## Current disposition — 2026-07-24

The bounded full-revolution software path is implemented on
`codex/full-ogrid-product-milestone`. It covers editor validation, deterministic
five-block all-hex generation, OpenFOAM case generation and JobManager
execution, full-volume VTK preview, exterior-surface triangulation, and
solver-space XYZ probing. Unsupported topology and physics fail closed.

The prospective v1 verification contract was frozen before the definitive
scientific run. The retained `2026-07-24-v1-r2` coarse level passed
deterministic generation, every mesh gate, and every pressure, flow,
conservation, and profile gate. It failed the frozen solver group:

- no SIMPLE convergence declaration by the 2,000-iteration limit;
- final linear residual `1.3859648e-07` exceeded `1e-07`; and
- absolute global continuity error `1.3146633e-08` exceeded `1e-08`.

The contract's mandatory-stop rule therefore prohibited medium and fine runs.
Observed order and GCI are unqualified. The immutable partial-failure package is
review-ready, but no controlled independent reviewer has yet been identified.
No fixture, registry, validation state, promotion flag, product claim, or
release state was changed.

The v1 discrepancy was subsequently judged non-catastrophic for this bounded
engineering use, without changing its frozen failure disposition. A
prospective v2 contract now preserves the same physics, meshes, numerical
controls, and 2,000-iteration budget while sampling pressure and flow every
iteration. Its solver decision requires stable pressure drop, stable flow,
conservation, normal termination, and the declared stop condition. Residual
and continuity tails are diagnostic. V2 must be committed cleanly before a new
coarse run; medium and fine remain gated by each preceding level.

The definitive `2026-07-24-v2-r3` campaign subsequently passed coarse, medium,
and fine. Pressure drop converged monotonically with observed order
`1.6125950398033853` and fine-grid GCI `0.1596996556959516%`. Every frozen
candidate gate passed, and the read-only evidence package has tree digest
`11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6`.
Controlled independent review remains unavailable, so numerical verification
has not been promoted to independent validation or a product claim.

The source and desktop verification surfaces passed: 581 backend tests, 81
frontend tests, lint, production build, 16 Playwright desktop E2E tests,
portable macOS package build/QA, and a native packaged interaction that exposed
the exact medium O-grid controls. See
`docs/desktop-full-ogrid-qa-2026-07-24.md` and
`docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_2026-07-24.md`.

The current axisymmetric path is a three-dimensional wedge realization of a
straight, collinear, circular source-to-sink profile. It supports varying
diameter along the pipe axis, including Venturi, contraction, expansion, and
nozzle edges. It does not support bends, branches, non-circular sections, swirl,
or circumferentially varying flow.

The next geometry milestone is a full-revolution, non-axisymmetric all-hex
O-grid. It must be planned, verified, and reviewed independently. Passing the
axisymmetric straight-pipe campaign does not validate or promote the O-grid.

## Bounded first claim

The first O-grid claim should remain narrow:

- one straight circular pipe with a full 360-degree fluid volume;
- steady incompressible laminar flow;
- deterministic core and wall-block topology;
- explicit inlet, outlet, wall, and internal block interfaces;
- solver-produced nonzero-volume mesh and XYZ fields;
- the same Hagen–Poiseuille quantity of interest, but a new prospectively
  frozen refinement and geometry-error contract.

Elbows, branches, arbitrary CAD, turbulence, transients, multiphase flow, and
production meshing remain later claims.

## Delivery stages

1. **Geometry contract**
   - Introduce an explicit full-revolution representation and coordinate frame.
   - Keep physical SI dimensions separate from editor layout.
   - Fail closed on unsupported topology and diameter discontinuities.
   - Record geometry representation, wall faceting, core layout, patch roles,
     and hashes in the case manifest.

2. **O-grid topology**
   - Generate a conformal center block plus circumferential wall blocks.
   - Preserve internal faces as internal interfaces, not boundary patches.
   - Make axial, radial, core, and circumferential resolution explicit.
   - Run topology and geometry checks, including positive volume, closed wall,
     patch coverage, non-orthogonality, skewness, and aspect ratio.

3. **Product execution and visualization**
   - Route case generation through the normal adapter and `JobManager`.
   - Export preview and solved fields from the actual full-volume mesh.
   - Reuse the exterior-surface triangulation and solver-space XYZ probing
     without substituting a planar or wedge proxy.
   - Record mesh and field provenance in result metadata.

4. **Prospective verification**
   - Freeze three genuinely uniform refinement levels before running them.
   - Refine every discretization dimension included in the claim, including
     wall faceting and core resolution.
   - Declare how geometry error is treated; do not let error cancellation stand
     in for convergence.
   - Retain solver logs, raw fields, mesh-quality reports, QoI extraction,
     residuals, conservation, GCI, image identity, and content hashes.

5. **Independent review and product status**
   - Obtain controlled independent review of the exact immutable package.
   - Change an O-grid fixture or registry entry only after every frozen gate and
     review requirement passes.
   - Keep any accepted O-grid entry analysis-only unless a separate product
     promotion contract explicitly authorizes more.

## Exit criteria

The milestone is complete only when:

- the editor-authored full-volume case fails closed outside its declared scope;
- `blockMesh`/native generation and `checkMesh` succeed on all frozen levels;
- the solved VTK/VTU bounds are genuinely three-dimensional;
- XYZ probes identify the expected surface and owning cell;
- the three-level analytic comparison and GCI pass their prospectively frozen
  gates;
- the evidence package is immutable and hash-bound to source and runtime;
- controlled independent review approves the bounded O-grid claim; and
- fixture, registry, documentation, and UI labels agree.

Until then, the O-grid remains experimental software work and must not borrow
the axisymmetric campaign's validation or release status.
