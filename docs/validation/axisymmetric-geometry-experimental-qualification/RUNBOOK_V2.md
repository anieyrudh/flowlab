# Axisymmetric generated-geometry experimental qualification runbook v2

Status: **prospective experimental-software revision; no v2 retained result observed at freeze**

The v2 authority is `EXPERIMENTAL_QUALIFICATION_CONTRACT_V2.json`, a
digest-bound merge patch over the unchanged v1 contract. All v1 scientific
gates, grids, geometry, physics, trends, exclusions, and non-promotion
boundaries remain unchanged.

## V1 disposition

The ignored `2026-07-31-v1-r1` campaign is retained unchanged. Its coarse
OpenFOAM job completed, but evaluation stopped before any scientific gate was
decided. Two software defects were identified:

1. the checkMesh parser included sentence punctuation in the minimum-volume
   numeric token;
2. exact physical vertex coordinates could not bind generated cells to
   blockMesh cells after the already-declared axial grading.

That output is an evaluator and result-identity infrastructure failure. It is
not a scientific pass or failure and may not be edited, resumed, or reused.

## V2 identity rule

V2 maps cells by unique logical wedge-vertex signatures. Each vertex is
identified independently in the generated mesh and actual OpenFOAM `polyMesh`
by axial-station rank, radial-station rank at that axial station, and explicit
front, back, or axis side. The resulting cell signatures must be unique and
must form a one-to-one permutation. Physical file order and visual appearance
are never used as identity.

The verified permutation is still embedded as `flowlabSourceCellId` in the
controlled `postProcessing/flowlabNative/*.vtk` artifact. Identity-bound jobs
must collect that controlled artifact even when legacy `foamToVTK` files are
present.

## Fresh execution

Commit the v2 revision, implementation, and focused tests before execution.
Run only into a new empty ignored directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.axisymmetric_geometry_qualification \
  benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v2-r1 \
  --run
```

The v1 runbook remains authoritative for every unchanged preflight, execution,
evaluation, evidence-retention, and handoff requirement.
