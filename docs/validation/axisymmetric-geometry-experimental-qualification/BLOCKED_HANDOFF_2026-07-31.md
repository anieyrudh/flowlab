# Axisymmetric generated-geometry qualification blocked handoff

Date: 2026-07-31  
Branch: `codex/geometry-axisymmetric-qualification`  
Exact Prompt 1 baseline: `d3bef15698272ac57f73ca6a82b27e0e21c11d8d`  
Initial prospective implementation commit: `0f05666eee9f2fc8b2c7aeb516c8f16ca40998e3`  
V2 identity revision implementation commit: `d2b7451f2789684a9dba440c8ff9ff8a2faffdd1`

## Outcome

The software path and prospective evidence machinery are implemented, but the
experimental geometry qualification is **blocked**. No validation, promotion,
release, registry, fixture-pointer, retained-evidence allowlist, or existing
straight-pipe campaign status changed.

The blocker is a frozen v1 mesh gate, not missing Docker or an unverifiable
result identity. OpenFOAM correctly reports the bounded wedge as:

- two geometric non-empty/wedge directions `(1 1 0)`;
- three solution non-empty directions `(1 1 1)`;
- a positive-volume 3D mesh with the exact required patches;
- `Mesh OK`.

The v1 contract additionally froze `geometricDirections: 3`. That condition
cannot pass for this OpenFOAM wedge classification. It may not be weakened,
removed, or reinterpreted after observing the retained result. A fresh
three-level solver campaign was therefore not started.

## Frozen contracts

- V1 contract SHA-256:
  `b0a2cceaf4a83f95d3f30c9318496f06ec0f12e1c90c71066c48a99f8b3f83bb`
- V2 revision SHA-256:
  `072e356d925521cd7969e9181505887b2e0f972522c152e571f8aeb2a5c9ebed`
- Existing stricter straight-pipe campaign SHA-256, unchanged:
  `c03e608b486a2f1cd0374bede89a579bb9092554624c2ba1803b1bd049f88925`

V2 is a digest-bound prospective revision over the unchanged v1 contract. It
changes only evaluator/result-identity infrastructure. It changes no geometry,
physics, grid, numerical threshold, expected trend, scientific claim, or
product status.

## Generation evidence

The clean-commit v2 materialization is retained locally under the ignored
directory:

`benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v2-preflight-r1`

Its `preflight-report.json` SHA-256 is
`6c2e5080dd31c5456c91a86c76610e46ccbb2868727fc11eb3ba6015c21e0ac9`.

All seven cases reproduced identical generated-file hashes across two
independent builds. Venturi, contraction, expansion, and nozzle generation
each produced a positive-volume 3D wedge with exact inlet, outlet, walls,
front, back, and axis roles.

Generated file-set digests:

| Case | SHA-256 |
|---|---|
| Venturi | `2cb793e34db1bc5dbb1bacb3c1322ec56deb6138d820b3db1e5599ef78782a87` |
| Contraction | `395ce1f6d532e64425d6140c0b77ba15323a50947de2dd5b47778c575fe05db5` |
| Expansion | `c1f59907abe2e1c5c4e42a975a201a57901abc0e40b109e4650b3777b2a65e14` |
| Nozzle | `59dac576b2f1448170d2fc7905549cf3dfad7634657ed8fd2b47f941b4282491` |
| Coarse composite | `82942296836fc17ca7f57a5f988b98f0bfd7a4743f044b546e08f71b2eebd07f` |
| Medium composite | `9ec32c2d7bba0e49e0d42ac28adef178ca487615800c372db60661b066aeacc0` |
| Fine composite | `7f10dff910606532293bd2c2b93ac82f17ad06d7869b29401f9691731967ed99` |

The coarse, medium, and fine v2 source-identity contract SHA-256 values are,
respectively:

- `03d3606d4c95ea839237d7d2671146b3fcb170a25e583844edb02f1bdafa0d58`
- `b960434d28eff878d5418ff91f6f451596deecbe52011e8023bf31e998482c11`
- `b776e270df0d1b82af990735aaf63b535dfdebe947eed8b1d9f19804b2b8808e`

## Retained runtime evidence and classification

The ignored v1 directory
`benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v1-r1` remains
unchanged. It is a retained evaluator/result-identity infrastructure failure,
not a scientific pass or failure.

