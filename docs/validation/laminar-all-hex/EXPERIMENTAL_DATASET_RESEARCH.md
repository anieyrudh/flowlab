# Experimental dataset research

Research date: 2026-07-16

## Research contract

The target is a public, independent experiment that can validate FlowLab's
steady incompressible Newtonian all-hex open-boundary campaign. The required
comparison quantities are pressure drop, volume flow, velocity profile, and—if
available—wall shear, with declared measurement uncertainty and enough geometry
and boundary information to reproduce the experiment. The useful Reynolds
range is height-based `Re_H = 4.17`, `16.67`, and `66.7`. Mobile and performance
evidence are out of scope.

Subagent fan-out was unavailable for this run, so five non-overlapping research
lanes were executed sequentially: Akbari author/publication records, public data
repositories, pressure/PIV primary experiments, classic duct measurements, and
official V&V/metrology guidance. Source overlap was zero across the five lanes.

## Executive finding

No source found is eligible to close `CAM-0001` as-is. The closest match is
Akbari, Sinton, and Bahrami's rectangular-microchannel experiment: it covers
Reynolds numbers 1–35, includes five aspect ratios, repeats each pressure
measurement three times, and reports propagated friction-factor uncertainty
below 10%. However, its numeric results are published as plots rather than a
machine-readable table, its channels have finite sidewalls and non-ideal PDMS
cross-sections, and it does not measure wall traction. Plot digitization would
add an unreported extraction uncertainty and is therefore exploratory evidence,
not an acceptable validation dataset.

The current empirical gate remains false. The fastest credible path is to
obtain the authors' raw Akbari pressure/flow table and uncertainty metadata, then
model the measured finite-aspect geometry as a separate campaign. If the raw
data cannot be obtained, commission a small parallel-plate experiment with a
published uncertainty budget rather than treating an analytic correlation as
experimental validation.

## Five-lane source map

