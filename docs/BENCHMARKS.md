# Benchmarks and verification status

FlowLab publishes benchmark definitions, frozen evaluation contracts, compact
review records, and reproduction tools separately from raw solver campaigns.
Raw meshes and field histories remain in the governed evidence archive.

## Current status

| Evidence track | Result | What it supports |
| --- | --- | --- |
| Laminar all-hex analytical/manufactured suite | Numerical gates passed | Solver and numerical-method verification within its frozen cases |
| Full-360 O-grid straight circular pipe v2 | Candidate passed; independent review pending | End-to-end product-path numerical verification for one steady incompressible laminar pipe |
| Canonical 90-degree circular elbow V2 R2 | Bounded candidate passed; independent review pending | One true-3D OpenFOAM elbow at Rc/D=3, 10D legs, and Re=100 |
| FDA nozzle Re=500 v2 | Frozen validation gates did not pass | Retained diagnostic evidence; no validation or promotion claim |
| Desktop release | macOS and Windows candidate CI passed | Signed public release checks remain |

## Full-360 O-grid result

The O-grid campaign is rigorous enough to include as a numerical-verification
benchmark candidate. Its contract was frozen before the retained scientific
runs, all material mesh dimensions were refined at three levels, and the cases
ran through FlowLab's normal case-generation and JobManager product path.

| Level | Cells | Pressure drop | Pressure error | Velocity L2 error | Flow imbalance |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 3,072 | 0.465818954 Pa | 1.21960% | 1.50239% | `2.0e-9` |
| Medium | 24,576 | 0.469604754 Pa | 0.416788% | 0.387925% | `2.0e-9` |
| Fine | 196,608 | 0.470842747 Pa | 0.154262% | 0.0992816% | `0.0` |

The pressure-drop sequence is monotone, with observed order
`1.6125950398` and fine-grid GCI `0.1596996557%`. All three meshes passed the
frozen topology and quality checks: exact cell and patch counts, one connected
all-hex region, positive volumes, full XYZ extents, required inlet/outlet/wall
roles, and no collapsed-axis cells. The retained package tree digest is:

```text
11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6
```

This supports a deliberately narrow statement: FlowLab's full-revolution
five-block O-grid path numerically reproduces Hagen-Poiseuille behavior for the
frozen steady, incompressible, laminar straight-pipe case. It is not a general
CFD or arbitrary-geometry result. Controlled review of the exact package is
still pending, so its registry and promotion states remain unchanged.

## Why the evidence is publishable

- The contract, gates, observation operators, mesh sequence, and provenance
  requirements were prospective.
- Axial, annular-radial, circumferential, and core resolution all doubled.
- Case generation was deterministic and exercised the user-facing product
  path through OpenFOAM Foundation 11.
- Mesh, conservation, profile, iterative-stability, and three-grid checks were
  evaluated at every required level.
- The evidence package is content-addressed, hashed, and read-only.
- Failed or interrupted predecessor runs retain their original dispositions.

The remaining step is review, not threshold adjustment. The complete frozen
contract and review packet are:

- `docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V2.json`;
- `docs/validation/full-ogrid-straight-pipe/RUNBOOK_V2.md`;
- `docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_V2_2026-07-24.md`.

## Canonical curved-elbow result

The bounded V2 campaign generated and solved one constant-diameter,
90-degree circular elbow with centreline radius Rc/D=3, 10D straight inlet and
outlet legs, and Re=100 through OpenFOAM Foundation 11. The coarse, medium,
and fine all-hex meshes contained 3,456, 27,648, and 221,184 cells. Every
prospective geometry, mesh, solver, conservation, symmetry, pressure, and
explicit source-cell provenance gate passed.

Pressure loss increased monotonically from `0.833388626 Pa` to
`0.846635465 Pa` and `0.848178524 Pa`. The observed order was
`3.1017830100`, and the fine-grid pressure-loss GCI was
`0.0299820729%`. The fine result had relative mass-flow imbalance
`1.47696e-7`, maximum symmetry-plane error `1.39648e-7`, positive static and
total-pressure losses, one connected 3D fluid region, and no invalid cells.

The fine solve completed normally, but the original runtime classified its
diagnostics as an infrastructure failure because the parser did not accept
OpenFOAM Foundation's component-wise wall-shear extrema table and the surface
VTK fallback exceeded its size cap. V2 R2 corrected only that parser and
re-evaluated the unchanged, fully hashed source campaign into a separate
read-only package. It did not rerun CFD, edit retained evidence, or change a
scientific gate.

This result is a bounded numerical-qualification candidate, not independent
experimental validation. It does not support arbitrary CAD, other elbows or
Reynolds numbers, turbulence, transient, compressible, multiphase, hybrid-mesh,
SU2, registry-promotion, product-promotion, or external-release claims.
`validated` and `promotionAuthorized` remain false. The frozen contract and
assessment are:

- `docs/validation/curved-elbow-re100/QUALIFICATION_CONTRACT_V2.json`;
- `docs/validation/curved-elbow-re100/RUNBOOK_V2.md`;
- `docs/validation/curved-elbow-re100/RECOVERY_RUNBOOK_V2_R2.md`;
- `docs/validation/curved-elbow-re100/QUALIFICATION_ASSESSMENT_V2_R2.md`.

## Reproducing checks

Software verification:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
```

The governed scientific runner and its exact command are documented in the
campaign runbook. Do not write a reproduction into an existing retained
campaign directory; use a new output directory and preserve the original
contract.

Only compact evidence named in `benchmarks/tracked-evidence.txt` is admitted
to Git. The full raw campaign is intentionally not part of the source clone.
