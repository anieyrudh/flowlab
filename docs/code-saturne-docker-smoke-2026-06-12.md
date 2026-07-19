# Code_Saturne Docker Smoke Validation - 2026-06-12

FlowLab completed a real local Code_Saturne solve-through using Docker and the existing local image `flowlab-code-saturne:local`.

Runtime check:

```bash
docker run --rm flowlab-code-saturne:local /bin/bash -lc 'code_saturne run --help | head -80'
```

Command:

```bash
FLOWLAB_CODE_SATURNE_IMAGE=flowlab-code-saturne:local npm run smoke:code-saturne -- --runtime-root /tmp/flowlab-code-saturne-vtk-runtime --output /tmp/flowlab-code-saturne-vtk-smoke.json --timeout 300
```

Observed result:

- Smoke: `code-saturne-incompressible-navier-stokes-solve-through`
- Status: `complete`
- Completed: `true`
- Execution: `docker`
- Exit code: `0`
- Docker image: `flowlab-code-saturne:local`
- Native fallback: `code_saturne` was not on `PATH`; Docker was used.
- Logs captured: `28`

FlowLab converted the Code_Saturne fluid-domain EnSight Gold output into one bounded VTK result:

- `RESU/20260612-0503/postprocessing/flowlab_code_saturne_fluid.vtk`

Converted VTK fields:

- `pressure`
- `total_pressure`
- `cfl`
- `fourier_number`
- `velocity`

FlowLab captured these bounded `RESU` diagnostics:

- `RESU/20260612-0503/compile.log`
- `RESU/20260612-0503/listing`
- `RESU/20260612-0503/performance.log`
- `RESU/20260612-0503/preprocessor.log`
- `RESU/20260612-0503/residuals.csv`
- `RESU/20260612-0503/run_solver.log`
- `RESU/20260612-0503/setup.log`
- `RESU/20260612-0503/summary`
- `RESU/20260612-0503/timer_stats.csv`

Latest parsed residual row:

```json
{
  "t": 2.5,
  "velocity": 0.016382631,
  "pressure": 0.31886755
}
```

The generated runtime also contains Code_Saturne postprocessing and checkpoint artifacts, including:

- `RESU/20260612-0503/checkpoint/main`
- `RESU/20260612-0503/checkpoint/mesh_input`
- `RESU/20260612-0503/postprocessing/RESULTS_BOUNDARY.case`
- `RESU/20260612-0503/postprocessing/RESULTS_FLUID_DOMAIN.case`
- `RESU/20260612-0503/postprocessing/results_fluid_domain.velocity.00001`
- `RESU/20260612-0503/postprocessing/results_fluid_domain.pressure.00001`
- `RESU/20260612-0503/postprocessing/results_fluid_domain.total_pressure.00001`

FlowLab records these Code_Saturne postprocessing files on disk and converts the starter fluid-domain EnSight fields into a legacy ASCII VTK hexahedral result for browser visualization. The converter is intentionally narrow: one time slice, fluid-domain `hexa8` geometry, scalar/vector element fields averaged onto points, and bounded output size.

The recorded smoke manifest captured the starter Code_Saturne case files, including
`DATA/setup.xml`, `DATA/run.cfg`, `DATA/cs_user_scripts.py`,
`SRC/cs_user_boundary_conditions.f90`, `MESH/flowlab_mesh.msh`, FlowLab mesh
exports, and SHA-256/size metadata. This proves the Code_Saturne adapter can
materialize a FlowLab case, run the Docker-backed solver, compile user
boundary-condition code, execute the starter case, collect `RESU` diagnostics,
and return a complete job result on this machine.

Current generated Code_Saturne cases also include:

- `DATA/flowlab_physics_preset.json`
- `DATA/flowlab_native_setup_checklist.json`
- `DATA/flowlab_code_saturne_capability_matrix.json`
- `DATA/cs_user_physics.py`
- Unresolved physics modes additionally include
  `DATA/flowlab_native_physics_review.py`, a guarded native-review payload with
  `FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True`,
  `FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False`, and
  `FLOWLAB_PRODUCTION_READY = False`.
- Compressible-flow mode additionally includes
  `DATA/flowlab_compressible_handoff.json`, a native-review manifest for the
  pressure-based starter surrogate, required compressible-flow module,
  equation-of-state and energy-equation setup, total/static boundary-condition
  review, acoustic timestep/CFL controls, and expected density, temperature,
  and Mach fields.
- Multiphase VOF mode additionally includes
  `DATA/flowlab_multiphase_handoff.json`, a native-review manifest for phase
  material placeholders, manual VOF/free-surface interface setup,
  initialization regions, boundedness review, and expected phase fields.
- Cavitation mode additionally includes
  `DATA/flowlab_cavitation_handoff.json`, a native-review manifest for
  liquid/vapour material placeholders, saturation pressure, manual phase-change
  setup, bounded pressure review, and expected cavitation fields.
