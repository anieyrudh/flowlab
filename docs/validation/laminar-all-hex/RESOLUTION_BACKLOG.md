# Resolution backlog

Status: **v4 numerical/product gates accepted; FDA v2 full campaign authorized, scientific promotion still blocked**

## P0 — Execute the frozen FDA nozzle Re=500 v2 full campaign

The completed v1 evidence is under
`benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1`; the completed 2^3
preflight and frozen next-campaign contract are under
`benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight`.
The preflight selected short outlet, second-order scheme, and enhanced local
resolution. Next:

1. materialize the strict-all-hex coarse/medium/fine sequence exactly as frozen
   in `v2-full-campaign-contract.json`;
2. execute the three nominal grids plus the predeclared input-sensitivity and
   iterative-uncertainty cases;
3. apply the source-backed PIV-window operator and offset-free pressure
   differences without fitted offsets;
4. calculate pointwise GCI, experimental/input/iterative/grid/operator
   uncertainty, comparison error, and ASME V&V 20 validation uncertainty;
5. repeat flow, face-integration, force-object, traction, and negative gates;
6. retain the UI block unless every frozen scientific and product gate passes.

Do not add the published 100-250 Pa pressure offset as a post-hoc allowance to
v1. Its pressure-drop comparison passed, but wall-pressure profiles and axial
pointwise coverage did not.

## P0 alternative — Obtain another independent dataset

Preferred literature path:

1. request Akbari et al.'s repeat-level pressure/flow table, measured
   cross-section coordinates, fluid properties, calibration data, coverage
   factors, and covariance information;
2. hash and preserve the raw files unchanged;
3. build a separate finite-sidewall rectangular-duct model matching the
   experiment rather than mapping the data onto the v4 symmetry geometry;
4. calculate comparison error and validation uncertainty from experimental
   uncertainty, numerical GCI, and iterative uncertainty;
5. obtain independent review before changing the validated-campaign pointer to
   an accepted assessment.

If the raw packet cannot be obtained, commission a boundary-compatible
parallel-plate experiment with synchronized pressure, flow, velocity-gradient
or wall-shear measurements, repeat-level observations, calibration records,
and a declared uncertainty propagation method. Plot digitization remains
`exploratory-not-validation`.

## P1 — Final desktop release QA after the empirical gate passes

- rebuild the final assessment with `experimentalDatasetPinned=true` only from
  eligible evidence;
- rerun all six negative controls and four product contracts;
- update the digest-checked campaign pointer to that accepted report;
- run backend, frontend, and desktop computer-use QA on macOS and Windows;
- expose the runnable preset only when the final report explicitly sets
  `promotionAuthorized=true`.

Mobile remains out of scope.

## P2 — Evidence storage policy

- preserve compact manifests, reports, hashes, logs, issue records, and the
  accepted v4 evidence package;
- remove obsolete raw OpenFOAM trees only after a compact evidence archive and
  cleanup manifest exist;
- retain the current v4 raw primary and follow-up trees until empirical
  validation and final desktop QA no longer need field-level inspection.

## Completed

- v3 dynamic stopping diagnosed as a mixed-iterative-state GCI contaminant;
- common-1300 representative diagnostic passed 4/4 groups;
- v4 primary matrix passed 78/78 and 24/24 operating points;
- six-cell confirmation and cold/MPI reproducibility passed;
- negative and product controls passed after the audited product-only fix;
- desktop registry now reports only the empirical blocker and remains closed.
- FDA nozzle Re=500 v1 completed five strict-all-hex solves and a full
  uncertainty assessment; verification passed, but scientific promotion was
  correctly rejected by three frozen gates.
- FDA nozzle Re=500 v2 preflight completed all eight strict-all-hex matrix
  cases plus the prospective v1 observation reanalysis; it selected the short,
  second-order, enhanced contract and authorized the full v2 campaign, while
  correctly leaving scientific and desktop promotion blocked.
