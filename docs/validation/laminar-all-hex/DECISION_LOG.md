# Decision log

## DL-001 — Freeze existing gates

Existing affine, MMS, mass, residual, field, force, and face-traction limits
remain unchanged for the complete campaign. A failed limit creates an issue; it
does not authorize retuning.

## DL-002 — Parallel execution, ordered acceptance

Independent cells may run concurrently. Scientific acceptance remains ordered:
affine before non-affine MMS, and both before the physical-envelope claim.

## DL-003 — Separate physical and diagnostic factors

The physical envelope varies Reynolds number, direction, channel aspect, cell
aspect, and resolution. Solver/BC experiments belong to diagnostic campaigns
and cannot be mixed into promotion evidence.

## DL-004 — Predeclare nested claims

Only the five named nested claims may be accepted. Post-hoc islands or holes in
the parameter matrix cannot be promoted.

## DL-005 — Preserve current desktop scope

No mobile work and no general production-CFD claim are authorized by this
campaign.

## DL-006 — Preserve the rejected v2 verdict

Six residual failures were independently confirmed. The successful
1,250-iteration diagnostic identifies a likely immediate cause but cannot
retroactively change v2. Remediation requires `laminar-all-hex-v3`.

## DL-007 — Block the older desktop promotion path

The earlier single-point evidence is no longer sufficient to mint a validated
preset. The registry now requires the complete campaign promotion report; the
current API returns 409 and the UI hides the action.

## DL-008 — Do not substitute literature curves for experimental data

The Akbari study is the best candidate found, but absent raw pointwise data and
geometry-compatible modeling it remains a lead, not validation evidence.

## DL-009 — Preserve the MPI equivalence miss

The 2-rank derived pressure-error norm exceeds the pre-run equivalence ratio by
0.1677 ppm, despite physical force QoIs matching to much tighter levels. The
gate is not redefined after observation; v3 must predeclare a physically scaled
field-equivalence metric.

## DL-010 — Make every promotion blocker reachable in the desktop UI

The validation panel must scroll and list every failed campaign gate. Hiding
the run action is necessary but not sufficient; users must also be able to see
why promotion is blocked.

## DL-011 — Preserve the rejected v3 aggregate verdict

V3 accepted every individual physical solve but failed 18/24 group-level
order-consistency gates. The mixed stopping state is a supported cause, but the
completed v3 report is not reinterpreted or promoted.

## DL-012 — Use a common 1300 minimum in v4

The representative common-floor diagnostic passed unchanged gates. V4
therefore predeclares 1300 as the earliest stop iteration, retains the same
25-iteration joint residual window, and keeps the 2000 hard cap. This is a new
campaign ID, not a post-hoc v3 threshold change.

## DL-013 — Separate product evidence selection from scientific source

The desktop registry selects a final assessment through a digest-checked data
pointer. Updating which immutable report is current must not require changing
solver or campaign code. The one post-campaign product-source change is
explicitly hashed and reconstructed in amendment r2.

## DL-014 — Keep the desktop closed on the sole empirical blocker

V4 passes the numerical, confirmation, reproducibility, control, and product
gates. It is still not empirically validated. The registry may display the v4
truth, but the API remains HTTP 409 and the run action remains hidden until a
compatible experimental packet passes the final gate. Mobile remains out of
scope.
