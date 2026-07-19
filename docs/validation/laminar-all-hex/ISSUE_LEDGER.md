# Issue ledger

The canonical ledger is append-only `issues.jsonl` in the campaign directory.
The schema is `benchmarks/laminar_all_hex_issue.schema.json`.

## Initial issues

### CAM-0001 — Experimental dataset missing

- Kind: known limitation
- Severity: P1
- Status: open
- Blocks: empirically validated laminar envelope
- Does not invalidate: existing numerical and analytic verification
- Next action: select and provenance-pin a compatible dataset with uncertainty

### CAM-0002 — Native performance evidence unavailable

- Kind: known limitation
- Severity: P2
- Status: accepted limitation
- Blocks: native performance claims
- Cause: AMD64 image under emulation and concurrent scientific execution
- Next action: exclusive native-AMD64 timing campaign only if requested

## Recording rule

Every issue must name affected gates and claims, raw evidence and hashes,
suspected and falsified causes, interacting factors, conflicts, a minimal
reproducer, blocked downstream work, and the next diagnostic. A summary without
those fields is not sufficient campaign evidence.

## Executed issue clusters

### HAR-0001 — Strict-JSON and retry-directory harness defect

- Observed in abandoned run r1
- Cause: non-finite failure sentinels were serialized as invalid JSON, and the
  identical retry reused a non-empty output directory
- Resolution: non-finite values become JSON `null` at persistence; attempts use
  isolated directories
- Scientific impact: none; r1 was not used as evidence

### HAR-0002 — Affine instrumentation leaked into other lanes

- Observed in abandoned run r2
- Cause: the diagnostic `libincompressibleFluid` was injected globally
- Resolution: the library is mounted only for affine cells
- Scientific impact: none; r2 was stopped and excluded

### SCI-0001 through SCI-0006 — Nonlinear residual failures

- Cells: the six `Re=66.7`, `L/H=1` coarse/medium forward/reverse cases listed
  in `factorial-analysis.json`
- Confirmed: yes, with identical failure signatures and numeric results
- Falsified causes: transient infrastructure, scheduler contention, random
  variation, flow direction, force integration, analytic traction, mesh
  topology, mass conservation, and linear-solver failure
- Supported cause: insufficient fixed nonlinear iteration budget
- Evidence: all six pass unchanged gates at 1,250 iterations; worst sustained
  crossing is iteration 1,215
- Status: cause understood; unresolved in completed v2

### REP-0001 — Two-rank derived pressure-error equivalence miss

- Observed: relative change `1.1676989e-6`; limit `1e-6`
- Absolute change: approximately `4.41e-12`
- Other evidence: both MPI runs pass every physical gate; pressure and viscous
  forces agree with serial at `6.4e-12` relative or better
- Interference: decomposition reduction order × normalization by a small error
  norm
- Status: open for v3 metric design; v2 reproducibility gate rejected

### CAM-0001 — Experimental dataset missing

- Five independent research lanes completed
- Best lead: Akbari et al. 2009
- Status: open; raw data acquisition or a new experiment is required

### UI-0001 — Older evidence bypassed campaign promotion

- Cause: the registry previously used only three earlier accepted reports
- Resolution: registry now consumes the full final campaign assessment and
  fails closed
- Verification: backend HTTP 409, hidden UI action, 4/4 product contracts pass
- Status: resolved

### UI-0002 — Campaign gate card clipped in desktop sidebar

- Cause: the fixed-height Reference Cases panel inherited `overflow: hidden`
- Impact: campaign blockers existed in the DOM but were not reachable in the
  rendered desktop viewport
- Resolution: the panel now scrolls vertically and renders every blocking
  reason, not only the first
- Verification: rendered-browser inspection shows all three blocker lines,
  no runnable validated action, and no console warnings or errors
- Status: resolved

### SCI-0007 — V3 mixed-stop iterative states contaminated observed order

- Observed: all 72 v3 physical cells passed individually, but 18/24 three-grid
  groups exceeded the frozen `0.75` velocity-order-spread gate
- Interaction: grid resolution × convergence stop iteration
- Falsified causes: non-monotone field error, force/traction failure, mesh
  failure, and direction asymmetry
- Diagnostic: four representative groups rerun at a common 1300 iterations
  passed with order spread `0.3058`–`0.4640`
- Resolution: v4 predeclared a common 1300 minimum and retained the sustained
  residual/hard-cap rule
- Verification: v4 passes 24/24 groups with spread `0.30580`–`0.46459`
- Status: resolved in v4; v3 verdict remains rejected

### INF-0003 — Transient write failure during common-floor diagnostic

- Observed: one fine diagnostic cell failed while OpenFOAM wrote
  `postProcessing/residuals/0/residuals.dat` during disk pressure
- Treatment: no scientific input changed; obsolete r2 raw trees were compacted
  and the exact cell was rerun in a fresh directory
- Verification: identical repair accepted, all 12 combined cells accepted, and
  all four diagnostic GCI groups passed
- Status: resolved as transient storage I/O; original failure retained

### PRO-0001 — Product evaluator rejected v4 report identity

- Observed: negative controls passed, but the first positive product path
  rejected a synthetically complete v4 report as an invalid identity
- Cause: accepted campaign IDs/schemas and the selected report path were
  version-locked in application source
- Resolution: accept v4/v2-final-assessment identity and use a digest-checked
  data pointer for the current report
- Audit: original/current hashes and reconstruction patch are retained in
  `product-evaluator-amendment-r2.json`
- Scientific impact: none; scientific inputs, tolerances, and results unchanged
- Verification: 10 focused tests plus final controls `r3` pass; actual desktop
  gate reports only `experimentalDatasetPinned`
- Status: resolved

### CAM-0001 — Experimental dataset remains the only final blocker

- Numerical, confirmation, reproducibility, negative-control, and product
  gates: accepted in v4
- Experimental gate: rejected
- Blocks: desktop regime execution and empirical-validation claim
- Next action: acquire the complete Akbari raw/UQ packet and model its finite
  geometry, or commission a boundary-compatible experiment
- Status: open