| Lane | Primary source | Contribution | Compatibility decision |
|---|---|---|---|
| Akbari records | [Akbari, Sinton & Bahrami, Journal of Fluids Engineering, DOI 10.1115/1.3077143](https://doi.org/10.1115/1.3077143) and [author PDF](https://www.sfu.ca/~mbahrami/pdf/2009/M.%20Akbari%2C%20D.%20Sinton%2C%20M.%20Bahrami%20-%20Pressure%20drop%20in%20rectangular%20microchannels%20as%20compared%20to%20theory%20based%20on%20arbitrary%20cross-section.pdf) | Water, rectangular channels, `Re=1–35`, pressure/flow, five aspect ratios, propagated uncertainty below 10% | Best candidate, but raw numeric measurements are unavailable and geometry is not the current infinite-span plane-Poiseuille contract |
| Public repositories | [Bohling et al. Dryad dataset, DOI 10.5061/dryad.xksn02vwk](https://doi.org/10.5061/dryad.xksn02vwk) and [Zenodo channel PIV/pressure-estimation dataset](https://zenodo.org/records/6473075) | Machine-readable channel-flow measurements | Located records have incompatible obstacles or regimes and lack the joint pressure, traction, and uncertainty packet |
| Pressure/PIV experiments | [Qu, Mudawar, Lee & Wereley, DOI 10.1115/1.2159002](https://doi.org/10.1115/1.2159002), [Zheng & Silber-Li, DOI 10.1007/s00348-007-0454-4](https://doi.org/10.1007/s00348-007-0454-4), and [Meinhart et al. author PDF](https://web.stanford.edu/group/microfluidics/Publications/ParticleTracking_Diagnostics/Meinhart%20Micro%20PIV%20Measurements%20ExperFluids.pdf) | Rectangular-channel pressure drop or velocity profiles; the low-Re PIV studies report profile agreement and spatial resolution | No raw force-compatible packet was located; Qu is outside the campaign regime and the PIV-only papers do not provide the required pressure/traction observables |
| Classic duct measurements | [Ahrens & Zahner, DOI 10.1016/0009-2509(68)85006-7](https://doi.org/10.1016/0009-2509(68)85006-7), [Hartnett, Koh & McComas, DOI 10.1115/1.3684299](https://doi.org/10.1115/1.3684299), and [NASA TN D-3074](https://ntrs.nasa.gov/api/citations/19650024645/downloads/19650024645.pdf) | Direct links among pressure loss, velocity profiles, and wall gradients in rectangular ducts | The located evidence is plot/PDF based and lacks machine-readable pointwise observations with a complete uncertainty packet |
| Validation and uncertainty method | [ASME V&V 20](https://www.asme.org/codes-standards/find-codes-standards/standard-for-verification-and-validation-in-computational-fluid-dynamics-and-heat-transfer), [NIST TN 1297](https://www.nist.gov/pml/nist-technical-note-1297), and [JCGM 100:2008](https://www.bipm.org/documents/20126/2071204/JCGM_100_2008_E.pdf) | Requires comparison at specified validation points with both numerical and experimental uncertainty and enough reporting to reproduce the uncertainty statement | Confirms that literature agreement or an analytic curve cannot substitute for experimental observations plus uncertainty |

## Candidate details and conflicts

### Candidate A — Akbari et al. 2009

- Geometry: nominally rectangular PDMS channels with aspect ratios 0.13–0.76;
  measured cross-sections were acknowledged to be imperfect.
- Conditions: distilled water, pressure-driven steady flow, Reynolds number
  1–35.
- Measurements: pressure drop, flow rate, dimensions; three repeats per flow
  rate.
- Reported maximum input uncertainties: pressure 0.25%, length 0.01%, hydraulic
  diameter 6.12%, area 4.42%, and flow 0.5%; propagated friction-factor
  uncertainty was below 10%.
- Conflict: FlowLab v4 preserves v3's symmetry planes in the spanwise direction, while
  the experiment has finite sidewalls. This is a different physical model, not
  a drop-in validation point.
- Missing: raw pressure/flow rows, covariance or repeat-level data, coverage
  factors, and wall-shear observations.

### Candidate B — Qu et al. 2006

- Strength: both pressure and velocity-profile measurements, with explicit
  pressure-transducer uncertainty. Zheng and Meinhart add low-Re full/partial
  velocity profiles, but publish the observations in figures rather than a
  reusable raw packet.
- Conflicts: Reynolds range starts at 196, entrance effects are material, and
  the rectangular aspect ratio and plenum geometry differ from v4.
- Decision: useful for a future developing-flow campaign, not for the current
  bounded regime.

### Candidate C — Bohling et al. 2026 Dryad data

- Strength: public CSV velocity measurements and strong provenance.
- Conflicts: the scientific experiment contains a confined cylinder array and
  uses the empty rectangular channel only as a supporting measurement; the
  required pressure/force quantities and uncertainty budget are not present.
- Decision: not eligible for this validation claim.

## Resolution options

1. **Preferred: obtain Akbari raw data.** Request pressure, flow, temperature,
   fluid properties, measured cross-section coordinates, repeat values, and
   the full uncertainty/covariance definition. Preserve the received file
   unchanged, record its licence and SHA-256, and create a finite-aspect
   rectangular-duct campaign rather than reusing v4.
2. **Independent experiment.** Build a wide rectangular water channel with
   long upstream development length, calibrated differential pressure and flow
   measurements, PIV profiles, repeated runs, measured geometry, and a
   GUM-compatible uncertainty budget. Publish raw CSV and calibration records.
3. **Secondary option: Qu dataset acquisition.** Request the original micro-PIV
   and pressure tables for a separate higher-Re developing-flow validation;
   this cannot validate the current low-Re plane-Poiseuille regime.
4. **Exploratory only: plot digitization.** Digitize published Akbari plots with
   a documented pixel-to-data uncertainty. This can test ingestion and UQ code,
   but must remain `exploratory-not-validation`.

## Required acceptance packet

Before the experimental gate can pass, a dataset packet must contain:

- immutable raw observations and SHA-256 hashes;
- geometry, boundary conditions, fluid properties, and units;
- instrument calibration, repeatability, coverage factor, and uncertainty for
  each validation variable;
- a mapping from experimental quantities to CFD QoIs without calibration to
  the CFD result;
- numerical GCI and iterative uncertainty for the same validation point;
- comparison error and validation uncertainty calculated without changing any
  gate after seeing the data;
- an independent review record and explicit licence/redistribution status.