- Frozen source commit:
  `0f05666eee9f2fc8b2c7aeb516c8f16ca40998e3`
- OpenFOAM image:
  `flowlab/openfoam11-gmsh:2026-07-13`
- Immutable image ID:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Host/container architecture:
  arm64 macOS / emulated linux-amd64
- Coarse job:
  `job-4e124ac1c7`, complete, exit code 0
- Mesh:
  208 cells, minimum volume `1.119835e-10 m3`, exact patches, `Mesh OK`
- Solver:
  normal termination, finite `p` and `U`
- Final relative mass-flow imbalance:
  `0`
- Pressure-tail relative span:
  `0`
- Flow-tail relative span:
  `1.1512687958366282e-7`

The v1 evaluator first stopped because its numeric expression included the
period after checkMesh's minimum-volume value. After that parser defect was
isolated, exact physical vertex matching was shown to be unsuitable for the
already-declared blockMesh grading.

V2 uses unique logical wedge-vertex signatures: axial-station rank,
radial-station rank, and explicit front/back/axis side. Against a disposable
copy of the retained coarse case, this produced a one-to-one 208-cell
permutation without using file order or visual appearance. The mapping
SHA-256 was
`81744bbd0b2b160616b4b65375eccfdf99d8600dbecd974df328f612d3faccdc`.
The controlled native artifact contained complete unique
`flowlabSourceCellId` values and passed full and bounded-preview loading. This
diagnostic copy is not retained scientific campaign evidence.

The coarse diagnostic trends passed contraction velocity/pressure, throat
velocity/pressure, and recovery velocity direction. Recovery mean pressure was
not above throat mean pressure at coarse resolution. The frozen trend gate is
fine-grid-only, so no scientific trend decision was made.

No medium or fine solve was run. Observed order and GCI are unresolved.

## Result pipeline and unsupported capabilities

Identity-bound OpenFOAM jobs now surface only the controlled
`postProcessing/flowlabNative/*.vtk` conversion. A legacy `foamToVTK` artifact
cannot mask a missing or ambiguous source-cell mapping. Result metadata,
full/preview loading, and the browser all require the explicit identity
contract. Edge ownership must be unique; connectors and other unowned cells
remain probe-only.

The focused browser E2E selected both owned multi-edge results through explicit
source IDs and preserved linked schematic selection. SU2 axisymmetric and full
O-grid requests fail closed without planar fallback.

## Verification

- Focused backend:
  `243 passed`
- Backend excluding locally ignored evidence:
  `595 passed, 3 skipped, 7 deselected`
- Exact required backend command:
  `595 passed, 3 skipped, 7 failed`
- Frontend:
  `94 passed`
- TypeScript lint:
  passed
- Production build:
  passed
- Focused multi-edge provenance E2E:
  `1 passed`

All seven exact-command backend failures are marked `local_evidence` and
depend on older ignored FDA-nozzle or laminar-all-hex campaign trees that are
not present in this isolated worktree. They are not failures in the
axisymmetric qualification implementation. Those raw trees were not copied,
tracked, or mutated to manufacture a green full-suite result.

Two long frontend integration tests exceeded their original fixed five-second
budgets on this host but passed unchanged assertions with explicit 20-second
per-test budgets. The focused Playwright provenance test similarly required a
120-second budget under software-rendered execution and passed in about 90
seconds.

## Integration conflicts

The files most likely to conflict with concurrent result-pipeline or geometry
work are:

- `server/flowlab/adapters.py`
- `server/flowlab/execution.py`
- `server/flowlab/results.py`
- `server/flowlab/schemas.py`
- `server/flowlab/result_identity.py`
- `src/App.tsx`
- `src/results/vtk.ts`
- `src/types.ts`
- `tests/e2e/editor.spec.ts`

## Lawful next step

Do not continue this campaign or change its frozen gate. A future experiment
would need a new question and independently reviewed prospective contract
whose mesh-dimensionality criterion is declared before any run. That review
must decide whether the intended product requirement is positive-volume 3D
wedge geometry with three solution directions, or a different mesh topology
that OpenFOAM itself reports as three geometric directions. No result from the
v1 or v2 directories may be reused as the new experiment's outcome.
