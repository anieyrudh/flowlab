# FDA nozzle Re=500 independent benchmark

This campaign tests the bounded claim:

> FlowLab's laminar OpenFOAM execution has passed an independent experimental CFD benchmark.

The claim is fail-closed. It is authorized only when every mandatory gate in the
predeclared machine-readable contract passes. The desktop product must not expose
the claim before `promotionAuthorized` is `true` in the final assessment.

## Frozen v1 configuration

- FDA sudden-expansion nozzle, throat Reynolds number 500.
- Density 1056 kg/m3 and dynamic viscosity 0.0035 Pa s.
- Exact nominal flow 5.20624e-6 m3/s.
- Fixed parabolic inlet velocity, normalized using the discrete inlet-face
  quadrature so every mesh receives exactly the same flow.
- Inlet and wall `fixedFluxPressure`; outlet fixed zero-gauge pressure.
- Nested O-grid block meshes with linear refinement 1/2/4. Every cell must be a
  hexahedron and `checkMesh -allTopology -allGeometry` must pass.
- OpenFOAM 11 runs in the pinned immutable FlowLab container recorded in the
  campaign manifest.

## Predeclared comparisons

Promotion-grade comparisons are axial velocity profiles, centreline axial
velocity, wall pressure relative to the expansion, and pressure drop. Boundary
flow conservation and OpenFOAM's force function object versus direct face
integration are verification gates.

Radial velocity and legacy wall-shear/viscous-traction comparisons are retained
as mandatory diagnostics but are nonpromotional. The primary interlaboratory
study identifies reliability limitations for these measurements, and the later
wall-shear uncertainty publication does not provide pointwise machine-readable
uncertainty in the official archive.

For each eligible promotion point, the campaign records experimental, input,
iterative and grid uncertainty. The comparison error is `E = S - D`, and the
ASME V&V 20 validation uncertainty is

`U_val = sqrt(U_exp^2 + U_input^2 + U_iter^2 + U_grid^2)`.

A point passes when `|E| <= U_val`. Three-grid GCI uses a 1.25 safety factor;
nonmonotonic or nonconvergent point sequences are explicitly unqualified.

The companion CFD interlaboratory paper reports a 100-250 Pa Re=500 pressure
offset attributed partly to the x=0 normalization measurement and use of
non-differential pressure transducers. The frozen v1 contract does not add a
post-hoc allowance for this published systematic; `FDA-0003` records it as an
open qualification that must be handled prospectively in any v2 campaign.

## Current evidence status

The immutable v1 campaign completed five strict-all-hex solves. Its numerical
verification gates passed, but the scientific promotion gate failed and the
desktop claim remains blocked. A prospective source-backed reanalysis did not
change that verdict: PIV-window V&V 20 coverage was 246/315 profile points and
13/15 centreline points; offset-free wall pressure coverage was 7/16 adjacent
differences and 2/4 named differences.

The v2 preflight then ran a frozen 2^3 matrix over outlet length, spatial
scheme, and local resolution. All eight numerical gates passed. Every matched
short-versus-extended pair met the 0.5% throat-mean velocity and 1 Pa pressure
thresholds, so the frozen full-campaign selection is:

- 0.120 m short outlet;
- formal second-order spatial scheme;
- enhanced local resolution;
- strict all-hex coarse/medium/fine sequence with linear refinement ratio 2.

The 0.720 m enhanced cases exposed a strong iterative-cost interaction: every
pressure solve reached the 1,000-iteration cap and the two runs took about
25,500 seconds each, while near-field responses were effectively unchanged.
This risk is retained in the issue ledger and is avoided by the selected short
outlet. The preflight authorizes preparing and running the full v2 campaign; it
does not authorize the scientific claim or desktop promotion.

## Evidence paths

- Frozen contract: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/campaign-contract.json`
- Source archive and parsed data: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/experiment/`
- Cases and raw solver logs: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/cases/` and `logs/`
- Direct face integration: per-case CSV files under `results/`
- Issue ledger: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/issues.jsonl`
- Final decision: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1/assessment.json` and `REPORT.md`
- Frozen v2 preflight contract: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight/preflight-contract.json`
- V2 matrix assessment and issue ledger: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight/preflight-assessment.json`, `REPORT.md`, and `issues.jsonl`
- Frozen full-campaign contract: `benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight/v2-full-campaign-contract.json`

## Authoritative sources

- FDA benchmark portal: <https://cdrh-rst.fda.gov/benchmark-dataset-validating-computational-fluid-dynamic-cfd-simulation-blood-flow-through>
- FDA/OSEL benchmark repository, pinned commit and archive hash in the contract: <https://github.com/OSEL-DAM/CFD-and-Blood-Damage-Benchmarks>
- Hariharan et al., DOI 10.1115/1.4003440.
- Raben et al., DOI 10.1007/s13239-015-0251-9.
