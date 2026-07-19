# SU2 Docker Smoke Validation - 2026-06-12

FlowLab completed real local SU2 solve-through runs using Docker and the official SU2 v8.5.0 Linux binary bundle mounted from `/tmp/flowlab-su2/linux64-omp`.

Provisioning:

```bash
mkdir -p /tmp/flowlab-su2
curl -fL --retry 2 -o /tmp/flowlab-su2/SU2-v8.5.0-linux64-omp.zip https://github.com/su2code/SU2/releases/download/v8.5.0/SU2-v8.5.0-linux64-omp.zip
unzip -q /tmp/flowlab-su2/SU2-v8.5.0-linux64-omp.zip -d /tmp/flowlab-su2
unzip -q /tmp/flowlab-su2/linux64-omp.zip -d /tmp/flowlab-su2/linux64-omp
docker run --rm --platform linux/amd64 -v /tmp/flowlab-su2/linux64-omp:/opt/su2:ro ubuntu:22.04 /bin/bash -lc 'export PATH=/opt/su2/bin:$PATH PYTHONPATH=/opt/su2/bin:$PYTHONPATH && SU2_CFD --help'
```

Base command shape:

```bash
FLOWLAB_SU2_HOME=/tmp/flowlab-su2/linux64-omp npm run smoke:su2 -- --advanced-mode <mode> --runtime-root /tmp/flowlab-su2-<mode>-runtime --output /tmp/flowlab-su2-<mode>.json --timeout 300
```

Observed matrix:

| Mode | Status | Completed | Execution | Exit code | Latest iteration | Result files | Diagnostic files |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| `incompressible-navier-stokes` | `complete` | `true` | `docker` | `0` | `5` | `1` | `1` |
| `compressible-flow` | `complete` | `true` | `docker` | `0` | `49` | `1` | `1` |
| `heat-transfer` | `complete` | `true` | `docker` | `0` | `5` | `1` | `1` |

Completed modes produced:

- `flowlab_su2.vtk`
- `history.csv`

Latest parsed diagnostic rows:

```json
{
  "incompressible-navier-stokes": {
    "Inner_Iter": 5.0,
    "Outer_Iter": 0.0,
    "Time_Iter": 0.0,
    "rms[P]": -32.0,
    "rms[U]": -32.0,
    "rms[V]": -32.0
  },
  "compressible-flow": {
    "Inner_Iter": 49.0,
    "Outer_Iter": 0.0,
    "Time_Iter": 0.0,
    "rms[RhoE]": 7.318796343,
    "rms[RhoU]": -32.0,
    "rms[RhoV]": -32.0,
    "rms[Rho]": 1.134072241
  },
  "heat-transfer": {
    "Inner_Iter": 5.0,
    "Outer_Iter": 0.0,
    "Time_Iter": 0.0,
    "rms[P]": -32.0,
    "rms[U]": -32.0,
    "rms[V]": -32.0,
    "rms[h]": -0.8986149725
  }
}
```

The generated case manifests recorded native `.su2` mesh export, `case.cfg`, FlowLab mesh exports, `flowlab_su2_mode_preset.json`, `flowlab_su2_native_setup_checklist.json`, `flowlab_su2_capability_matrix.json`, and SHA-256/size metadata. This proves the SU2 adapter can materialize a FlowLab case, mount an official SU2 binary bundle into Docker, run `SU2_CFD case.cfg`, collect `flowlab_su2.vtk`, parse `history.csv`, and return complete job results on this machine.

Current generated SU2 cases also include `flowlab_su2_mode_preset.json`, `flowlab_su2_native_setup_checklist.json`, and `flowlab_su2_capability_matrix.json`. The preset uses schema `flowlab.su2_mode_preset.v1` to record the requested advanced mode, starter support level, `supportedByAdapter`, `requestedPhysicsResolved`, required native SU2 capabilities, readiness checks, blocking reasons, blocked/manual model gaps, manual setup requirements for export-only modes, native setup plans, expected primary fields, result expectations, fluid inputs, mesh assumptions, and `productionReady: false`. The checklist uses schema `flowlab.su2_native_setup_checklist.v1` and repeats the generated files, readiness items, expected fields, unresolved native modules, and manual setup actions in a direct review format. The matrix uses schema `flowlab.su2_capability_matrix.v1` to cover every FlowLab advanced mode, identify the active mode, summarize starter-supported single-zone modes, blocked export-only modes, handoff modes, readiness counts, manual native modules, and expected fields while keeping `productionReady: false`. Blocked/export-only modes also emit `flowlab_su2_native_config_template.cfg`, a guarded non-runnable native review template containing `FLOWLAB_TEMPLATE_ONLY= YES` and `FLOWLAB_UNSUPPORTED_MODE= YES`, required native capabilities, manual modules, expected fields, and starter marker/mesh context from `case.cfg`. Blocked/export-only modes now also emit `flowlab_su2_advanced_preflight.json`, which ties that guarded template, the generated handoff artifacts, unresolved native modules/actions, expected fields, and blocking reasons into a single machine-readable preflight record while keeping `productionReady: false` and `nativeSu2Ready: false`. Smoke reports now list the preset/checklist/matrix files in `caseValidation.requiredFilesPresent`, and blocked-mode validation also requires the review template and advanced preflight guardrails before accepting the generated bundle.

