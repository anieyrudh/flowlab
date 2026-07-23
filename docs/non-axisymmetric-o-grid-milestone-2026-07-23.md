# Non-axisymmetric full O-grid milestone

Status: **next separate geometry milestone; not inherited from axisymmetric evidence**

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
