# Canonical curved-elbow V2 R2 qualification assessment

## Disposition

The V2 R2 evidence passes the prospective software, geometry, mesh, solver,
physics, provenance, and three-grid gates for one bounded true-3D OpenFOAM
case:

- steady, incompressible, laminar flow;
- one constant-diameter circular 90-degree elbow;
- centreline radius Rc/D=3;
- 10D straight inlet and outlet legs;
- Re=100.

The supported disposition is **bounded numerical-qualification candidate
passed; independent review pending**. This is not independent experimental
validation, benchmark or registry promotion, product promotion, or external
release. The machine-readable report retains:

```text
qualified=true
validated=false
promotionAuthorized=false
```

## Evidence identity

The CFD source campaign is the ignored, retained, read-only directory
`benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r1`. The V2 R2
assessment is the separate ignored, read-only directory
`benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r2-recovery`.
Neither directory is admitted to normal Git history.

| Artifact | SHA-256 |
| --- | --- |
| V2 contract | `4c5117a6a6d396c2abd8dff8a66b17e7eab0452da6138a9f8edf39cb9014eadf` |
| V2 runbook | `93f3f5badb5e1b601fce15f09aae1b0e63d2681483c41a29c2ee77637039f594` |
| V2 R2 recovery runbook | `e2b511ea2bcaabec99e829981b9f7064265abec20741766c0a402f6d5d0594c4` |
| Source solver commit | `2caede4223b04a5a66ecd49e2b93baec05295a5c` |
| Source artifact manifest | `92c45e23af11a5bd155bee65511a33d07c794e92fa7d296160ff3bc7e3d2de5b` |
| Source artifact tree | `bf23029ef2df6732f842b795b23cd24dff6c1c7fb74a91faf97e00abde0ca2c6` |
| Recovery evaluator commit | `e7164c12d54658468022b1dc06ab34a5ffc0fcb1` |
| Recovery source binding | `3be530011b16025b35628ce0767da56e4aa5cebf562a5d13992ab32f2c799def` |
| Recovery artifact manifest | `a51f1488abee82715e8682fc1dc140583067974883eb16f2099e541e82a56983` |
| Recovery artifact tree | `a030cf603ff5bd65f7cf73ced4f55360fe7fe9466d56657d5048d26093191866` |

The recovery evaluator verified every source artifact size and hash before
reading it. It reran no CFD and changed no retained source artifact.

## Gate results

| Level | Cells | Pressure loss | Max dimension error | Mass imbalance | Symmetry error |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 3,456 | 0.833388626 Pa | 0.004146% | gate passed | gate passed |
| Medium | 27,648 | 0.846635465 Pa | 0.007294% | gate passed | gate passed |
| Fine | 221,184 | 0.848178524 Pa | 0.008767% | `1.47696e-7` | `1.39648e-7` |

All levels had one connected all-hexahedral 3D fluid region, passed
`checkMesh`, had zero invalid cells, finite pressure and velocity, normal
termination at the prospectively declared stop, positive static pressure loss,
no unexplained total-pressure gain, and explicit non-overlapping source-cell
ranges for `inlet-leg`, `elbow`, and `outlet-leg`. Probes resolve through
those explicit source-cell ranges rather than geometric inference.

The pressure-loss sequence was monotone. Its observed order was
`3.1017830099893517`, within the frozen 0.5–4 interval. Fine-grid GCI was
`0.029982072875125436%`, below the frozen 5% limit. The extrapolated pressure
loss was `0.8483819652025414 Pa`.

Duplicate full campaign materialization produced identical trees at each
level:

| Level | Materialized tree SHA-256 |
| --- | --- |
| Coarse | `684350b69ff8c6733302b5505c6aa5d6ed9660f9185c29c9361dbcb1d1c2f93e` |
| Medium | `dbd3090f2ec19f6f9ff0bcce82e96514278c0fdd681a90cdfd4d706f96a28dbd` |
| Fine | `91e80042d43a2f8c4b3daee59844804f2fcad4b272fbf4ea6b1bbd2d99163208` |

## Retained failures and recovery boundary

V1 remains retained as a failed hypothesis: its coarse mesh exceeded the
prospective aspect-ratio limit, its angle observation selector was
under-specified, and its generated stop did not match the declared stop.
V2 prospectively corrected mesh resolution, tightened only the observation
selector, and made the generated stop match its existing declaration without
changing a gate.

All three V2 CFD solves then completed normally. The fine runtime was
classified as an infrastructure failure only because FlowLab could not parse
Foundation's native component-wise vector wall-shear extrema record and its
large VTK fallback exceeded the diagnostics size cap. V2 R2 accepts that
native record only as component-wise extrema in solver vector units; it does
not invent pointwise scalar extrema, magnitude, mean, or Pascal values.

## Claim boundary

This assessment does not inherit any axisymmetric validation state and does
not qualify arbitrary CAD, other bend angles, other Rc/D ratios, other Reynolds
numbers, broad elbow families, branches, diameter changes, turbulence,
transient flow, compressible flow, multiphase flow, hybrid meshes, or SU2.
Independent review, empirical validation, registry admission, promotion,
notarized packaging, and external release remain separate future gates.
