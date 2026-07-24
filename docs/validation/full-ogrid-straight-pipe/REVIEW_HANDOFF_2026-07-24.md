# Full O-grid v1 controlled-review handoff

Status: **review-ready partial-failure evidence; no validation or promotion
authorized**

This handoff requests controlled independent review of the exact retained
failure package. The reviewer must be independent of implementation and
execution. The campaign failed frozen coarse solver-convergence gates, so the
reviewer cannot approve the bounded O-grid verification claim. The permissible
decision is whether the package faithfully and reproducibly supports the
recorded failure disposition.

## Exact review target

- Local campaign:
  `benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v1-r2`
- Source commit:
  `8ed349be9f6c2b513ec242c327e86f6a7bf70a5a`
- Prospective contract SHA-256:
  `4ba46eb81f442fdd3dceb2cff44d6820a51e26d9b4b7159391089430710a5b0d`
- Docker image:
  `flowlab/openfoam11-gmsh:2026-07-13`
- Immutable image ID:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Package manifest SHA-256:
  `e53de417585feabca4250399d3244db9d7b62fcae7194ddf4b4f5a0df5c4ae80`
- Artifact index SHA-256:
  `69fff1179e7639cde3496069e2e1b24e348597ff2dc4d685843e6e8be9191072`
- Package tree digest:
  `d80574cd2d2c10ba38d5d02f0ee49098193abffe6371056446d8bb9121c68f2f`
- Campaign result SHA-256:
  `d4d6ae97eb1299a66fb0572c4f56bda03b6d74002390760ac27c2c02c47242f9`
- Independent-review request SHA-256:
  `33c2b6461f27f4038aef6784fe641474ce52b803e1ad7c1745c6fbe0d71c332c`

The package directory and every contained file are read-only. Do not edit the
package or retained campaign in place.

## Recorded result

The coarse level passed deterministic generation, all mesh gates, all
pressure/flow/conservation/profile gates, and process exit. It failed:

- SIMPLE convergence declaration required: observed `false`;
- maximum final linear residual: observed `1.3859648e-07`, limit `1e-07`; and
- maximum absolute global continuity error: observed `1.3146633e-08`, limit
  `1e-08`.

Under the frozen mandatory-stop rule, medium and fine were not launched.
Observed order and GCI are therefore unqualified. The package records
`allCandidateGatesPassed=false`, `promotionAuthorized=false`, and no authority
to mutate a fixture, registry, validation state, or product claim.

## Required independent checks

1. Recompute the contract, campaign-result, package-manifest, artifact-index,
   and review-request hashes.
2. Recompute every artifact size and SHA-256 listed in
   `immutable-evidence-package/artifact-index.json`.
3. Recompute the package tree digest using the lexicographically sorted
   `name sha256 size` rows for the indexed artifacts plus
   `artifact-index.json`.
4. Verify the review request binds both the package-manifest hash and tree
   digest.
5. Verify the source commit and frozen transitive paths in the campaign result.
6. Verify the Docker tag, immutable image ID, detected Foundation OpenFOAM 11
   runtime, JobManager record, command, and exit code.
7. Review `blockMesh`, `checkMesh`, polyMesh boundary, solver VTK, patch
   metrics, residual history, evaluation, and frozen observation-operator
   calculations.
8. Confirm that `v1-r1` is separately retained as an evaluation-infrastructure
   failure and was not used as the definitive scientific result.
9. Confirm that the coarse failure required the prospective stop and that no
   medium/fine/GCI result is claimed.
10. Confirm no straight-pipe fixture, benchmark registry, validation pointer,
    UI claim, promotion flag, or release state changed.

## Review record

Create a new review record; do not modify retained evidence. It must include:

- reviewer identity and independence basis;
- review date;
- exact package-manifest SHA-256 and tree digest above;
- checks performed and independently recomputed values;
- findings with severity and disposition;
- one verdict:
  `failure-package-integrity-accepted`,
  `failure-package-integrity-rejected`, or
  `changes-required`;
- explicit statements that the numerical verification claim failed, GCI is
  unqualified, and promotion remains unauthorized; and
- `promotionAuthorized: false`.

Even an accepted integrity review cannot change the failed scientific result.
Any later hypothesis requires a new prospective contract, new source commit,
and new output directory; it cannot tune this frozen v1 campaign.
