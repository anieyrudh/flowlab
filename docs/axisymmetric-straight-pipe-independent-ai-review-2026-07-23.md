# Axisymmetric straight-pipe candidate: independent AI technical review

Date: 2026-07-23

Review type: independent AI technical review, read-only

Promotion authority: **none**

This review examined source commit
`4fa840bb852be23ccd9445c8a3f5283ef335ee8f` and the retained package at
`benchmarks/cases/straight-pipe/campaigns/2026-07-23-axisymmetric-product-v1/immutable-evidence-package/`.
It is not the controlled independent reviewer approval required by the frozen
fixture contract.

## Verdict

| Surface | Verdict |
| --- | --- |
| Byte/hash integrity | Pass |
| Archive-to-retained-source correspondence | Pass |
| Numerical recomputation and candidate thresholds | Pass |
| Product execution-path and runtime mesh evidence | Pass |
| Frozen promotion-contract compliance | Hold |
| Fixture or registry mutation | Not authorized |

The package is a strong local verification candidate, but it is not promotable
under the frozen contract.

## Blocking findings

### 1. Pressure-drop observation-operator conflict

The frozen fixture defines pressure drop as area-averaged inlet static pressure
minus area-averaged outlet static pressure. The periodic campaign evaluator
uses the prospectively recorded mean-velocity forcing gradient multiplied by
length and density.

The periodic operator is technically appropriate for this formulation, but it
does not literally satisfy the fixture's canonical wording. The retained
inlet/outlet patch-average `p` values are equal under periodic forcing, while
the reported pressure drop comes from the forcing gradient. This cannot be
reinterpreted after observing the result.

Required disposition:

- either run a new prospectively frozen campaign that uses the existing
  patch-average operator; or
- create a separately versioned periodic-gradient verification contract,
  review and freeze it before execution, then run a new campaign.

The retained campaign and package must remain unchanged.

### 2. Controlled independent approval is absent

The package explicitly records:

- `promotionAuthorized=false`;
- `reviewStatus=pending-controlled-independent-review`;
- `validated=false`.

The review request is not an approval record. Controlled review must identify
the reviewer, timestamp, exact package digest, claim boundary, findings, and
approval decision.

### 3. Point-field surface sampling semantics

The reviewed working-tree snapshot displayed the ray-hit solver XYZ but sampled
the nearest triangle vertex for point fields. That made the displayed location
and value inconsistent.

The product implementation was corrected after this review to barycentrically
interpolate point-field values over the hit exterior triangle. Cell fields
continue to sample the owning cell. The UI identifies an interpolated point
sample as `field @ surface`, while preserving the clicked solver-space XYZ.
This remediation requires the normal automated and packaged-app QA; it does not
alter the immutable scientific package.

## Independently reproduced evidence

- Package tree digest:
  `d57fc73f4fc2727c8cdb6fea1ff2aba32fed862e0216e758b66f9eb8726be056`
- Package manifest SHA-256:
  `3a86bd3c60597af195078ab162d90dbd2d779ecdc7c8b52ddc5a1b53f0e10b62`
- Artifact index SHA-256:
  `bbf49d6438368172c337efe612d53e2004e8c23a2390fdfcccea06a761746644`
- Container identity:
  `sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
- Pressure drops:
  `0.457474056`, `0.467655816`, `0.470355072 Pa`
- Analytical reference:
  `0.471570201753764 Pa`
- Reynolds number:
  `1061.032953945969`
- Observed order:
  `1.9153532495650476`
- Fine-grid GCI:
  `0.25877682173974026%`
- Fine pressure error:
  `0.25767738276188347%`
- Fine signed mass flows:
  `-0.01000000008` and `+0.01000000008 kg/s`
- Fine relative mass imbalance:
  `0`

Every artifact-index digest, size, and archive member matched the retained
campaign source. The three meshes had 64, 256, and 1024 cells; all were
nonzero-volume three-direction wedges and reported `Mesh OK`.

## Claim boundary

The candidate supports only one steady, incompressible, Newtonian, laminar,
straight circular pipe at Reynolds number approximately 1061, using the product
adapter and `JobManager`, a 5-degree axisymmetric wedge, OpenFOAM Foundation 11
in the recorded emulated `amd64` Docker image, periodic mean-velocity forcing,
and the frozen three axial/radial refinement levels.

It does not establish empirical validation, Venturi accuracy,
non-axisymmetric flow, arbitrary geometry, turbulence, transients, multiphase
or compressible accuracy, native performance, general production CFD, desktop
release readiness, or external promotion.

## Decision

Keep the formal fixture and validated registry unchanged. Preserve the package
as immutable candidate evidence and resolve the pressure-drop operator through
a new prospective contract decision before any rerun or status change.
