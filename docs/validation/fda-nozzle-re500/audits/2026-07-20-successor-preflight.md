# FDA Re=500 successor preflight assessment

Status: **blocked before new full CFD**

This is a new read-only audit of the immutable v2 r3 evidence. It does not
change the r3 assessment, weaken a frozen gate, authorize product promotion, or
write into a retained campaign. The machine-readable record is
`2026-07-20-successor-preflight-r3.json`. Earlier candidate JSON records are
retained but superseded: the first omitted the audit-source hash, and r2 used an
overly strong geometry-invariance interpretation. R3 distinguishes normal
curved-boundary geometry convergence from solution discretization; no retained
campaign finding or gate changed.

## Retained outcome reproduced

- r3 status: `validated-blocked`;
- promotion: false;
- axial profiles: 246/315 eligible, or 246/291 among GCI-qualified points;
- centreline velocity: 13/15 eligible, or 13/13 among GCI-qualified points;
- wall-adjacent pressure: 8/16 eligible, or 8/12 among GCI-qualified points;
- mandatory GCI: 316/347 qualified;
- failed gates: axial velocity, pressure validation, and complete three-grid
  GCI.

## Interpretation 1 — pressure reference is underidentified

Likelihood: **high for the pressure gate; low for velocity**.

The official packet has five dataset codes. The frozen eligibility rule uses
243, 468, and 763; code 297 has deleted pressure plots, and code 999 is the
published-excluded outlying series. Three eligible traces support sixteen
adjacent-tap differences. After centring, the observed trace matrix rank is two,
which is also the maximum possible covariance rank with three traces. Twelve of
48 adjacent trial differences are exactly zero at the retained precision.

This is directly verifiable from the pinned experiment and pressure-validation
records. It does not prove the pressure measurements are wrong. It proves they
cannot identify a full 16-dimensional covariance and do not contain the
laboratory mapping, tap-specific bias/calibration model, or as-built metrology
needed for promotion-grade pressure attribution.

## Interpretation 2 — GCI combines geometry and solution discretization

Likelihood: **high for the GCI failures; moderate for the validation errors**.

The grid sequence refines cell counts exactly by 8x:

| Grid | Cells | Inlet faces | Domain volume, m3 |
|---|---:|---:|---:|
| Coarse | 20,436 | 12 | 1.9619510024108e-5 |
| Medium | 163,488 | 48 | 2.123600462363808e-5 |
| Fine | 1,307,904 | 192 | 2.165204254992868e-5 |

The analytic nominal volume is 2.17917965852834e-5 m3. The coarse, medium, and
fine volume errors are approximately -9.97%, -2.55%, and -0.64%, respectively.
This monotonic convergence is normal for planar finite-volume faces
approximating a curved boundary; it does not prove the grids solve different
continuum problems. It does show that classical GCI on this family combines
geometry and solution discretization, with a large geometric contribution on
the coarse level. That is directly verifiable from the nominal geometry and
retained `checkMesh` logs, and is a plausible source of non-asymptotic GCI
behavior rather than proof that the retained GCI is invalid.

## Interpretation 3 — model, boundary, or as-built mismatch

Likelihood: **moderate**.

Downstream axial failures cluster at x = 0.008, 0.024, and 0.080 m, while each
station's normalized RMSE remains below 10%. The v2 PIV-window operator and
outlet-length preflight did not remove the discrepancy. That makes a remaining
inlet, nominal-versus-as-built geometry, tap, material, boundary-condition, or
solver-form difference plausible. It is not yet attributable because the
reference and grid-family blockers confound the comparison. It becomes
verifiable only through prospectively declared sensitivities and an independent
solver after the first two blockers are resolved.

## Fine pressure iterations

The fine run reached its 1,000-iteration pressure cap in 62/100 solves and did
not meet the configured final linear tolerance. This is a real performance and
stationarity risk. It is less likely to be the primary scientific cause because
the retained iterative uncertainty is at most 0.001349 in absolute terms and
0.213 of validation uncertainty. A stationary cloned-fine diagnostic remains
required before another full campaign, but cannot repair the reference or
grid-family defects.

## Why 90% is not carried forward

The 90% rule was a conservative FlowLab aggregation choice: it prevented a few
good regions from hiding widespread point failures while allowing limited
isolated exceptions. That rationale is understandable for a bounded internal
campaign, but 90% is not a universal FDA or ASME V&V 20 acceptance threshold.
Dense profile samples are also spatially correlated, so treating every point as
an independent vote overstates the apparent sample size. The successor design
retains pointwise comparison error and validation uncertainty, requires every
critical QoI to have resolved numerical uncertainty, and adds predeclared
station/region functionals, correlation-aware global error, maximum
standardized exceedance, and contiguous-exceedance diagnostics. Any new
aggregate cut-off must be calibrated independently before results are seen.

## Next authorized work

1. Acquire the missing pressure provenance and covariance evidence, or declare
   pressure nonpromotional.
2. Predeclare a geometry-discretization tolerance, then build and mesh-check a
   grid family that bounds or separately propagates that contribution.
3. Only after those pass, run a stationary cloned-fine discriminator.
4. Then run an independent solver and bounded as-built/inlet/tap sensitivities.
5. Freeze a new full-campaign contract only if the earlier stages support it.

No new full CFD solve is currently authorized by this diagnostic contract.
