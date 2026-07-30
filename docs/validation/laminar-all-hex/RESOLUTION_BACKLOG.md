# Resolution backlog

Status: **v4 numerical/product gates accepted; FDA v2 completed and blocked; successor full campaign not authorized**

## P0 — Complete the HF infrastructure qualification

The v2 campaign completed through the retained r3 fine-grid recovery, but its
assessment is `validated-blocked`. Pressure has been formally demoted to a
mandatory nonpromotional diagnostic, narrowing the successor claim to bounded
axial-velocity-field agreement. Do not rerun v2 or tune its gates.

The first frozen 1% V3 mesh preflight failed before mesh construction because
the preparer omitted `system/controlDict`. Its retained evidence and compact
assessment are immutable. The separately frozen r2 recovery exercised the
corrected preparer in a new directory and passed every mesh and geometry gate.
The observed domain-measure error sequence was approximately 2.5505%, 0.6413%,
and 0.1606%, with exact 8x refinement and strict all-hex meshes. The successor
velocity-verification design is now frozen and its offline validator passes all
22 checks. Two r2 Hugging Face Jobs confirmed AMD64, then stopped before any
artifact or solver because the connector credential could neither create the
private dataset nor commit to main. The local `fluidmech` credential proved it
can do both. The retained r2 Space image separately failed its rootless build
on UID/GID 98765. A prospectively frozen R3 image recovery using UID/GID 1000
now passes local identity, private Space build/runtime, immutable commit, and
commit-prefixed registry-digest gates. This qualifies only the image path; HF
Jobs, volume, artifact, and coarse-pilot qualification remains blocked. Next:

1. reconnect the Hugging Face Jobs connector to the locally verified,
   write-capable credential; its present credential can submit Jobs but cannot
   create or commit main-branch private evidence;
2. bind a new qualification contract to the passing R3 image recovery: private
   Space commit `4c8572ab...`, commit-prefixed tag `cpu-4c8572a`, and registry
   digest `sha256:87467233...`; do not reuse the failed frozen r2 image identity;
3. rerun the architecture and revision-pinned volume probes under new unique
   Job IDs, then run the isolated serial coarse pilot on `cpu-upgrade` and
   `cpu-xl`, recover
   and independently rehash every artifact, and compare the predeclared field
   invariants within `1e-10`;
4. independently review the qualification and all six intended cases, 18
   station functionals, centreline requirements, uncertainty rules,
   convergence thresholds, and phase stop/go conditions;
5. freeze a separate execution-authorizing contract only if every
   infrastructure gate and the review pass, with a new absent raw output
   directory and explicit user authorization;
6. run phase 1 fine-grid stationarity/cold-repeat diagnostics first, and stop
   unless every residual, QoI-drift, repeat, telemetry, and terminal-state gate
   passes;
7. run phase 2 only after separate stop/go review, and freeze a later full-
   campaign contract only after the numerical evidence
   supports it.

The old 90% point-pass figure remains part of the immutable v1/v2 history. It
was a conservative FlowLab aggregation policy, not an FDA or ASME universal
acceptance value. A successor should use predeclared critical-QoI coverage and
correlation-aware station/region metrics; any aggregate threshold needs
independent calibration, not post-hoc tuning.

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

## P1 — Final validated-regime desktop QA after the empirical gate passes

This P1 governs the desktop's validated-regime pointer and runnable preset; it
is not the external software-distribution gate. A bounded external release is
governed separately by `desktop/electron/release-contract.json`: it must retain
experimental, nonpromotional language and pass its own controlled-review,
authorization, signing, notarization, and clean-machine installer gates.
Neither path may set `experimentalDatasetPinned=true` or
`promotionAuthorized=true` without an eligible accepted assessment.

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
- FDA nozzle Re=500 v2 completed through the r3 fine-grid recovery at time 800;
  assessment remained correctly blocked at 246/315 axial, 13/15 centreline,
  8/16 wall-adjacent pressure, and 316/347 mandatory GCI.
- the read-only successor preflight reproduced the retained result and blocked
  new full CFD because pressure covariance is rank-limited and a successor grid
  family with bounded geometry discretization has not passed preflight.
- pressure was formally demoted to nonpromotional and the claim narrowed to
  axial velocity; the first 1% mesh-only attempt then stopped fail-closed on a
  missing `controlDict` before any mesh or scientific gate was evaluated.
- the r2 mesh-only infrastructure recovery passed all frozen gates with exact
  all-hex refinement and 0.1606% fine-grid geometry error; it authorizes only
  design of the next numerical-verification campaign.
- the velocity-focused numerical-verification design passed its offline
  validator with six intended cases and all execution/promotion flags false;
  independent review and a separate execution contract are now required.
