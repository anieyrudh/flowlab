# Full-O-grid generated-geometry experimental qualification v5

V5 uses the complete V3 runbook and base contract without changing any
scientific gate. Read `RUNBOOK_V3.md`, `RUNBOOK_V4.md`,
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json`, and
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V5.json` together.

## Predecessor disposition

The V3 and V4 retained directories are immutable. Do not edit, resume, compact,
delete, or reuse them, and do not present any V3 or V4 observation as a V5
outcome.

- `2026-07-31-v3-r1` stopped before solver launch on native mesh command routing.
- `2026-07-31-v4-r1` generated and meshed the coarse level correctly
  (`blockMesh` 2,496 hexahedra, `checkMesh` `Mesh OK`) but stopped before solver
  launch because the generated and solver logical vertex signatures disagreed for
  888 of 2,496 cells. The medium and fine levels were never queued.

## What V5 changes

V5 changes result-identity and evaluator infrastructure only.

1. **Identity algorithm** `full-ogrid-normalized-logical-vertex-signature-v4`.
   The v3 algorithm compared `format(value, ".9g")` labels as exact text.
   `cos(22.5 deg)` lies 1.13e-11 above a rounding boundary, and OpenFOAM writes
   `constant/polyMesh/points` with ten significant digits, so one physical vertex
   was labelled `0.923879533` on one side and `0.923879532` on the other. Every
   16-sector butterfly contains that coordinate, so the failure was structural.
   v4 clusters within a relative tolerance of 1e-6 and averages the per-station
   scale over the outer wall ring. Verified against the retained V4 meshes:
   coarse 888 to 0, medium 5,920 to 0, fine 56,344 to 0 mismatching cells.
2. **Two checkMesh parser repairs.** The geometric-direction gate searched for
   `Mesh has 3 geometric (non-empty) directions`, but OpenFOAM 11 always writes
   `(non-empty/wedge)` on that line, so the gate was permanently false rather
   than merely failing. The connected-region gate searched for
   `^\s*regions:\s+1\s*$`, but OpenFOAM writes `Number of regions: 1 (OK).`.
   Both now parse the reported values. **Both required values are unchanged**
   (3 geometric directions, 1 region).

No geometry, physics, grid, numerical threshold, expected trend, scientific
claim, product status, or promotion gate changes.

## Execution

Commit the exact V5 contract, runner, identity implementation, and tests first.
`_source_identity` refuses to run with any frozen path uncommitted.

Choose a new empty ignored directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.full_ogrid_geometry_qualification \
  benchmarks/cases/full-ogrid-geometry/campaigns/<new-v5-run-id> \
  --materialize-only
```

Then execute the same command with `--run` against a second new empty
directory. The runtime preflight must record `surfaceFeatureExtract` and
`snappyHexMesh -overwrite` as not applicable and must execute only `blockMesh`
and `checkMesh` before the solver.

Expected cost, measured on an Apple Silicon host with the pinned amd64 image
under emulation: coarse about 2 minutes, medium about 13 minutes, fine about
107 minutes. Emulation is not the dominant cost; a native arm64 runtime measured
only 1.11 times faster on the same case, so do not substitute the image for
speed. Give Docker at least 16 GB before the fine level.

All V3 scientific, identity, retention, and nonpromotion requirements remain
conjunctive and unchanged. `promotionAuthorized` stays false regardless of
outcome.