- Water-hammer mode additionally includes
  `DATA/flowlab_water_hammer_handoff.json` and
  `DATA/flowlab_water_hammer_waveform.csv`.
- Conjugate heat-transfer mode additionally includes
  `DATA/flowlab_cht_handoff.json`, a native-review manifest for the starter
  fluid domain, missing solid-domain mesh, manual interface coupling, solid
  material inputs, expected CHT fields, and Code_Saturne setup actions.

These files explicitly record the requested FlowLab advanced mode, support level,
enabled starter models, blocked/manual native Code_Saturne models, fluid inputs,
readiness checks, blocking reasons, manual setup requirements, setup.xml model
toggles, native setup plans, expected primary result fields, result-conversion
expectations, generated native setup checklist action items, adapter-wide
capability matrix entries, `supportedByAdapter`, `requestedPhysicsResolved`,
and `productionReady: false`.

Current presets also include `turbulencePlan` metadata. Laminar starter modes
record `model: off` and `starterStatus: laminar-starter`; RANS starter or
surrogate modes record `model: k-epsilon` and `starterStatus: rans-starter`.
The plan keeps production turbulence evidence blocked until native turbulence
review, near-wall y-plus evidence, mesh-independence checks, and result-field
convergence are available. LES, DNS, transition models, and richer turbulence
closures remain explicitly unresolved.
Focused generated-case tests now cover incompressible, heat-transfer,
compressible handoff/surrogate, cavitation, multiphase, CHT, water-hammer handoff
export, and rigid-body metadata.

The heat-transfer preset now carries a structured `thermalStarter` and
`thermalBoundaryPlan` for the `temperature_celsius` starter scalar. These
metadata blocks record inlet/initial temperature, adiabatic wall placeholder
intent, expected temperature output, and explicit exclusions for CHT, radiation,
buoyancy validation, and phase change. This remains a passive scalar starter;
it is not native CHT, radiative heat transfer, phase-change heat transfer, or
validated buoyant thermal flow.

The conjugate heat-transfer preset is now `metadata-plus-handoff`. It records a
passing `cht-handoff-export` readiness item for the generated
`DATA/flowlab_cht_handoff.json` review file, while keeping solid-domain mesh
generation, fluid-solid thermal interface coupling, solid material properties,
and heat-flux continuity evidence unresolved. The handoff improves native setup
review only; it is not wired into an executable Code_Saturne CHT case.

The multiphase VOF and cavitation presets are now `metadata-plus-handoff`.
They record passing `phase-handoff-export` readiness items for generated review
files while keeping native VOF/free-surface controls, phase material tables,
phase initialization, boundedness evidence, cavitation phase-change laws, and
saturation-pressure source terms unresolved. These handoffs improve native
setup review only; they are not wired into executable Code_Saturne phase-
resolved cases.

The rigid-body-fluid-force preset is now `metadata-plus-handoff`. It emits
`DATA/flowlab_rigid_body_handoff.json`, recording the intended moving-body/FSI
coupling, MuJoCo as the current approximate rigid-body sandbox, manual
mesh-motion/body-kinematics setup, expected force/moment/displacement fields,
and native dynamic-mesh or co-simulation actions that remain unresolved.

Limitations:

- This validates the starter incompressible Code_Saturne workflow, not every advanced mode.
- Heat-transfer is limited to the documented `temperature_celsius` starter
  scalar and requires native review before any production thermal claim.
- The physics preset metadata, phase handoff export, water-hammer handoff export, CHT handoff export, and rigid-body handoff export improve
  auditability for advanced modes but do not by themselves implement native
  compressible, multiphase, cavitation, CHT, coupled water-hammer, or
  fluid-structure Code_Saturne setups.
- `metadata-plus-handoff` and `metadata-only` presets intentionally report
  `requestedPhysicsResolved: false` with failing readiness checks and manual
  setup requirements.
- Each unresolved preset now identifies the native Code_Saturne modules still
  needed in `nativeSetupPlan.manualNativeModules` and the fields a true native
  setup should produce in `resultExpectations.expectedPrimaryFields`.
- `DATA/flowlab_native_setup_checklist.json` separates files FlowLab generated
  from unresolved native Code_Saturne module/action items for advanced modes.
- `DATA/flowlab_code_saturne_capability_matrix.json` gives a whole-adapter
  view of starter-supported, surrogate, handoff, and metadata-only modes,
  including expected fields, manual native modules, handoff artifacts,
  readiness counts, and blocking reasons.
- `DATA/flowlab_native_physics_review.py` gives unresolved modes a compact
  guarded review payload for required native modules, manual setup actions,
  handoff artifacts, and expected output fields. It is not imported by the
  starter `code_saturne run` path.
- The generated Code_Saturne output is EnSight Gold/checkpoint data; FlowLab currently converts only the starter fluid-domain EnSight output into VTK.
- The mesh remains FlowLab v1's thin port-aware extrusion and should not be treated as production CFD meshing.
- Industrial Code_Saturne use still requires review of the generated XML, physical groups, boundary conditions, and mesh quality.
