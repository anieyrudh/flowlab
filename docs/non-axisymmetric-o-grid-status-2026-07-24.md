# Non-axisymmetric full O-grid status — 2026-07-24

Status: **software path complete; v2 three-level numerical-verification
candidate gates passed; controlled independent review pending**

This record keeps software completeness, native mesh verification, solver
execution, numerical verification, independent validation, promotion, and
release separate. Nothing in this record changes a benchmark fixture, registry
entry, validation state, promotion state, or product claim.

## Prospective v2 successor

After reviewing the v1 discrepancy, the user directed the work to proceed
without treating the narrow residual and continuity misses as catastrophic.
That direction does not reclassify v1. It authorizes a new prospective
campaign with a decision operator tied directly to the bounded engineering
quantities.

V2 preserves the v1 physical case, boundary conditions, three meshes,
discretization, relaxation, `p`/`U` residual controls, and 2,000-iteration
budget. It adds per-iteration inlet/outlet pressure and flow histories and
requires the final 100 consecutive iterations to satisfy:

- pressure-drop relative span at most `0.0005`;
- measured-flow relative span at most `0.000025`;
- maximum relative mass-flow imbalance at most `0.0001`;
- normal termination without fatal or non-finite markers; and
- either a SIMPLE convergence declaration or completion of the exact declared
  iteration budget.

The pressure threshold is one thirtieth of the frozen 1.5% fine-grid GCI gate.
The flow threshold is one quarter of the existing 0.01% conservation and
target-flow gates. Residual and continuity tails remain retained diagnostics
but are not v2 pass/fail gates.

The prospective authority is
`docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V2.json` with
runbook `docs/validation/full-ogrid-straight-pipe/RUNBOOK_V2.md`. No retained
v2 execution may begin until those files and their transitive implementation
and tests are committed and clean.

The first two v2 directories have infrastructure-only dispositions:

- `2026-07-24-v2-r1` stopped after deterministic materialization because the
  sandbox could not access the Docker socket. No solver ran.
- `2026-07-24-v2-r2` completed the coarse solver and wrote all per-iteration
  QoI histories, but evaluation stopped before applying gates because native
  OpenFOAM formats iteration markers as `Time = 2000s` and the parser accepted
  only `Time = 2000`.

Both directories remain untouched. The second issue is a parser-only mismatch;
its correction accepts the native optional `s` suffix without changing the
contract, operator, threshold, case, solver, or retained result. A new
directory and clean source commit are required for the next attempt.

## Definitive retained v2 campaign `v2-r3`

Parser-only source commit
`e816ae917b4a05016cf65f14e7f879575bc8552b` preserved the v2 contract
SHA-256
`6077a08519a1e8b423a9690a2fef6597ad42af7243caed53d4ad2c13191db875`.
The retained campaign completed all three product-path levels through
`JobManager`.

All deterministic generation, mesh, patch, XYZ extent, solver QoI-stability,
physics, fine-level, monotonicity, wall-geometry, observed-order, and GCI gates
passed. Pressure drop progressed from `0.465818954` to `0.469604754` to
`0.470842747 Pa`; the fine pressure error was `0.154262%`. Velocity-profile
relative L2 error progressed from `1.50239%` to `0.387925%` to `0.0992816%`.
Observed order was `1.6125950398033853`, and fine-grid GCI was
`0.1596996556959516%`.

The immutable package:

- has read-only files and 12 indexed artifacts;
- reproduced every artifact hash and size in an independent local check;
- reproduced package tree digest
  `11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6`;
- has package-manifest SHA-256
  `a4742d1d78f027eec125f8c0f3590170741240d79cffa315864c5a981cb1463e`;
  and
- is summarized in
  `docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_V2_2026-07-24.md`.

Disposition: **three-level numerical-verification candidate passed; controlled
independent review unavailable and therefore pending**. This is not
independent experimental validation. No fixture, registry, validation state,
product claim, promotion state, or release state changed.

## Final clean verification surface

- Focused v2 campaign, adapter, and execution tests: `174 passed`.
- Complete backend suite: `584 passed`.
- Frontend unit/component suite: `81 passed`.
- TypeScript lint: passed.
- Production web build: passed.
- Playwright desktop E2E: `16 passed`.
- Clean-source portable macOS app build: passed.
- Internal package contract QA: passed.
- Strict code-signature and property-list checks: passed.
- Native packaged interaction: backend online; OpenFOAM, steady, full-360
  O-grid, and the exact medium `32/8/64/16` controls confirmed.

