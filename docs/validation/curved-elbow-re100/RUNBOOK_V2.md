# Canonical curved-elbow Re=100 qualification runbook V2

## Status

This is a prospective runbook for one bounded numerical-qualification
candidate. It is not independent experimental validation, product promotion,
benchmark-registry admission, or external release.

V2 supersedes V1 only for future execution. The immutable V1 campaign remains
failed evidence and must not be edited, deleted, or reclassified.

## Frozen scope

- one 90-degree constant-diameter circular elbow;
- `D = 0.01 m`, `Rc/D = 3`;
- 10D straight inlet and outlet legs;
- water-like fluid at `Re = 100`;
- steady, incompressible, laminar OpenFOAM;
- deterministic conformal 15-block all-hex O-grid;
- coarse/medium/fine counts frozen in
  `QUALIFICATION_CONTRACT_V2.json`.

The V2 hypothesis changes only the axial logical counts, corrects the
wall-angle endpoint selector, and makes the generated control dictionary honor
the already-declared 3000-iteration stop. No scientific threshold changes.

## Preconditions

1. Run from the repository root on branch
   `codex/geometry-elbow-qualification`.
2. Commit every path listed in
   `server.flowlab.curved_elbow_campaign.FROZEN_SOURCE_PATHS`.
3. Verify the contract SHA-256 and source commit.
4. Confirm the output directory does not exist or is empty.
5. Confirm Docker can run the pinned
   `flowlab/openfoam11-gmsh:2026-07-13` image.
6. Do not edit V1 evidence or the V2 contract after execution begins.

## Software verification

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
npm run build
```

## Deterministic materialization

Materialization is not scientific evidence:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.curved_elbow_campaign \
  /private/tmp/flowlab-elbow-v2-materialized \
  --materialize-only
```

Every complete generated case is built twice and its sorted per-file SHA-256
map must match.

## Retained execution

Use one new ignored campaign directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.curved_elbow_campaign \
  benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r1 \
  --run
```

The runner executes levels sequentially and stops at the first infrastructure
or scientific gate failure. A completed or failed directory is made
read-only. Do not restart, compact, overwrite, or repair it in place.

## Interpretation

- A mesh failure is not a solver-physics failure.
- A normal solver exit is not qualification.
- Per-level passes are not a three-grid pass.
- A three-grid pass is bounded numerical qualification only.
- V2 never authorizes promotion; `promotionAuthorized` remains false.
- Any further hypothesis requires V3 and a new output directory.
