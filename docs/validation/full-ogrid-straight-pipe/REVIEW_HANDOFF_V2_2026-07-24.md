# Full O-grid v2 controlled-review handoff

Status: **all frozen numerical-verification candidate gates passed; controlled
independent review pending**

This handoff does not authorize a fixture, registry, validation-state, product
claim, promotion, or release change. The reviewer must be independent of
implementation and execution and must bind their decision to the exact package
tree digest below.

## Bound evidence

- Campaign:
  `benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v2-r3`
- Source commit:
  `e816ae917b4a05016cf65f14e7f879575bc8552b`
- Contract:
  `docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V2.json`
- Contract SHA-256:
  `6077a08519a1e8b423a9690a2fef6597ad42af7243caed53d4ad2c13191db875`
- Package manifest SHA-256:
  `a4742d1d78f027eec125f8c0f3590170741240d79cffa315864c5a981cb1463e`
- Package tree digest SHA-256:
  `11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6`
- Independent-review request SHA-256:
  `0e2a229cf852b011f2d9dd3ce5fc15633c41ecef11efc1e2b3b9df28a9ed70a0`
- Container:
  `flowlab/openfoam11-gmsh:2026-07-13`
- Immutable image ID:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Detected solver:
  OpenFOAM Foundation 11

The package contains 12 indexed artifacts. An independent local recomputation
found no hash or size mismatches, reproduced the declared package tree digest,
and opened every deterministic tar archive successfully. Package files are
read-only.

## Candidate results

| Level | Cells | Pressure drop (Pa) | Pressure error | Velocity L2 | Velocity Linf | Flow imbalance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Coarse | 3,072 | 0.465818954 | 1.21960% | 1.50239% | 1.66288% | `2.0e-9` |
| Medium | 24,576 | 0.469604754 | 0.416788% | 0.387925% | 0.502737% | `2.0e-9` |
| Fine | 196,608 | 0.470842747 | 0.154262% | 0.0992816% | 0.167092% | `0.0` |

All levels have:

- exact contracted cell and patch-face counts;
- one connected all-hex region;
- three geometric and solution directions;
- positive cell volumes and all frozen quality gates;
- patches `inlet`, `outlet`, and `walls` with the required roles;
- solver VTK spans `[0.024, 0.012, 0.012]`;
- zero collapsed-axis cells;
- normal solver exit and the exact 2,000-iteration declared stop; and
- 100 consecutive final QoI samples passing pressure, flow, and conservation
  stability gates.

The last-100 pressure-drop relative span was zero at retained output precision
for every level. Maximum measured-flow relative span was approximately
`2.0e-9`, `1.0e-9`, and `1.0e-9` from coarse to fine. Residual and global
continuity tails are retained diagnostics under v2, not pass/fail gates.

## Three-grid result

- Pressure drop is monotone:
  `0.465818954`, `0.469604754`, `0.470842747 Pa`.
- Observed order:
  `1.6125950398033853`.
- Fine-grid GCI:
  `0.1596996556959516%`.
- Richardson extrapolated pressure drop:
  `0.4714442943966627 Pa`.
- Polygon wall-area deficits:
  `0.006413148855794248`,
  `0.0016056069643816118`,
  `0.00040154685032089965`.
- Polygon-deficit ratios:
  `3.994220876006369`,
  `3.998554497684335`.

The order and GCI quantify the combined solution-discretization and
wall-geometry-realization sequence. They do not isolate solution discretization
and do not establish independent experimental validation.

## Predecessor dispositions

- V1 remains an immutable coarse scientific-gate failure. V2 does not
  reclassify it.
- `2026-07-24-v2-r1` is a retained pre-execution Docker-permission
  infrastructure failure.
- `2026-07-24-v2-r2` is a retained pre-gate native-log parser infrastructure
  failure. A read-only diagnostic after the parser-only correction showed its
  coarse data would pass v2, but it remains unclassified as a campaign result.

## Required reviewer decision

The reviewer must independently verify:

1. contract prospectivity and exact digest;
2. source cleanliness and source commit;
3. image and OpenFOAM identities;
4. deterministic generated-case hashes;
5. topology, patch roles, mesh quality, and XYZ extents at all levels;
6. QoI-history, pressure, conservation, and velocity-profile operators;
7. per-level gate calculations;
8. observed-order, GCI, and wall-geometry calculations;
9. every artifact hash and the package tree digest;
10. bounded steady incompressible laminar straight-pipe scope; and
11. absence of fixture, registry, validation, promotion, or release mutation.

Until a controlled reviewer accepts this exact digest, the state is
`verification-candidate-awaiting-independent-review`, `validated=false`, and
`promotionAuthorized=false`.