The final app manifest binds clean source commit
`65c23bea33feb04c583a1f33303570dd2ae36498` and has SHA-256
`afce581cc2ce3fc3b96fc845cfda0139c38756e59104bc08bc787609f002b5a3`.
External distribution remains separately blocked because the package is
ad-hoc signed and has not been notarized or independently checked on a clean
supported Mac.

## Prerequisite disposition

The desktop-release QA goal recorded a definitive disposition before this work
began:

- the self-contained internal macOS arm64 package passed its bounded portable
  QA surface;
- external release remains blocked on Developer ID signing, Team ID,
  notarization/stapling, and an independent clean-Mac check; and
- no scientific or promotion state was changed by that release work.

Implementation started from clean commit `5f525dc` on the dedicated branch
`codex/full-ogrid-product-milestone`.

## Implemented software surface

- Explicit `solver.meshMode = "full-ogrid"`.
- Exactly one editor-authored source-to-sink, constant-diameter circular pipe.
- Fail-closed steady, incompressible, laminar scope.
- Deterministic five-block all-hex topology:
  - one center block;
  - four circumferential wall blocks;
  - four conformal internal interfaces;
  - explicit `inlet`, `outlet`, and `walls` patches;
  - no collapsed-axis cells.
- Explicit axial, annular-radial, circumferential, and core resolution.
- Product adapter, API, JobManager native-mesh routing, result loading,
  full-volume VTK/VTU preview, editor controls, exterior-surface extraction,
  and solver-space XYZ owner-cell probing.
- A prospective-request hook for a fully developed parabolic inlet and pressure
  outlet. This hook is not a frozen campaign contract and is not validation
  evidence.

The coarse/medium/fine product defaults refine every material discretization
dimension:

| Level | Axial | Annular radial | Circumferential | Core side | Cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 16 | 4 | 32 | 8 | 3,072 |
| Medium | 32 | 8 | 64 | 16 | 24,576 |
| Fine | 64 | 16 | 128 | 32 | 196,608 |

## Native software preflight disposition

A disposable coarse product case with length `0.024 m` and diameter `0.006 m`
was materialized and run with the configured
`flowlab/openfoam11-gmsh:2026-07-13` image:

