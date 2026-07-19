# Final assessment

Status: **numerically accepted; FDA empirical campaign completed but not accepted; desktop promotion blocked**

Campaign `laminar-all-hex-v4` completed all 78 primary scientific cells and all
conditional numerical/product follow-ups. The authoritative result is
`2026-07-16-laminar-all-hex-v4-followups/final-assessment-r2/final-assessment.json`.
Every final gate is true except `experimentalDatasetPinned`.

## Gate result

| Gate | Result | Evidence |
|---|---|---|
| Every scientific cell accounted for | Pass | 78/78; no infrastructure gaps |
| Affine 12/24/48 grid invariance | Pass | all existing affine gates |
| Non-affine boundary-compatible MMS | Pass | all existing order and GCI gates |
| Full 72-cell physical envelope | Pass | 24/24 three-grid operating points |
| Common convergence state | Pass | 72/72 stop at iteration 1300; zero hard-cap cells |
| Independent sensitive-cell confirmation | Pass | 6/6 accepted; tracked signatures exactly equal |
| Cold reproducibility | Pass | two repeats exactly match source and one another |
| MPI reproducibility | Pass | 2/4 ranks pass scientific, primary-QoI, and analytic-scale field gates |
| Negative controls | Pass | 6/6 rejected as expected |
| Desktop product contracts | Pass | 4/4, with current incomplete regime still blocked |
| Product evaluator amendment audit | Pass | one product-only source drift recorded and reconstructed |
| Independent experimental validation | **Fail** | FDA nozzle Re=500 v1 is pinned and complete, but fails GCI, axial-velocity, and pressure validation gates |

## Numerical result

V3 accepted each individual solve but rejected the aggregate physical lane:
18 of 24 three-grid groups exceeded the frozen `0.75` velocity-order-spread
limit because grid levels stopped at different iterative states. V4 preserved
all scientific tolerances and imposed the predeclared common 1300-iteration
minimum, followed by the same 25-iteration sustained residual rule and a
2000-iteration hard cap.

V4 results:

- velocity order spread: `0.30580` to `0.46459`;
- minimum observed velocity order: `2.01122`;
- worst fine-grid velocity GCI relative to analytic field norm: `5.68417e-4`;
- largest wall-viscous-force relative error: `1.77867e-4`;
- largest face-viscous-traction relative error: `1.82269e-4`;
- largest OpenFOAM-force versus direct-integration absolute difference:
  `1.62939e-15`;
- largest mass relative imbalance: `7.28204e-13`.

All 72 physical cells pass mesh, mass, residual, field, direct face
integration, analytic pressure force, analytic viscous traction, and momentum
balance gates. Forward/reverse behavior remains symmetric to numerical
precision.

## Reproducibility result

The two cold repeats are exact numeric-signature matches. Both MPI runs pass
decomposition, parallel mesh, solve, reconstruction, direct face audit, and
every frozen physical gate. The largest primary force-QoI serial/parallel
relative difference is `1.68273e-11`; the largest analytic-scale field
difference is `1.77157e-11`, below the `1e-10` limit. Campaign source drift was
empty when confirmation and reproducibility executed.

## Product amendment

The first v4 product-control run exposed a version-locked promotion evaluator.
The fix accepts the v4 identity/schema and reads a digest-checked evidence
pointer. This changed only `server/flowlab/validated_benchmark.py`, after the
scientific and reproducibility runs. Frozen/current hashes and a reconstruction
patch are recorded in `product-evaluator-amendment-r2.json`. No solver input,
scientific threshold, aggregation result, or numerical artifact changed.

The final control pass proves that a synthetically complete v4 report would be
accepted, while the actual v4 report remains blocked.

## External validation result

The FDA sudden-expansion nozzle Re=500 campaign is the first completed
independent experimental attempt. It used pinned official data, strict all-hex
grids of 6,720/53,760/430,080 cells, fixed inlet velocity, fixed outlet
pressure, direct face integration, and explicit experimental/input/iterative/
grid uncertainty. Verification passed, including flow closure and force-object
reconciliation, but promotion did not:

- mandatory pointwise GCI qualified 340/360 points;
- primary axial-profile V&V 20 pass fraction was 79.2% versus 90% required;
- centreline axial velocity passed 13/15 points;
- wall pressure passed 2/17 points, although offset-invariant pressure drop
  passed;
- the published 100-250 Pa Re=500 pressure normalization systematic was
  recorded but not added post-hoc to the frozen uncertainty budget.

The authoritative assessment is
`benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/assessment.json`.
The older Akbari rectangular-microchannel route remains an alternative, but its
repeat-level uncertainty packet is still unavailable and its geometry does not
match the current symmetry model.

## Desktop decision

- Candidate status: `campaign-promotion-blocked`
- Product-visible blocker: `experimentalDatasetPinned` remains false because no accepted empirical assessment is eligible for the validated pointer
- Runnable validated preset: unavailable; backend returns HTTP 409
- UI run action: hidden
- UI gate panel: points to the current digest-checked v4 assessment
- Generic generated cases: experimental
- Mobile: unchanged and out of scope
- General production-CFD or native-performance claim: prohibited

The next work is an FDA v2 campaign with prospectively defined offset-invariant
pressure metrics, a refined nested grid sequence, a CFD-to-PIV spatial-
averaging operator, and downstream-domain/spatial-scheme sensitivity. The
desktop pointer remains unchanged until that campaign passes every gate.
