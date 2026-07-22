# FlowLab Benchmark Fixture Scaffolding

Track C adds repeatable benchmark metadata without claiming CFD validation.
The registry lives under `benchmarks/` and currently lists:

- `straight-pipe`
- `plane-channel`
- `venturi`
- `heated-channel`
- `lid-driven-cavity-placeholder`
- `nozzle-placeholder`

`straight-pipe` and `plane-channel` are deliberately distinct geometries.
`straight-pipe` is a **true 3D circular pipe** with a Hagen-Poiseuille reference
(`128*mu*L*Q/(pi*D^4)`), targeting the dedicated 3D O-grid reference runner.
`plane-channel` is a **2D one-cell-thick planar channel** (`frontAndBack` type
`empty`, gap `H = diameter*3.6`) with a plane-Poiseuille reference
(`12*mu*U*L/H^2`) -- this is the geometry FlowLab's ordinary product pipeline
actually generates for a `pipe` edge, so it is the benchmark that matches product
output. See `server/flowlab/verification.py::plane_channel_reference`.

Each case uses schema `flowlab.benchmark_fixture.v1` and is marked
`pending-real-run`. The metadata defines expected inputs, required patches,
required fields, required diagnostics, and acceptance criteria, but intentionally
does not include observed numeric CFD results.

## Straight-Pipe Quantitative V&V Contract

`straight-pipe` additionally carries a `quantitativeVerification` contract for
the analytic Hagen-Poiseuille pressure-drop case. It is a promotion gate, not a
claim that FlowLab has already met the gate. While the fixture remains
`pending-real-run`, the contract contains no computed CFD values, no solver-run
identifier, and no passed result.

The contract fixes the comparison before a run is promoted:

- The analytic reference is `Delta p = 128 * mu * L * Q / (pi * D^4)` for
  steady, incompressible, Newtonian, fully developed laminar flow in a straight
  circular no-slip pipe.
- The reference envelope is limited to Reynolds number below 2100. Captured
  evidence must record a fully developed inlet profile, periodic pressure
  gradient, or a documented entrance-length exclusion before the comparison is
  applicable.
- Required QoIs are pressure drop (`Pa`) and mass flow rate (`kg/s`), with SI
  units for all governing geometry, material, and flow inputs.
- The current first-milestone validator accepts exactly three uniformly refined
  mesh levels, ordered coarse-to-fine, with a refinement ratio at least 1.3. It
  recomputes the observed order and fine-grid GCI for pressure drop rather than
  accepting a hand-entered summary. The predeclared fine-grid GCI limit is
  1.0%.
- Temporal verification is conditional: it requires at least three time-step
  levels for transient or pseudo-transient methods. A direct steady solve may
  declare it inapplicable only with recorded method and rationale.
- The eventual pressure-drop comparison must report dimensionless relative
  error against the analytic reference and meet the predeclared 5% maximum.
  This is an acceptance threshold, not an observed result.
- The eventual signed inlet/outlet mass-flow imbalance must be dimensionless
  and no greater than 0.001, with the integrated patch-flow table retained.
- Promotion requires solver/version/command/environment metadata, hashes for
  the generated case manifest, mesh artifact, raw results, solver log, and QoI
  extraction table, plus residual history and a mesh-quality report.
- A validated fixture must contain filesystem-backed artifact records with
  SHA-256 digests, an immutable evidence package, and an approved independent
  review. Names typed into `realOutputs.files` are not evidence. Hashes provide
  package integrity, not proof that a human reviewer is independent; that
  remains a controlled review-process responsibility.

For the straight-pipe gate, `qoi-extraction-table` must be a JSON artifact with
schema `flowlab.straight-pipe-qoi-extraction.v1`. It contains the SI
Hagen-Poiseuille inputs, three solver-produced mesh samples, their raw-result
artifact digest, and signed inlet/outlet mass flows. The evidence package must
repeat those QoIs and derived values consistently. The validator recomputes
Reynolds number, analytical pressure drop, relative error, mass imbalance,
observed order, and fine-grid GCI from the verified extraction artifact; it
also binds case-manifest, mesh, raw-result, solver-log, and QoI digests to their
respective stored artifacts.

These fields follow the core CFD verification practices summarized by NASA:
iterative convergence, conservation/solution consistency, spatial and temporal
convergence, and comparison with highly accurate analytic or benchmark
solutions. See [NASA's CFD verification assessment](https://www.grc.nasa.gov/www/wind/valid/tutorial/verassess.html).

## Current Status

The fixture files are scaffolds. Their acceptance criteria stay
`pending-real-run`, and `realOutputs.files` stays empty until Track A captures
solver-produced fields, logs, diagnostics, and mesh-quality evidence.

Do not promote a fixture to `validated` unless the promoted artifact set
contains real solver evidence:

- generated case manifest
- solver stdout/stderr logs
- result fields such as VTK/VTU or solver-native output
- diagnostic summaries such as residual history, patch flow/pressure, probes,
  force tables, or thermal summaries as required by the case
- mesh-quality or production-readiness evidence
- an explicit record of the solver, command, exit code, and runtime environment

## Promotion From Track A

When Track A produces a real benchmark run:

1. Copy only bounded, reviewable outputs into the matching benchmark case
   directory or a documented subdirectory.
2. Update `realOutputs.status` to `captured` and add each promoted artifact as
   a kind, safe relative path, and SHA-256 digest.
3. Add the canonical machine-readable QoI extraction artifact and a reviewed
   immutable evidence package that records solver command, runtime environment,
   captured QoIs, conservation, and mesh/time-refinement data. For the current
   straight-pipe milestone, provide exactly three uniform coarse-to-fine mesh
   levels and bind each sample to the verified raw-result artifact digest.
4. Attach an independent-review record with reviewer identity, timestamp, and
   the evidence-package digest. Mark a criterion `passed` only when the
   verified package proves it.
5. For `straight-pipe`, capture the quantitative V&V evidence required by its
   contract: analytic calculation, mesh/time refinement tables when applicable,
   error and conservation calculations, and all listed provenance artifacts.
6. Change the case `status` to `validated` only after every required criterion
   has real evidence.

Until those steps happen, benchmark fixtures are planning and review metadata
only. They must not be described as passed CFD benchmarks.