For SU2 water-hammer export-only cases, FlowLab also emits
`flowlab_su2_water_hammer_handoff.json` and
`flowlab_su2_water_hammer_waveform.csv`. The mode preset records a passing
`moc-boundary-handoff-export` readiness item for those generated review files,
while keeping the native transient-liquid model, native MOC boundary mapping,
and pipe-wall elasticity checks failed. The generated native setup checklist
therefore lists the handoff JSON/CSV in `generatedFiles`, but still keeps
`requestedPhysicsResolved: false` and `productionReady: false`.

For SU2 conjugate heat-transfer export-only cases, FlowLab now emits
`flowlab_su2_cht_handoff.json`. The handoff records the intended fluid zone,
the missing solid-zone mesh, manual fluid-solid interface coupling, required
solid material inputs, expected CHT fields, blocking reasons, and native SU2
MULTIZONE setup actions. The mode preset records a passing
`cht-handoff-export` readiness item for this generated review file while
keeping the native multi-zone driver, solid-zone mesh, and thermal-interface
checks unresolved. The generated native setup checklist lists the handoff in
`generatedFiles`, but still keeps `requestedPhysicsResolved: false` and
`productionReady: false`.

For SU2 multiphase and cavitation export-only cases, FlowLab emits
`flowlab_su2_multiphase_handoff.json` or
`flowlab_su2_cavitation_handoff.json`. These phase handoffs record the intended
phase model, two-phase material placeholders, manual native interface setup,
expected phase fields, and cavitation saturation pressure when relevant. The
mode presets record a passing `phase-handoff-export` readiness item for these
review files while keeping the native multiphase solver, VOF/free-surface
capture, cavitation phase-change law, and saturation-pressure source treatment
unresolved.

For SU2 rigid-body-fluid-force export-only cases, FlowLab emits
`flowlab_su2_rigid_body_handoff.json`. This handoff records the intended
moving-body/FSI coupling, identifies MuJoCo as the current approximate
rigid-body sandbox, keeps motion setup manual, and lists expected force/moment
fields. The mode preset records a passing `rigid-body-handoff-export`
readiness item while keeping dynamic mesh, native FSI, and coupled force
feedback setup unresolved.

Blocked-mode validation evidence was added on 2026-06-15 through focused
backend tests for generated phase, cavitation, water-hammer, CHT, and
rigid-body handoff artifacts. These tests verify the guarded
`FLOWLAB_UNSUPPORTED_MODE= YES` template remains present, generated checklists
list the handoff files, capability matrices include the handoff modes, and
case validation blocks malformed or missing handoffs before runtime resolution.
The same validation path now requires `flowlab_su2_advanced_preflight.json`
for every blocked/export-only SU2 mode and verifies that its handoff list,
artifact checks, unresolved actions, expected fields, and non-production flags
match the mode preset before runtime resolution.

Limitations:

- These are starter cases on FlowLab's simple port-aware 2D quad-strip mesh, not production SU2 validation benchmarks.
- SU2 multiphase, cavitation, water-hammer, conjugate heat-transfer, and rigid-body-fluid-force modes remain blocked with `FLOWLAB_UNSUPPORTED_MODE= YES`; `flowlab_su2_mode_preset.json`, `flowlab_su2_capability_matrix.json`, and `flowlab_su2_advanced_preflight.json` record the missing multi-zone, multiphase, transient pressure-wave, or coupled setup requirements as failing readiness checks with manual setup requirements, `nativeSetupPlan.manualNativeModules`, the guarded native config review template, handoff artifacts, unresolved actions, and `resultExpectations.expectedPrimaryFields`. Those handoff artifacts are review evidence only; they are not wired into native SU2 multiphase/cavitation, transient compressible-liquid, MULTIZONE CHT, dynamic-mesh, or FSI setups. The generated native setup checklist additionally exposes unresolved modules and manual requirements as `actionItems`.
- The Docker path depends on `FLOWLAB_SU2_HOME` pointing to the extracted Linux binary bundle and Docker being available.
