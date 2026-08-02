# Benchmarks and verification status

FlowLab publishes benchmark definitions, frozen evaluation contracts, compact
review records, and reproduction tools. It publishes them separately from the
raw solver campaigns. The raw meshes and the field histories stay in the
governed evidence archive.

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
benchmark candidate. The contract was frozen before the retained scientific
runs. The campaign refined all material mesh dimensions at three levels. The
cases ran through FlowLab's normal case-generation and JobManager product path.

| Level | Cells | Pressure drop | Pressure error | Velocity L2 error | Flow imbalance |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 3,072 | 0.465818954 Pa | 1.21960% | 1.50239% | `2.0e-9` |
| Medium | 24,576 | 0.469604754 Pa | 0.416788% | 0.387925% | `2.0e-9` |
| Fine | 196,608 | 0.470842747 Pa | 0.154262% | 0.0992816% | `0.0` |

The pressure-drop sequence is monotone. The observed order is `1.6125950398`.
The fine-grid GCI is `0.1596996557%`.

All three meshes passed the frozen topology and quality checks. These checks
are:

- exact cell and patch counts;
- one connected all-hex region;
- positive volumes;
- full XYZ extents;
- the required inlet, outlet, and wall roles; and
- no collapsed-axis cells.

The retained package tree digest is:

```text
11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6
```

This supports a deliberately narrow statement. FlowLab's O-grid path uses a
full revolution and five blocks. That path numerically reproduces
Hagen-Poiseuille behavior for the frozen straight-pipe case, which is steady,
incompressible, and laminar. It is not a general CFD result and it is not an
arbitrary-geometry result. A controlled review of the exact package is still
pending. Thus the registry state and the promotion state do not change.

## Why the evidence is publishable

- The contract, the gates, the observation operators, the mesh sequence, and
  the provenance requirements were prospective.
- The axial, annular-radial, circumferential, and core resolution all doubled.
- Case generation was deterministic. It used the user-facing product path
  through OpenFOAM Foundation 11.
- The campaign evaluated the mesh, conservation, profile, iterative-stability,
  and three-grid checks at every required level.
- The evidence package is content-addressed, hashed, and read-only.
- Failed or interrupted predecessor runs keep their original dispositions.

The remaining step is review, not threshold adjustment. The complete frozen
contract and review packet are:

- `docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V2.json`;
- `docs/validation/full-ogrid-straight-pipe/RUNBOOK_V2.md`;
- `docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_V2_2026-07-24.md`.

## Canonical curved-elbow result

The bounded V2 campaign generated and solved one circular elbow through
OpenFOAM Foundation 11. The elbow has a constant diameter and a 90-degree bend.
Its centreline radius is Rc/D=3. Its straight inlet and outlet legs are 10D
long. Its Reynolds number is Re=100.

The coarse, medium, and fine all-hex meshes contained 3,456, 27,648, and
221,184 cells. All the prospective gates passed. These gates cover geometry,
mesh, solver, conservation, symmetry, pressure, and explicit source-cell
provenance.

Pressure loss increased monotonically from `0.833388626 Pa` to
`0.846635465 Pa` and `0.848178524 Pa`. The observed order was
`3.1017830100`. The fine-grid pressure-loss GCI was `0.0299820729%`.

The fine result had a relative mass-flow imbalance of `1.47696e-7`. Its maximum
symmetry-plane error was `1.39648e-7`. It had positive static-pressure and
total-pressure losses, one connected 3D fluid region, and no invalid cells.

The fine solve completed normally. But the original runtime classified its
diagnostics as an infrastructure failure. There were two causes: the parser did
not accept OpenFOAM Foundation's component-wise wall-shear extrema table, and
the surface VTK fallback was larger than its size cap.

V2 R2 corrected only that parser. It then re-evaluated the unchanged, fully
hashed source campaign into a separate read-only package. It did not rerun CFD,
edit retained evidence, or change a scientific gate.

This result is a bounded numerical-qualification candidate. It is not
independent experimental validation. It does not support claims about:

- arbitrary CAD;
- other elbows or other Reynolds numbers;
- turbulence, transient flow, compressible flow, or multiphase flow;
- hybrid meshes;
- SU2;
- registry promotion or product promotion; or
- external release.

`validated` and `promotionAuthorized` remain false. The frozen contract and
assessment are:

- `docs/validation/curved-elbow-re100/QUALIFICATION_CONTRACT_V2.json`;
- `docs/validation/curved-elbow-re100/RUNBOOK_V2.md`;
- `docs/validation/curved-elbow-re100/RECOVERY_RUNBOOK_V2_R2.md`;
- `docs/validation/curved-elbow-re100/QUALIFICATION_ASSESSMENT_V2_R2.md`.

## Reproducing checks

Run these commands to check the software:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
```

The campaign runbook documents the governed scientific runner and its exact
command. Do not write a reproduction into an existing retained campaign
directory. Use a new output directory. Keep the original contract.

Git holds only the compact evidence named in `benchmarks/tracked-evidence.txt`.
The source clone does not hold the full raw campaign. This is intentional.