- image ID:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`;
- OpenFOAM Foundation version: `11`;
- `blockMesh`: exit `0`;
- `checkMesh -allGeometry -allTopology`: exit `0`, `Mesh OK`;
- one connected region and three geometric/solution directions;
- 3,072 hexahedra, 3,553 points, 9,664 faces, and 8,768 internal faces;
- patches: `inlet` 192 faces, `outlet` 192 faces, `walls` 512 faces;
- bounds: `(0,-0.003,-0.003)` to `(0.024,0.003,0.003)`;
- minimum volume: `2.6367187e-11 m^3`;
- maximum aspect ratio: `16`;
- maximum non-orthogonality: `32.152674°`;
- maximum skewness: `0.55753716`;
- minimum cell determinant: `0.10455798`;
- minimum interpolation weight: `0.16487075`; and
- minimum face-volume ratio: `0.151744`.

The real FlowLab JobManager product path then:

- detected the Foundation runtime and normalized function objects;
- skipped non-applicable surface extraction and `snappyHexMesh`;
- repeated `blockMesh` and full `checkMesh` successfully;
- completed the steady solver in 56 iterations with exit `0`;
- exported five bounded VTK result artifacts;
- produced seven `fullOGridXYZProbes` pressure samples;
- reported relative inlet/outlet flow imbalance
  `4.627486223418557e-08`; and
- exported a 3,072-cell full-volume VTK containing `U`, `p`, `cellID`, and
  `wallShearStress`, with XYZ spans `[0.024, 0.006, 0.006]`.

The disposable solver VTK had SHA-256
`afadc9985eed2d711bfbed70b0bd0ddecc0d3678d3d899fd1c923279c902b23b`.
This hash records the software-preflight disposition only; the run is not a
retained scientific campaign and is not promotion evidence.
Both disposable preflight directories were removed after the disposition and
metrics above were recorded.

Directly invoking the generated `bash Allrun` without JobManager runtime
normalization failed before iteration one because Foundation OpenFOAM 11 does
not provide the generic `patchFlowRate` function-object type. The product path
passed because its existing runtime adapter converts the normalized diagnostic
contract to Foundation-compatible per-patch `surfaceFieldValue` objects. The
standalone-path limitation remains open and must not be hidden by the passing
JobManager result.

The optional pre-solve `postProcess -func yPlus` also exited `1` because a
turbulence model was not loaded in that pre-solve context. It remained
non-blocking for this laminar software execution. Production-mesh approval
remains blocked and was not reinterpreted.

## Verification completed for this increment

- Backend: `573 passed`.
- Frontend: `81 passed`.
- Type/lint gate: passed.
- Production build: passed.
- Focused topology, geometry-contract, adapter, execution-router, API, schema,
  editor-control, preview-surface, and probing tests: passed.

## Prospective scientific freeze

The separate O-grid campaign was frozen before any retained scientific
execution:

- contract:
  `docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V1.json`;
- contract SHA-256:
  `4ba46eb81f442fdd3dceb2cff44d6820a51e26d9b4b7159391089430710a5b0d`;
- runbook:
  `docs/validation/full-ogrid-straight-pipe/RUNBOOK.md`;
- boundary operator: discrete-flux-normalized fully developed parabolic
  velocity inlet, zero kinematic-pressure outlet, and no-slip wall;
- physical case: length `0.024 m`, radius `0.006 m`, density
  `1000 kg/m^3`, dynamic viscosity `0.001 Pa s`, and volumetric flow
  `1e-5 m^3/s`;
- analytical pressure drop: `0.471570201753764 Pa`;
- Reynolds number: `1061.032953945969`;
- observation operators: patch-average pressure, signed surface flux,
  mid-plane cell-centred velocity profile, retained residual/continuity
  history, solver VTK bounds, and wall-facet area deficit;
- three-grid interpretation: combined solution-discretization and
  wall-geometry-realization convergence, not isolated solution
  discretization; and
- review boundary: the exact immutable package digest must receive controlled
  independent review before any fixture, registry, validation, or claim
  mutation.

The campaign runner independently rebuilds and hashes every generated level,
requires committed clean transitive scientific paths and an immutable Docker
image ID, executes through `JobManager`, evaluates the frozen per-level and
three-grid gates, records mathematically invalid GCI as unqualified, and
packages either a pass or a failure without overwriting evidence.

Focused contract, materialization, strict case-generation, source-cleanliness,
synthetic retained-level evaluation, and combined-geometry GCI tests passed:
`17 passed`.

## States at prospective freeze

- Prospective O-grid scientific contract: **frozen before retained execution**.
- Retained coarse/medium/fine scientific runs: **not started**.
- Observed order and GCI: **not evaluated on retained outputs**.
- Conservation/profile gates: **operators frozen; not evaluated on retained
  outputs**.
- Immutable scientific evidence package: **implementation complete; no
  retained package assembled**.
- Controlled independent scientific review: **not requested**.
- Benchmark/registry/validation changes: **none**.
- O-grid promotion: **not authorized**.
- External release: **blocked independently**.

That gate was satisfied by commit
`5e370d1f1d1b322cc5ac17b4dad7e567fa1ce241` before the first retained
campaign attempt.

## Retained campaign attempt `v1-r1`

The first retained attempt started from clean source commit
`5e370d1f1d1b322cc5ac17b4dad7e567fa1ce241`, contract SHA-256
`4ba46eb81f442fdd3dceb2cff44d6820a51e26d9b4b7159391089430710a5b0d`,
and immutable image ID
`sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`.

The coarse JobManager level completed with solver exit `0`, but the campaign
stopped before evaluation and before medium or fine began. The evaluator
expected an alternate `checkMesh` label for determinant, interpolation-weight,
and face-volume-ratio metrics. Foundation OpenFOAM 11 retained the same
contracted quantities as:

- `Cell determinant (wellposedness) : minimum: ...`;
- `Face interpolation weight : minimum: ...`; and
- `Face volume ratio : minimum: ...`.

Disposition: **retained evaluation-infrastructure failure; not a scientific
gate result**. The `v1-r1` tree remains untouched at
`benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v1-r1`.
No evidence was deleted, resumed, or rewritten. A parser-only successor may
accept both labels without changing the frozen operator, value, threshold, or
contract hash, but it must use a new output directory and committed source.

A read-only diagnostic application of that parser-only repair to the retained
coarse artifacts found:

- every frozen mesh gate passed;
- every frozen pressure, flow, conservation, and velocity-profile gate passed;
- pressure-drop relative error: `0.012195952442234873`;
- velocity-profile relative L2 error: `0.015023886681146731`;
- velocity-profile relative Linf error: `0.016628754253039706`;
- relative flow imbalance: `2.0000001343099295e-09`;
- no SIMPLE convergence declaration by the frozen 2,000-iteration limit;
- maximum final linear residual: `1.3859648e-07`, above the frozen `1e-07`
  limit; and
- maximum absolute global continuity error: `1.3146633e-08`, above the frozen
  `1e-08` limit.

These diagnostic values do not retroactively turn `v1-r1` into a completed
scientific evaluation. They do establish that a clean parser-only successor
must enforce the runbook's mandatory per-level stop rule if the same solver
gates recur. No gate, threshold, iteration limit, or observation operator will
be changed after observing them.

## Definitive retained campaign `v1-r2`

Parser-only successor commit
`8ed349be9f6c2b513ec242c327e86f6a7bf70a5a` kept the contract hash,
operators, thresholds, iteration cap, geometry, physics, and image identity
unchanged. The retained coarse JobManager run completed and was evaluated
successfully.

Disposition: **frozen scientific gates failed at coarse; medium and fine were
not launched under the prospective mandatory-stop rule**.

Passing coarse groups:

- deterministic generated case;
- every native mesh and patch gate;
- every pressure, flow, conservation, and velocity-profile gate; and
- solver process exit `0`.

Failing coarse solver gates:

- no SIMPLE convergence declaration by iteration `2000`;
- maximum final linear residual `1.3859648e-07` exceeded `1e-07`; and
- maximum absolute global continuity error `1.3146633e-08` exceeded `1e-08`.

The runner produced a read-only partial-failure package:

- campaign:
  `benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v1-r2`;
- package manifest SHA-256:
  `e53de417585feabca4250399d3244db9d7b62fcae7194ddf4b4f5a0df5c4ae80`;
- artifact index SHA-256:
  `69fff1179e7639cde3496069e2e1b24e348597ff2dc4d685843e6e8be9191072`;
- package tree digest:
  `d80574cd2d2c10ba38d5d02f0ee49098193abffe6371056446d8bb9121c68f2f`;
- campaign result SHA-256:
  `d4d6ae97eb1299a66fb0572c4f56bda03b6d74002390760ac27c2c02c47242f9`;
- review request SHA-256:
  `33c2b6461f27f4038aef6784fe641474ce52b803e1ad7c1745c6fbe0d71c332c`;
- package state: `candidate-gates-failed`;
- GCI: unqualified because the mandatory stop prevented a three-level
  sequence; and
- immutable mutation flags: fixture, registry, validation state, product
  claim, and promotion all `false`.

A separate read-only audit recomputed all 12 indexed artifact sizes and hashes,
recomputed the tree digest, checked the review-to-package-manifest binding, and
opened every deterministic tar archive successfully.

## Current milestone states

- Software completeness: **implemented and regression-tested**.
- Native mesh verification: **coarse, medium, and fine passed every frozen v2
  mesh and topology gate**.
- Solver execution: **all three v2 levels exited `0`, reached the declared
  stop, terminated normally, and passed frozen QoI-stability gates**.
- Three-level numerical verification: **candidate gates passed; observed order
  and GCI mathematically qualified**.
- Independent validation: **not claimed**.
- Immutable evidence: **read-only complete v2 package assembled and
  hash-audited; v1 failure package remains immutable**.
- Controlled independent review: **pending; no controlled reviewer identified
  in this session**.
- Benchmark, registry, or validation-state mutation: **none and unauthorized**.
- Product promotion: **not authorized**.
- External release: **blocked independently**.

The exact remaining blocker is a controlled independent reviewer for the
exact v2 package tree digest. Until that review is accepted, the passing
three-level result remains a numerical-verification candidate and cannot
change a fixture, registry, validation state, product claim, promotion state,
or release state. V1 remains a failed campaign and is not superseded as
evidence by the v2 result.
