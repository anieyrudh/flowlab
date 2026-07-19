# Data and methodology sources

## Numerical and analytic sources

- Accepted affine grid-invariance report:
  `2026-07-16-forced-mms-v29-affine-grid-invariance`
- Accepted non-affine MMS report:
  `2026-07-16-forced-mms-v36-non-affine-mms`
- Accepted independent plane-Poiseuille force report:
  `2026-07-16-forced-mms-v40-laminar-force-benchmark`
- Independent traction utility:
  `benchmarks/tools/flowlabPatchTractionAudit`

Each campaign manifest records current SHA-256 fingerprints rather than
trusting these names alone.

## Methodology

- ASME V&V 20: validation comparison and uncertainty framing
  <https://www.asme.org/codes-standards/find-codes-standards/standard-for-verification-and-validation-in-computational-fluid-dynamics-and-heat-transfer>
- NASA CFD Verification Assessment: observed order and GCI reporting
  <https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html>

## External-data research result

The 2026-07-16 five-lane search did not find a boundary-compatible,
machine-readable experiment containing all required quantities and uncertainty.
The closest pressure/flow source is Akbari, Sinton, and Bahrami (2009), but its
raw pointwise measurements are not public and its finite-sidewall PDMS geometry
does not match the v2 spanwise-symmetry model. See
`EXPERIMENTAL_DATASET_RESEARCH.md` and the machine-readable
`experimental-dataset-assessment.json` follow-up. Issue `CAM-0001` remains open.
