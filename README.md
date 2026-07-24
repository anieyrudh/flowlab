# FlowLab

FlowLab is a desktop-only local fluid-simulation workstation with two solver tiers:

- **Instant 1D hydraulics** in the browser for pipes, pumps, sinks, mixers, Venturi effects, Bernoulli/head losses, Reynolds regimes, cavitation thresholds, control-volume force estimates, parameter sweeps, and water-hammer previews.
- **Advanced CFD orchestration** through a local FastAPI service that generates and queues starter cases for OpenFOAM, SU2, Code_Saturne, and MuJoCo-backed rigid-body fluid-force experiments.

The advanced tier is dependency-aware. It reports missing Docker/native solver binaries instead of faking CFD execution.
OpenFOAM, SU2, and configured Code_Saturne jobs are materialized under `runtime/flowlab/jobs/` and run Docker-first when Docker is available. Code_Saturne also supports a native `code_saturne` run path. MuJoCo uses native Python plus the `mujoco` package. If Docker, native commands, or required Python modules are unavailable, the service reports a blocked dependency state instead of starting a pretend run.

The instant solver is port-aware. Dragged pipe endpoints, selected ports, and component rotation change the computed effective length, bend angle, geometry-derived minor-loss coefficient, control-volume force direction, and water-hammer closure time. The edge inspector reports `Effective length`, `Port bend`, and `Geometry K` so visual edits are traceable to the Tier 1 hydraulic response.
The editor validates pipe endpoint edits and imported projects for self-loops, invalid endpoint port directions, and duplicate occupied component ports. The live warnings dock also flags disconnected components, isolated nodes, and connected components that lack both a source and a sink boundary.

## Desktop App (macOS)

The fastest supported path is the native macOS application. It packages the production React UI and local FastAPI service in a signed AppKit/WebKit shell; there is no mobile target.

Prerequisites on the build machine:

- Node dependencies installed with `npm install`.
- Xcode Command Line Tools and an arm64 CPython 3.12 build environment.
- The exact packages in `desktop/macos/requirements-build.txt`.

The packaged UI and local service do not require Python on the run machine.
Docker Desktop and `flowlab/openfoam11-gmsh:2026-07-13` remain explicit
external runtime dependencies only for advanced OpenFOAM execution.

Build and launch:

```bash
python3.12 -m venv .venv/desktop-build
.venv/desktop-build/bin/python -m pip install -r desktop/macos/requirements-build.txt
FLOWLAB_BUILD_PYTHON=.venv/desktop-build/bin/python npm run desktop:build
npm run desktop:qa
open release/FlowLab.app
```

The build supports arm64 macOS 13.0 or newer and packages CPython 3.12 plus the
backend dependencies inside the application. It rejects other architectures,
Python series, and PyInstaller versions. Runtime jobs and logs live under
`~/Library/Application Support/FlowLab/`; the desktop backend log is
`flowlab-backend.log` there.

The `Validated regimes` panel currently reports `Candidate bounded regime — laminar open-boundary all-hex` as promotion-blocked. The v4 numerical, reproducibility, negative-control, and product-contract gates passed, but independent empirical validation has not: the completed FDA nozzle Re=500 v2 assessment is `validated-blocked` and its pressure reference is formally nonpromotional. The corrected successor mesh-only recovery passed its prospective 1% geometry contract, and the next velocity-focused numerical-verification design passes its offline fail-closed validator. Hugging Face Jobs twice confirmed `amd64`, but the connector credential could neither create nor commit main-branch evidence to the private dataset; no artifact or solver resulted. The local `fluidmech` credential separately proved private dataset and Space write access. The official OpenFOAM image then failed the private Space's rootless builder on UID/GID 98765. A prospectively frozen R3 image recovery using UID/GID 1000 passed all local identity gates and built, pushed, and ran at an immutable private Space commit and commit-prefixed registry digest. That qualifies only the image path: volume recovery, both coarse pilots, full HF infrastructure qualification, and the six-case execution contract remain blocked. `experimentalDatasetPinned` remains false, the runnable action is hidden, and the dedicated API fails closed with HTTP 409. The historical coarse preset implementation can atomically mint the immutable 12³ OpenFOAM case only after a digest-checked final campaign report passes every required gate and explicitly sets `promotionAuthorized=true`. Any generated-file mutation removes eligibility. All ordinary project-generated cases remain explicitly experimental.

The packaged app intentionally carries only the accepted evidence and
traction-audit source needed at runtime, not the full research run tree. Local
builds are ad-hoc signed by default. External distribution still requires a
Developer ID Application signature, Apple notarization with a stapled ticket,
and a launch test on a clean supported arm64 Mac. The historical
axisymmetric/XYZ internal-package evidence is recorded in
[docs/desktop-axisymmetric-qa-2026-07-23.md](docs/desktop-axisymmetric-qa-2026-07-23.md);
the self-contained packaging disposition is tracked separately in
[docs/desktop-release-qa-2026-07-23.md](docs/desktop-release-qa-2026-07-23.md);
the bounded full O-grid package QA and its separate scientific stop are recorded
in
[docs/desktop-full-ogrid-qa-2026-07-24.md](docs/desktop-full-ogrid-qa-2026-07-24.md).

## Run Locally

Install frontend dependencies:

```bash
npm install
```

Install backend dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

Start the solver service:

```bash
npm run server
```

In another terminal, start the UI:

```bash
npm run dev
```

Open the Vite URL shown by the terminal, usually `http://127.0.0.1:5173`.

## Solver Notes

- OpenFOAM is the primary advanced CFD target for incompressible Navier-Stokes, compressible flow, heat transfer, multiphase VOF, and cavitation workflows.
- SU2 is included for compressible/incompressible CFD and design-oriented workflows. FlowLab v1 generates a native ASCII `.su2` mesh, mode-aware `case.cfg` files, required `flowlab_su2_mode_preset.json` support manifests, `flowlab_su2_native_setup_checklist.json` native setup checklists, and `flowlab_su2_capability_matrix.json` all-mode capability matrices. Blocked/export-only SU2 modes also emit `flowlab_su2_advanced_preflight.json` to summarize the guarded template, handoff artifacts, unresolved native modules/actions, expected fields, and blocking reasons. Local execution still requires Docker or `SU2_CFD` on `PATH`.
- Code_Saturne is included as an optional industrial finite-volume backend. FlowLab v1 generates a standard `DATA`/`SRC`/`RESU`/`MESH` starter case with a Gmsh `.msh` mesh and runs it when native `code_saturne` is on `PATH` or `FLOWLAB_CODE_SATURNE_IMAGE` points to a Docker image containing `code_saturne`. The starter `setup.xml` includes an `all_cells` volume zone and maps deterministic Gmsh physical groups to Code_Saturne inlet, outlet, and wall boundary zones.
- MuJoCo is included only for rigid bodies with phenomenological fluid forces. It is not a Navier-Stokes CFD solver. FlowLab v1 generates an MJCF model plus `run_mujoco.py`, and runs it only when Python and the `mujoco` package are available.

## Mesh And Result Pipeline

FlowLab v1 mesh export is intentionally simple and deterministic. Generated solver cases include these mesh artifacts when the project has supported geometry:

- `flowlab_project.json`: the project graph used to generate the case.
- `mesh/flowlab_mesh.json`: FlowLab mesh metadata with points, quad cells, cell types, and edge regions.
- `mesh/flowlab_mesh.vtk`: legacy ASCII VTK `UNSTRUCTURED_GRID` mesh.
- `mesh/flowlab_mesh.vtu`: ASCII VTU `UnstructuredGrid` mesh.
- `mesh/flowlab_mesh.su2`: native ASCII SU2 mesh with quadrilateral elements and line boundary markers.
- `mesh/flowlab_mesh.msh`: Gmsh 2.2 ASCII mesh with physical groups for Code_Saturne import.
- `mesh/controls.json`: normalized mesh controls, including base resolution, optional longitudinal refinement, default 2D transverse boundary-layer strip controls, per-edge refinement factors, adaptive-mesh planning settings, and quality thresholds.
- `mesh/quality.json`: deterministic source-mesh quality report with min/max cell area, aspect ratio, interior angles, source non-orthogonality/skewness estimates, inverted/degenerate cell counts, thresholds, warnings, and `productionReady: false`.
- `mesh/refinement_plan.json`: deterministic per-region refinement evidence, including edge refinement factors, optional feature-refinement factors, throat/transition station clustering, and `productionReady: false`.
- `mesh/boundary_layer_plan.json`: deterministic target-y-plus first-cell sizing estimates per edge, including Reynolds number, smooth-pipe friction factor, friction velocity, target first-cell height, starter strip height, and `productionReady: false`.
- `mesh/prism_layer_plan.json`: deterministic native prism-layer review inputs derived from the y-plus plan, including first-cell height, layer count, growth rate, total layer-stack height, required native mesher controls, and unresolved y-plus/layer-quality evidence with `productionReady: false`.
- `mesh/adaptation_plan.json`: solver-neutral native adaptation/refinement plan tying geometry features, boundary-layer targets, optional user adaptive-mesh planning settings, and future solver-field indicators to per-edge refinement actions. It remains `productionReady: false` until native adapted 3D meshes, adaptation history, and post-adaptation quality/y-plus reports exist. The desktop inspector can export adaptive planning config, but FlowLab does not perform live remeshing yet.
- `mesh/meshio_roundtrip_validation.json`: optional meshio read/write/re-read validation for generated VTK, VTU, SU2, and Gmsh mesh artifacts. If Python `meshio` is unavailable, this records `status: skipped` with a clear dependency note and does not block case generation.
- `mesh/physical_groups.json`: deterministic source-region to solver physical-group map, including Gmsh physical names/tags, SU2 markers, Code_Saturne groups, OpenFOAM aggregate patch names, inlet/outlet/wall/front-back roles, and `productionReady: false`.
- `mesh/openfoam_snappy_handoff.json`: review-only OpenFOAM native meshing handoff that translates physical groups and prism-layer sizing into `snappyHexMesh`/`addLayersControls` planning inputs, the triSurface/dictionary artifacts, reviewed-geometry metadata, STL preview metadata, user boundary-tag roles, patch roles, refinement regions, and required quality evidence while keeping `productionReady: false`.
- `constant/triSurface/reviewedFlowLabSurfaces.stl`, `system/snappyHexMeshDict`, `system/surfaceFeatureExtractDict`, and `system/meshQualityDict`: materialized OpenFOAM meshing artifacts. By default the STL is generated from FlowLab's starter strip geometry; users can also import reviewed ASCII STL text or a safe relative `.stl` source path through `solver.reviewedGeometry`. Imported ASCII STL metadata is summarized for preview with triangle count, vertex count, bounds, ASCII validation, and a conservative edge-pair watertight check. For production-style reviewed geometry, `solver.reviewedGeometry.surfaces[]` can carry multiple reviewed STL surfaces with `surfaceName`, `role`, `patchName`, `cadReviewed`, per-surface metadata, and notes. FlowLab materializes those as separate `constant/triSurface/<surfaceName>.stl` files such as `inlet.stl`, `outlet.stl`, `walls.stl`, and optional `interface_*.stl`; `reviewedFlowLabSurfaces.stl` remains only the generated/combined fallback. The dictionaries include a computed non-placeholder `locationInMesh`, so the native preflight and native mesh runner can run against real files. Production approval remains blocked unless the materialized triSurface set is explicitly marked `cadReviewed: true`, reviewed inlet/outlet/wall surfaces are present, expected patches appear in native snappy/checkMesh evidence, and native mesh evidence passes.
- Reviewed OpenFOAM surface boundary-condition controls live on each `solver.reviewedGeometry.surfaces[]` entry. Supported starter controls are inlet `velocity-inlet`, `mass-flow-inlet`, and `pressure-inlet`; outlet `pressure-outlet` and `outflow`; wall `no-slip-wall`, `slip-wall`, `rough-wall`, `heat-flux-wall`, and `temperature-wall`; and guarded interface placeholders `coupled-interface`/`mapped-interface`. Generated OpenFOAM cases must write matching `boundaryField` entries for every reviewed patch used by `snappyHexMeshDict` into `0/U`, `0/p`, `0/T` when thermal fields are active, and `0/alpha.water` when VOF/multiphase fields are active. Missing inlet/outlet/wall reviewed-surface boundary conditions block before OpenFOAM solve execution instead of falling back silently to aggregate starter patches.
- `mesh/openfoam_native_mesh_preflight.py`: local preflight script for materialized OpenFOAM cases. It checks the materialized triSurface, installed native dictionaries, non-placeholder `locationInMesh`, and required snappy/checkMesh/y-plus evidence before a native production meshing attempt.
- `mesh/su2_native_meshing_handoff.json`: review-only SU2 native meshing handoff that records marker preservation, viscous-layer sizing, adaptation targets, expected native `.su2` files, and required SU2 startup/mesh diagnostic evidence while keeping `productionReady: false`.
- `mesh/code_saturne_native_meshing_handoff.json`: review-only Code_Saturne native meshing handoff that records Gmsh/CGNS/MED import intent, volume/boundary group localization, prism-layer import requirements, adaptation evidence, and preprocessing/listing quality evidence while keeping `productionReady: false`.
- `mesh/openfoam_snappyHexMeshDict.template`, `mesh/openfoam_surfaceFeatureExtractDict.template`, and `mesh/openfoam_meshQualityDict.template`: review-only OpenFOAM dictionary templates generated from the snappy handoff. They preserve patch/layer/refinement intent separately from the installed starter dictionaries, which are the runnable preflight inputs.
- `mesh/production_mesh_plan.json`: machine-readable production-mesh gap plan tying source quality, refinement intent, boundary-layer sizing, CAD-quality geometry, 3D volume meshing, prism layers, solver-native checks, and remaining blocking reasons together with `productionReady: false`.
- `mesh/native_meshing_plan.json`: review manifest for moving from the starter strip to native production meshing. It lists Gmsh/OpenFOAM/SU2/Code_Saturne handoff stages, required evidence, solver quality commands, and remaining CAD/prism/native-check blockers with `productionReady: false`.
- `mesh/production_mesh_acceptance.json`: solver-neutral production mesh acceptance checklist. It blocks production approval until CAD-quality geometry, native 3D volume mesh, boundary-layer prism cells, and OpenFOAM/SU2/Code_Saturne quality evidence are available. Its `nativeQualityEvidence` block records missing or captured solver-native commands, command exit codes, log paths, checkMesh failed-check counts, non-orthogonality, skewness, aspect ratio, min volume, layer/addLayers excerpts, y-plus/wall-distance evidence, and adaptation history required before a mesh can be approved.
- `mesh/gmsh_production_handoff.geo`: review-only Gmsh/OpenCASCADE scaffold preserving FlowLab physical group names and sizing intent. It is deliberately not a watertight CAD model or production mesh.
- `mesh/openfoam_review.json`: OpenFOAM-specific mesh readiness manifest with fitted-polyMesh, source-quality, scripted `checkMesh`, CAD-topology, boundary-layer/y-plus, and native-evidence checks. It keeps `productionReady: false` until production mesh evidence exists.
- `mesh/generate_mesh.py`: a small regeneration script for the exported project.

Supported geometry is limited to 2D quad-strip meshes along FlowLab rotated port-to-port spans: circular pipes, circular Venturi edges with throat interpolation, straight circular ducts such as expansion/contraction/nozzle edges, and rectangular channel edges. Mesh regions record `fromNode`, `toNode`, `fromPort`, `toPort`, `start`, `end`, `spanLengthPx`, segment count, transverse divisions, boundary-layer layer count, and refinement factor metadata. FlowLab also inserts simple connector cells across two-port components so fitted solver meshes remain face-connected through junction bodies. Unsupported edge types, unsupported shape kinds, missing endpoints, invalid ports, and non-positive dimensions fail closed instead of producing a pretend CAD-quality mesh.
The desktop inspector exposes these starter mesh controls in the advanced solver section: mesh resolution, longitudinal refinement, 2D boundary-layer strip count/growth, target y-plus, feature-aware station clustering, selected-edge local refinement, starter quality thresholds, and adaptive-mesh planning controls for target field, error mode, cadence, cell limits, sizing, gradation, and adapted-state export placeholders. The Mesh QA dock also includes reviewed-geometry controls for importing ASCII STL, previewing STL metadata, marking CAD review, tagging inlet/outlet/wall/interface boundaries, and recording review notes. The saved project JSON carries those values into case generation so mesh/export evidence reflects the user's GUI choices.
`solver.meshControls` can increase longitudinal segments, tune the default symmetric 2D transverse boundary-layer strip cells near both walls (up to 12 layers), select a `transverseDistribution` of `boundary-layer` (default, wall-clustered for near-wall/turbulent resolution) or `uniform` (evenly spaced across the gap, which resolves a laminar parabolic core far better and roughly triples pressure-drop accuracy for internal laminar flow at the same cell count), set per-edge refinement regions, enable feature-aware station clustering around Venturi throats or diameter transitions, set a target y-plus sizing estimate, and override starter quality thresholds. `solver.adaptiveMesh` can store an export-only adaptation plan with target field, error mode, cadence, max cells, min/max cell size, gradation, and adapted-state placeholder intent; it is written into `mesh/controls.json` and `mesh/adaptation_plan.json` but does not trigger live remeshing. `solver.reviewedGeometry` can store `sourceType`, `cadReviewed`, `reviewedAt`, `reviewNotes`, uploaded `stlText`, a safe relative `stlPath`, legacy single-STL `boundaryTags`, and multi-surface `surfaces[]` entries. Each reviewed surface records `surfaceName`, `role`, `patchName`, per-surface `cadReviewed`, metadata, notes, and an optional OpenFOAM boundary-condition control; roles map to OpenFOAM `patchInfo` as inlet/outlet/interface `type patch` and wall `type wall`. Unsafe paths, non-`.stl` paths, duplicate/unsafe patch names, missing inlet/outlet/wall reviewed surfaces, malformed/non-ASCII STL text, and missing required reviewed-patch boundary conditions are rejected or marked not ready. Mesh controls improve the deterministic source mesh and help OpenFOAM `checkMesh` pass for starter cases, but they are still not a CAD-quality 3D mesher. `mesh/prism_layer_plan.json` turns the y-plus sizing estimate into native prism-layer review inputs, while keeping native prism/hex-layer cells and solver y-plus evidence unresolved. `mesh/adaptation_plan.json` now converts geometry, boundary-layer, user adaptive planning config, and future solver-field indicators into native adaptation targets, while keeping native adapted volume meshes and adaptation history unresolved. `mesh/physical_groups.json` removes naming ambiguity before native meshing by recording which FlowLab source regions become Gmsh physical names, SU2 markers, Code_Saturne groups, and OpenFOAM aggregate patches. `mesh/openfoam_snappy_handoff.json`, `mesh/su2_native_meshing_handoff.json`, and `mesh/code_saturne_native_meshing_handoff.json` make solver-native production meshing paths explicit by mapping physical groups, legacy reviewed STL boundary tags, or multi-surface reviewed STL patches into review-only patch/marker/group, viscous-layer, adaptation, boundary-condition coverage, and quality-evidence plans; `mesh/openfoam_native_mesh_preflight.py` turns the OpenFOAM handoff blockers into a local runnable check before `snappyHexMesh`. FlowLab materializes `constant/triSurface/reviewedFlowLabSurfaces.stl` for generated/combined fallback geometry and materializes separate reviewed files for multi-surface projects, then installs OpenFOAM `system/` dictionaries with a validated `locationInMesh`, so preflight checks run against generated or reviewed surface/dictionary artifacts instead of placeholders. During OpenFOAM job execution, FlowLab also runs a native mesh stage before the solver: `surfaceFeatureExtract`, `blockMesh` when no `constant/polyMesh` base mesh exists, `snappyHexMesh -overwrite`, `checkMesh -allGeometry -allTopology`, and optional `postProcess -func yPlus -latestTime` when available. It writes `log.surfaceFeatureExtract`, `log.blockMesh` when used, `log.snappyHexMesh`, `log.checkMesh`, optional `log.yPlus`, and updates `mesh/production_mesh_acceptance.json` with command runs, quality metrics, layer excerpts, wall evidence, reviewed STL state, boundary coverage, boundary-condition coverage, and expected patch presence. Missing required native mesh commands, missing required reviewed STL inlet/outlet/wall surfaces, missing expected patches in snappy/checkMesh logs, missing reviewed-patch field boundary conditions, or failed `checkMesh` block before production approval. `mesh/production_mesh_plan.json` records this explicitly: starter topology, source quality, refinement intent, physical-group mapping, generated or reviewed OpenFOAM surface/dictionaries, solver-specific native-meshing handoffs/templates, adaptation targets, boundary tags/surfaces, boundary-condition coverage, and y-plus/prism-layer sizing inputs can pass while native 3D volume mesh, generated prism-layer mesh, native adaptation history, expected patch evidence, and solver-native checkMesh/y-plus/non-orthogonality evidence remain failing readiness checks. `mesh/native_meshing_plan.json`, `mesh/gmsh_production_handoff.geo`, and `mesh/production_mesh_acceptance.json` turn those blockers into a concrete native-meshing handoff and acceptance checklist, without claiming production readiness. The [non-axisymmetric full O-grid milestone](docs/non-axisymmetric-o-grid-milestone-2026-07-23.md) is intentionally a separate future geometry and verification track.
The mesh quality gate also fails closed for degenerate source quads before solver cases are queued. Passing `mesh/quality.json` means the starter quad-strip geometry is non-degenerate under FlowLab's thresholds and within the recorded source angle-quality estimates; it does not certify production CFD readiness. OpenFOAM smoke reports now include `caseValidation.openfoamMeshReview` so the fitted starter mesh, scripted `checkMesh`, and remaining production-readiness blockers are visible without opening the case directory.

Result visualization supports a documented VTK/VTU subset:

- Legacy ASCII VTK `UNSTRUCTURED_GRID` and OpenFOAM-style surface `POLYDATA` `POLYGONS` with `POINT_DATA` or `CELL_DATA` single-component `SCALARS`, three-component `VECTORS`, and OpenFOAM-style `FIELD` arrays.
- ASCII VTU `UnstructuredGrid` with `PointData` or `CellData` `DataArray` values.
- ASCII PVD `Collection` manifests are indexed as time-series metadata. FlowLab records bounded collection summaries, validates that referenced `.vtk`/`.vtu` files stay inside the job case directory, annotates referenced artifacts with solver-declared timesteps, and still previews/renders only concrete VTK/VTU geometry files. Terminal jobs now also generate `postProcessing/flowlab_results.pvd` when multiple parseable VTK/VTU snapshots are present.
- OpenFOAM native numeric time directories such as `0.1/`, `0.2/`, and `1/` are used as a fallback when no solver VTK/VTU files are present. FlowLab reads supported ASCII `internalField` values from `U`, `p`, `p_rgh`, `T`, `alpha.*`, `rho`, and common turbulence fields, maps them onto `mesh/flowlab_mesh.vtk`, and writes bounded legacy VTK snapshots under `postProcessing/flowlabNative/time_*.vtk` so the same timeline/playback path can inspect them.
- Supported linear cell types: VTK triangle (`5`), polygon surface (`7`), quad (`9`), tetrahedron (`10`), hexahedron (`12`), wedge (`13`), and pyramid (`14`).
- Browser overlays map `pressure`, `velocity`, `temperature`, `phase_fraction`, and `residuals` fields to the 2D canvas. `velocity` vectors are rendered as magnitude coloring plus arrows. Cell fields are used when point fields are absent.
- The field viewer shows min/max values, mean/spread/percentile statistics, and conservative inferred units for the active loaded field, supports a timestep/snapshot slider with playback, lists and filters loaded point/cell scalar and vector fields by name/location/kind/unit, lets pinned vector fields switch between magnitude and X/Y/Z components, offers Turbo/Viridis/Thermal/Grayscale result colormaps, shows a compact cross-snapshot mean trend and coverage summary for the active field/component, exports the active field timeline as CSV, and lets you pin an exact field even when point and cell arrays share a name.
- Empty-canvas probing samples the nearest rendered mesh point or cell center for the currently pinned field or overlay fallback.

A deterministic fixture is available at `public/fixtures/venturi-result.vtk`; use **Load fixture result** in the advanced solver panel to visualize fields without a long CFD solve. The top toolbar can export project JSON, instant/result JSON, and the loaded VTK/VTU source file when available.

When a queued OpenFOAM, SU2, Code_Saturne, or MuJoCo job writes ASCII `.vtk` or `.vtu` files under the generated case directory, the job result records small result files and the UI ingests them as additional timeline snapshots during job polling. When OpenFOAM exits without VTK/VTU output, FlowLab attempts the native time-directory conversion above; malformed or missing fields are surfaced as skipped result metadata, and an exit-zero OpenFOAM job is not marked complete unless at least one parseable field snapshot is collected and summarized. Parseable result artifacts and bounded result-index entries now also include `fieldSummary` metadata with schema `flowlab.result_field_summary.v1`, point/cell counts, field locations, scalar/vector-magnitude kind, tuple counts, and min/max/mean/stdDev/P50/P95 values, so users can inspect available result fields before loading every file into the canvas. Terminal jobs write `postProcessing/flowlab_results.pvd` for multi-frame VTK/VTU outputs and `postProcessing/flowlab_run_artifacts.json` with result, diagnostic, mesh-quality, patch-metric, collection, and optional post-processing evidence. The result index also recognizes `.pvd` collection manifests with schema `flowlab.pvd_collection.v1`, summarizes referenced timesteps, rejects unsafe references, annotates concrete `.vtk`/`.vtu` artifacts with PVD times when available, and still requires concrete VTK/VTU geometry for preview/rendering. The bottom dock turns those snapshots into a clickable field trend, field coverage summary, and active-field histogram so users can compare the selected field's availability, distribution, mean, and maxima across result times while playback is paused or running; pinned vector fields can be analyzed as magnitude or as X/Y/Z components, and that component choice is shared by the canvas color scale, histogram, trend, coverage, and probe readout. The bottom dock also includes a solver diagnostics panel backed by `logSummary` and `diagnosticSummary`, showing latest solver time/iteration, parsed residual convergence ratios, bounded diagnostic table summaries, and log warning/error excerpts without occupying the center canvas. Playback includes previous/next stepping, speed control, loop/hold behavior, and durable pinned-field handling that marks missing fields instead of silently falling back to another array. OpenFOAM whole-case `UNSTRUCTURED_GRID` results and patch-level `POLYDATA` files such as inlet, outlet, and wall exports can both be loaded when they use the supported ASCII subset. Snapshot ordering uses parsed solver times when available; for OpenFOAM logs that include `Time = 0`, filenames such as `case_50.vtk` map to the matching zero-based solver timestep instead of an arbitrary sequence index; indexed PVD collections provide solver-declared ordering for result-artifact sequences. Code_Saturne's starter EnSight Gold fluid-domain output is converted into a bounded legacy VTK hexahedral result with point-averaged scalar/vector fields when the expected `RESULTS_FLUID_DOMAIN.case` layout is present. Running jobs publish progressive snapshots periodically and immediately after recognizable residual lines; terminal jobs publish a final non-progressive snapshot. Large, non-text, and overflow result files are reported as skipped metadata rather than embedded into the job response, and the case summary separates embedded field files from skipped field entries. The API also exposes bounded `GET /api/jobs/{jobId}/artifacts?kind=result&limit=...`, `GET /api/jobs/{jobId}/artifact?path=...`, `GET /api/jobs/{jobId}/artifact/preview?path=...&pointLimit=...&cellLimit=...`, and `GET /api/jobs/{jobId}/artifact/chunk?path=...&offset=...&limit=...` readers for supported result or diagnostic files inside the materialized job case directory. The preview endpoint returns schema `flowlab.result_preview.v1` with sampled cells, referenced points, field summaries, and sampled point/cell field values for supported ASCII VTK/VTU artifacts under the preview byte cap. The desktop can index concrete result paths, preview one server-thinned artifact or append indexed preview sequences in 24-artifact pages as timeline snapshots, inspect field metadata for small parseable files, then load a skipped or omitted oversized UTF-8 VTK/VTU field via chunks when the artifact stays below the browser parse cap.
FlowLab treats files under `mesh/` as inspection/export artifacts, not solver results, so only solver/post-processing VTK/VTU files are collected into completed job result snapshots.
Small text diagnostics under `postProcessing/`, Code_Saturne `RESU/`, and MuJoCo `outputs/` run folders are collected separately as `diagnosticFiles`. FlowLab collects bounded `.dat`, `.csv`, `.txt`, `.log`, and `.json` files plus SU2 top-level convergence files such as `history.csv` and Code_Saturne extensionless run files such as `listing` and `error`. It also emits a conservative `diagnosticSummary` for recognizable numeric tables, SU2 residual histories, OpenFOAM nested wall-force rows, MuJoCo `outputs/summary.json` telemetry, and Code_Saturne fatal boundary-condition excerpts, recording the diagnostic kind, columns or excerpts, row count when applicable, and latest numeric row. Optional server-side visual postprocessing writes `postProcessing/flowlab_visual_postprocessing.json`; if PyVista is installed it may render bounded PNG thumbnails under `postProcessing/flowlabThumbnails/`, and if only ParaView `pvpython` is detectable it records availability without invoking a heavyweight render automatically. For OpenFOAM, FlowLab also scans `postProcessing/patchFlowRate`, Foundation-style `flowRatePatch`, `postProcessing/patchAverage`, `postProcessing/forces` or `wallForces`, `postProcessing/wallShearStress`, and pressure probe tables into `patchMetrics` with inlet/outlet flow balance, pressure-drop pairs, wall-shear min/mean/max, integrated force and moment vectors, pressure-probe spans, sources, and warnings when expected patch diagnostics are missing or malformed. Terminal OpenFOAM jobs write `postProcessing/flowlab_diagnostics_acceptance.json`, which records generated function objects, observed output paths, missing diagnostics, command exit codes, parser status, normalized `patchMetrics`, and the completion gate. Exit-zero OpenFOAM jobs are marked failed when strict generated diagnostics are missing; guarded/preflight modes can record partial diagnostics without claiming a full solve. These capture solver outputs such as OpenFOAM residuals, patch metrics, probes, field min/max tables, force histories, MuJoCo fluid-force summaries, and Code_Saturne setup failures without mixing them into field visualization snapshots.
Completed and failed solver jobs also include a normalized `logSummary` parsed from captured stdout when recognizable solver text is present. The summary records line counts, last log lines, OpenFOAM time/residual fields, parsed OpenFOAM global `checkMesh` counts and quality metrics when present, region-scoped OpenFOAM `checkMesh` metrics under `checkMeshRegions` for multi-region runs, SU2 iteration-table progress, Code_Saturne iteration/residual lines, MuJoCo step counts, and warning/error excerpts. Unknown log formats are preserved as raw logs without fabricated convergence claims.
OpenFOAM jobs also pass through conservative quality gates: before `Allrun`, the job runner executes the native mesh stage described above and blocks before solve launch when `surfaceFeatureExtract`, `snappyHexMesh`, or `checkMesh` are unavailable or when `checkMesh` reports failed checks. Generated `Allrun` scripts must still run `checkMesh -allGeometry -allTopology` before the solver, and fatal OpenFOAM log lines, nonzero failed-checkMesh counts, NaN solver output, NaN result fields, or absence of any parseable VTK/VTU/native time-directory field snapshot mark the job `failed` even if a shell script returns exit code `0` after post-processing. This keeps solve-through evidence tied to valid mesh and numeric fields rather than command completion alone.
OpenFOAM mesh QA evidence is also exposed through `GET /api/jobs/{jobId}/mesh-quality` and through `job.result.meshQuality` during job polling. The response is bounded to the generated production acceptance artifact plus the native mesh logs `log.surfaceFeatureExtract`, `log.blockMesh`, `log.snappyHexMesh`, `log.checkMesh`, and `log.yPlus`. The desktop bottom dock includes a Mesh QA panel for native stage status, command exit codes, checkMesh metrics, y-plus/wall-distance evidence, blocking reasons, and the production readiness badge.
The mesh-quality gate is summarized in [docs/mesh-quality-gate-2026-06-14.md](docs/mesh-quality-gate-2026-06-14.md).
The mesh-control and OpenFOAM checkMesh upgrade is summarized in [docs/mesh-controls-checkmesh-2026-06-14.md](docs/mesh-controls-checkmesh-2026-06-14.md).
The cell-data result parsing upgrade is summarized in [docs/result-cell-data-support-2026-06-14.md](docs/result-cell-data-support-2026-06-14.md).
The loaded-field inventory and explicit point/cell field selection upgrade is summarized in [docs/result-field-inventory-2026-06-14.md](docs/result-field-inventory-2026-06-14.md).
Large-result skip/overflow reporting is summarized in [docs/result-large-file-reporting-2026-06-15.md](docs/result-large-file-reporting-2026-06-15.md).

Benchmark fixture scaffolding lives under `benchmarks/` and is summarized in
[docs/benchmark-fixtures.md](docs/benchmark-fixtures.md). These fixtures define
expected inputs, required patches, required fields, diagnostics, and acceptance
criteria for cases such as straight pipe, Venturi, heated channel, and optional
lid-driven cavity/nozzle placeholders. They are all `pending-real-run` until
Track A promotes real solver logs, fields, diagnostics, and mesh-quality
evidence; they are not passed CFD benchmarks.

Reference-case scaffolding lives under `reference_cases/registry.json`. The API exposes `GET /api/reference-cases` and `POST /api/reference-cases/{caseId}/import-plan` for SU2 TestCases and OpenFOAM tutorial handoffs. Import plans record upstream source URLs, expected config/mesh files, required user review steps, and limitations; FlowLab does not auto-download or promote third-party tutorial assets into production evidence without a real local run and captured artifacts.

OpenFOAM, SU2, Code_Saturne, and MuJoCo cases include the mesh bundle for inspection. SU2 case generation references `mesh/flowlab_mesh.su2`; Code_Saturne case generation copies the Gmsh mesh to `MESH/flowlab_mesh.msh` and references it from `DATA/cs_user_scripts.py`; MuJoCo case generation writes `model.xml` and `run_mujoco.py`. Supported starter cases are marked blocked when the solver runtime is unavailable, a required Python module is unavailable, or mesh generation fails closed. Unsupported/export-only advanced modes are also marked blocked by design when FlowLab can generate review artifacts but cannot honestly generate the requested native physics. Queued job directories are materialized under `runtime/flowlab/jobs/`.
Every generated solver case includes `flowlab_case_manifest.json` with the solver mode, run command, file count, and SHA-256/size for each generated file. The job runner validates this manifest before resolving Docker/native execution, and the OpenFOAM smoke report includes it as case-bundle evidence.
Before a generated case is allowed to resolve Docker/native execution, the backend validates required case files, run commands, mesh references, OpenFOAM/SU2/Code_Saturne `mesh/quality.json` evidence with source non-orthogonality/skewness metrics and thresholds, boundary-layer sizing, reviewed STL ASCII sanity and preview metadata, reviewed STL boundary-tag completeness for inlet/outlet/wall roles, production-mesh readiness-plan schema, production-mesh acceptance checklist schema, OpenFOAM boundary patches, field `boundaryField` blocks, SU2 mode preset schema/support consistency, SU2 all-mode capability matrix coverage, and mode-specific files such as VOF phase fractions, cavitation dictionaries, thermal density fields, and water-hammer handoff metadata. Validation failures are blocked before a solver process is launched.

OpenFOAM cases also include a run-ready starter layout:

- `Allrun`: uses fitted `constant/polyMesh` when graph geometry is available; otherwise runs the `blockMesh` fallback, then requires `checkMesh -allGeometry -allTopology` before `foamRun -solver <mode>` and `foamToVTK -ascii -latestTime` when available.
- `constant/polyMesh/*`: a one-layer hexahedral OpenFOAM polyMesh extruded from the port-aware pipe/Venturi quad strip, with `inlet`, `outlet`, `walls`, and `frontAndBack` patches.
- `system/blockMeshDict`: a rectangular 2D blockMesh fallback retained for dependency checks when graph geometry cannot be exported.
- `system/controlDict` and `system/functions`: `foamRun` controls plus residual, centerline probe, `patchFlowRate` on inlet/outlet patches, `patchAverage` pressure on inlet/outlet patches, `wallShearStress` on wall patches, `forces` on wall/bend/valve wall patches, and pressure-only probe sampling when FlowLab probe nodes exist. Patch names come from reviewed geometry surfaces when present; otherwise the starter case uses `inlet`, `outlet`, and `walls`. The job runner optionally probes `foamVersion`; OpenCFD-style runtimes keep inline `controlDict` functions, while detected Foundation-style runtimes can switch `controlDict` to include `system/functions`.
- Run mode: the default incompressible run is a short transient starter (`endTime 0.05`) intended as a quick smoke, not a converged solve, so it does not produce a fully-developed pressure drop. Set `solver.runMode: "steady"` on an `incompressible-navier-stokes` project to generate a steady-state SIMPLE run instead (`ddtSchemes steadyState`, a `SIMPLE` block with `residualControl`, and relaxation factors, with `controlDict` iterating to convergence). This is honored only for the steady-capable incompressible mode; inherently transient modes (water-hammer, multiphase VOF, cavitation, compressible flow) always keep their transient controls. A converged steady run populates `patchMetrics.pressureDrops` from the per-patch `patchAverage` pressure output.
- Mesh mode: `solver.meshMode` selects the OpenFOAM topology. `planar-2d` (default) is the one-cell-thick planar strip. `axisymmetric` fail-closed compiles one straight, collinear, non-branching circular source-to-sink path—including continuous pipe, Venturi, contraction, expansion, and nozzle edges—into a conformal multi-block **wedge** with `front`/`back` wedge patches and a collapsed `axis`. The fitted planar polyMesh and `snappyHexMesh` stage are skipped. Unsupported physics, branches, non-collinear paths, non-circular sections, and diameter discontinuities are rejected rather than silently falling back. Pre-solve VTK/VTU inspection now uses a non-planar blockMesh-equivalent wedge; the original 2D editor strip is retained separately as `flowlab_source_strip`. Exact `axisymmetricAxialCells`/`axisymmetricRadialCells` controls and the opt-in periodic `axisymmetricBenchmark` contract freeze the product-path three-grid straight-pipe candidate. The governed runner can retain all three JobManager levels and create a content-hashed read-only candidate package, but it cannot promote the fixture; GCI/conservation/error gates and controlled independent review remain mandatory. See [docs/openfoam-axisymmetric-wedge-2026-07-22.md](docs/openfoam-axisymmetric-wedge-2026-07-22.md).
- `full-ogrid` is a separate experimental mode for exactly one steady, incompressible, laminar, constant-diameter circular source-to-sink pipe. It generates a full-360 five-block all-hex volume with a center block, four conformal circumferential wall blocks, internal interfaces, and only `inlet`, `outlet`, and `walls` boundary patches. Axial, annular-radial, circumferential, and core-side counts are explicit; unsupported topology or physics fails closed. Preview VTK/VTU and solved fields use the genuine XYZ volume, and the cinema renderer/prober operate on its exterior faces and owning hexahedra. The first frozen campaign stopped at a mandatory coarse solver-convergence gate before medium/fine, so GCI is unqualified and no benchmark, validation, or promotion claim exists. See [docs/non-axisymmetric-o-grid-status-2026-07-24.md](docs/non-axisymmetric-o-grid-status-2026-07-24.md) and the [controlled-review handoff](docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_2026-07-24.md).
- `constant/flowlab_patch_metrics.json`: the generated diagnostics contract for OpenFOAM patch metrics. It records the inlet/outlet/wall patch plan, pressure probe locations, and required function objects; the job runner validates this file and `system/controlDict` before resolving Docker/native execution.
- `constant/flowlab_openfoam_function_objects.json` and terminal `postProcessing/flowlab_diagnostics_acceptance.json`: runtime-style metadata and native diagnostics acceptance evidence. The tiny OpenFOAM smoke case generated by `openfoam_diagnostics_smoke_case()` includes a pressure probe and uses the same generated contract, so mocked or real native smoke runs must produce patch flow, patch pressure average, wall shear, wall force, and probe outputs before FlowLab reports complete diagnostics.
- `0/U`, `0/p`, `0/p_rgh`, `0/T`: basic inlet/outlet/wall/frontAndBack field boundary conditions.
- `constant/transportProperties`, `constant/physicalProperties`, `constant/turbulenceProperties`, `constant/momentumTransport`, and `constant/g`: starter material, turbulence, and gravity dictionaries.
- Heat-transfer and compressible modes add `0/rho` and `constant/thermophysicalProperties`; heat-transfer uses a laminar liquid-H2O starter while compressible flow uses a laminar `shockFluid` gas starter with ideal-gas density initialization and a smaller timestep for coarse-mesh stability.
- Conjugate heat-transfer now emits a real OpenFOAM v11 `foamMultiRun` starter bundle with fluid/solid region dictionaries, including `0/fluid/*`, `0/solid/T`, `constant/fluid/physicalProperties`, `constant/solid/physicalProperties`, `system/fluid/fvSchemes`, `system/fluid/fvSolution`, `system/solid/fvSchemes`, and `system/solid/fvSolution`. It also emits split `constant/fluid/polyMesh` and `constant/solid/polyMesh` directories with paired `mappedWall` starter patches, an `AllmeshCheck` script containing `checkMesh -region fluid -allGeometry -allTopology` and `checkMesh -region solid -allGeometry -allTopology`, plus `constant/flowlab_cht_interface.json` recording that per-region check plan and the `mesh/prism_layer_plan.json` native prism-layer review input. The solid region is now generated as an outward offset starter sleeve instead of duplicating the fluid strip. Runtime still fails closed because this interface mesh is not production-ready; a full CHT solve still needs CAD-quality solid topology, generated production 3D prism layers/y-plus evidence, and captured per-region mesh-quality evidence.
- Turbulent advanced modes add `0/k`, `0/omega`, `0/nut`, `0/alphat`, and a `kOmegaSST` RANS preset where the selected solver mode requires turbulence fields.
- VOF mode adds `0/alpha.water`, `0/alpha.air`, `0/rho`, `constant/phaseProperties`, per-phase `constant/physicalProperties.water` and `constant/physicalProperties.air`, surface tension, alpha compression controls, pressure-correction controls, and two-phase `rhoPhi` transport schemes.
- Cavitation mode uses `compressibleVoF` with a vapour/water phase pair: `0/alpha.vapour`, thermo-style `constant/physicalProperties.water` and `constant/physicalProperties.vapour`, `constant/thermodynamicProperties`, `constant/fvModels` with `compressible::VoFCavitation`, and a starter Schnerr-Sauer metadata dictionary.
- Water-hammer mode records a computed Tier 1 Method-of-Characteristics handoff in `constant/waterHammerPreview.json`, writes `constant/waterHammerWaveform.csv`, and maps the dominant Joukowsky pressure rise to an OpenFOAM inlet `uniformFixedValue` pressure table in `0/p`. This is a transient pressure-wave boundary preview, not a fully coupled CFD water-hammer solver.

This is a stronger local execution template than the inspection-only skeleton, but it is still a FlowLab v1 starter case: the OpenFOAM volume mesh is now fitted to the exported pipe/Venturi strip, yet remains a thin one-layer extrusion rather than a CAD-quality boundary-layer mesh. A true production CFD case still needs mesh-quality inspection and domain-specific boundary refinement.

SU2 cases include a native starter layout:

- `case.cfg`: points to `mesh/flowlab_mesh.su2`, selects the SU2 solver, and assigns inlet/outlet/wall markers from the exported mesh.
- `flowlab_su2_mode_preset.json`: explicit FlowLab-to-SU2 support manifest with schema, advanced mode, starter support level, requested-physics resolution, required capabilities, readiness checks, blocking reasons, blocked/manual models, manual setup requirements, native setup plan, expected primary fields, fluid inputs, mesh assumptions, and `productionReady: false`.
- `flowlab_su2_native_setup_checklist.json`: generated native setup checklist derived from the mode preset, listing generated files, readiness items, expected fields, unresolved native modules, manual setup actions, and `productionReady: false`.
- `flowlab_su2_capability_matrix.json`: generated adapter capability matrix covering all FlowLab advanced modes. It marks starter-supported single-zone modes, blocked export-only modes, handoff modes, expected fields, manual native modules, readiness counts, active-mode consistency with the preset, and `productionReady: false`.
- `flowlab_su2_native_config_template.cfg`: generated only for blocked/export-only SU2 modes. It is a guarded review template with `FLOWLAB_TEMPLATE_ONLY= YES` and `FLOWLAB_UNSUPPORTED_MODE= YES`, plus required native capabilities, manual modules, expected fields, and the starter marker/mesh context from `case.cfg`. It is intentionally not runnable until a user replaces the placeholder setup with reviewed native SU2 physics.
- `flowlab_su2_advanced_preflight.json`: generated only for blocked/export-only SU2 modes. It ties the guarded template, handoff artifacts, unresolved native modules/actions, expected primary fields, and blocking reasons into one machine-readable preflight record with `productionReady: false` and `nativeSu2Ready: false`.
- Incompressible mode uses `INC_NAVIER_STOKES`, constant-density water-like properties, velocity inlet markers, and gauge-pressure outlet markers.
- Compressible mode uses `NAVIER_STOKES`, standard-air properties, total-condition inlet markers, pressure outlet markers, and heat-flux wall markers.
- Heat-transfer mode uses `INC_NAVIER_STOKES` with `INC_ENERGY_EQUATION= YES`, isothermal wall markers, water-like heat capacity, and thermal conductivity.
- Water-hammer export-only mode additionally writes `flowlab_su2_water_hammer_handoff.json` and `flowlab_su2_water_hammer_waveform.csv`. These carry the Tier 1 MOC pressure-wave preview into SU2 review artifacts, and the generated checklist lists them as generated files, but FlowLab does not map them into a native SU2 transient compressible-liquid boundary setup.
- Conjugate heat-transfer export-only mode additionally writes `flowlab_su2_cht_handoff.json`. This records the required fluid zone, missing solid-zone mesh, manual fluid-solid interface coupling, solid material inputs, expected CHT fields, and native SU2 MULTIZONE setup actions. It is a review handoff, not a generated SU2 CHT case.
- Multiphase and cavitation export-only modes additionally write `flowlab_su2_multiphase_handoff.json` or `flowlab_su2_cavitation_handoff.json`. These record liquid/gas or liquid/vapour phase intent, manual interface setup, expected phase fields, cavitation saturation pressure when relevant, and unresolved native SU2 phase-model actions.
- Rigid-body-fluid-force export-only mode additionally writes `flowlab_su2_rigid_body_handoff.json`. This records the intended moving-body/FSI coupling, MuJoCo as the current approximate rigid-body sandbox, manual motion setup, expected force/moment fields, and unresolved native SU2 dynamic-mesh or FSI actions.
- Multiphase, cavitation, water-hammer, conjugate heat, and rigid-body-fluid-force modes are exported as blocked SU2 cases with `FLOWLAB_UNSUPPORTED_MODE= YES` plus failing readiness checks, blocked/manual model details, `nativeSetupPlan.manualNativeModules`, `resultExpectations.expectedPrimaryFields`, the guarded native config review template, generated checklist `actionItems`, handoff artifacts, and `flowlab_su2_advanced_preflight.json` until FlowLab can generate the required multi-zone, multiphase, transient, or coupled setup. These modes have passing handoff-export readiness items, but native multiphase/cavitation models, transient-liquid/MOC-boundary mapping, pipe-wall elasticity, solid-zone mesh, thermal-interface coupling, dynamic mesh, and FSI setup remain unresolved.

SU2 is best treated here as a starter solver export for single-zone compressible, incompressible, and thermal workflows. The current FlowLab mesh is still a simple 2D quad strip and should be replaced by a production mesh before interpreting high-fidelity results.

Code_Saturne cases include a native starter layout:

- `DATA/setup.xml`: starter analysis controls, fluid properties, turbulence/thermal toggles, an `all_cells` volume zone, and Code_Saturne boundary localization nodes for deterministic inlet, outlet, and wall mesh groups.
- `DATA/flowlab_physics_preset.json`: explicit FlowLab-to-Code_Saturne physics map with support level, requested physics, enabled starter models, blocked/manual native models, readiness checks, manual setup requirements, native setup plan, expected primary fields/result conversion, fluid inputs, and `productionReady: false`.
- `DATA/flowlab_native_setup_checklist.json`: generated native setup checklist with files FlowLab generated, unresolved Code_Saturne native modules/action items, readiness items, and expected primary result fields.
- `DATA/flowlab_code_saturne_capability_matrix.json`: generated adapter capability matrix covering all FlowLab advanced modes. It marks starter-supported modes, unresolved surrogate/handoff/metadata-only modes, expected fields, handoff artifacts, manual native modules, readiness counts, and blocking reasons without claiming unresolved physics is runnable.
- `DATA/flowlab_native_physics_review.py`: generated for unresolved Code_Saturne physics modes. It is a guarded review artifact with `FLOWLAB_CODE_SATURNE_REVIEW_TEMPLATE = True`, `FLOWLAB_REQUESTED_PHYSICS_RESOLVED = False`, and `FLOWLAB_PRODUCTION_READY = False`; it summarizes required native modules, manual setup requirements, handoff artifacts, and expected fields, but is not imported by the starter `code_saturne run` path.
- Heat-transfer mode records a `thermalStarter` and `thermalBoundaryPlan` around Code_Saturne's `temperature_celsius` starter scalar, including inlet/initial temperature, adiabatic wall placeholder intent, expected temperature output, and explicit exclusions for CHT, radiation, buoyancy validation, and phase change. This is still a passive thermal-scalar starter, not production thermal validation.
- Compressible-flow mode exports `DATA/flowlab_compressible_handoff.json`, recording the pressure-based starter surrogate, required native compressible-flow modules, equation-of-state and energy-equation setup, total/static boundary-condition review, acoustic timestep/CFL controls, and expected density, temperature, and Mach fields. This is a review artifact, not a generated native compressible Code_Saturne solve.
- Multiphase VOF and cavitation modes export `DATA/flowlab_multiphase_handoff.json` or `DATA/flowlab_cavitation_handoff.json`, recording phase material placeholders, required native interface/phase-change setup, expected phase fields, boundedness review requirements, and unresolved native Code_Saturne actions. These are review artifacts, not generated native phase-resolved solves.
- Water-hammer mode also exports `DATA/flowlab_water_hammer_handoff.json` and `DATA/flowlab_water_hammer_waveform.csv`, carrying the Tier 1 Method-of-Characteristics pressure-wave preview into Code_Saturne review artifacts. These files are not automatically wired into a native transient pressure boundary condition; compressible-liquid and pipe-wall elasticity setup remain manual.
- Conjugate heat-transfer mode exports `DATA/flowlab_cht_handoff.json`, recording the starter fluid domain, missing solid-domain mesh, manual fluid-solid interface coupling, solid material inputs, expected CHT fields, and native Code_Saturne setup actions. This is a review artifact, not a generated coupled CHT solve.
- Rigid-body-fluid-force mode exports `DATA/flowlab_rigid_body_handoff.json`, recording the intended moving-body/FSI coupling, MuJoCo as the current approximate rigid-body sandbox, manual mesh-motion/body-kinematics setup, expected force/moment/displacement fields, and unresolved native Code_Saturne dynamic-mesh or co-simulation actions.
- `DATA/run.cfg`: points the run at `setup.xml` with a deterministic `flowlab` run id.
- `DATA/cs_user_scripts.py`: pins `MESH/flowlab_mesh.msh` as the mesh input.
- `DATA/cs_user_physics.py`: Python-readable copy of the physics preset for users who want to add native Code_Saturne hooks.
- `SRC/cs_user_boundary_conditions.f90`: assigns a starter inlet, outlet, and default wall set from deterministic mesh extents.
- `MESH/flowlab_mesh.msh`: Gmsh 2.2 ASCII export with inlet, outlet, wall, and fluid physical groups.

This adapter is no longer an execution skeleton, but it is still a starter workflow. Heat-transfer is represented as a starter thermal scalar; compressible flow remains a pressure-based surrogate but now exports an explicit compressible native-setup handoff with unresolved module, thermodynamic, boundary, and field-verification requirements; multiphase, cavitation, water-hammer, CHT, and rigid-body coupling also export handoff artifacts for native setup review. Each advanced preset now records `nativeSetupPlan.manualNativeModules`, `resultExpectations.expectedPrimaryFields`, a generated native setup checklist, and a structured `turbulencePlan` that distinguishes laminar starters from k-epsilon RANS starters while keeping wall-function/y-plus evidence, LES, DNS, transition, and higher-fidelity turbulence work unresolved. For unresolved modes, FlowLab emits a guarded native physics review script and marks the generated case blocked with no `code_saturne run` command, so users can see which native Code_Saturne modules, manual actions, output fields, and handoff files must be added before the requested physics is truly resolved without accidentally launching a starter surrogate as advanced CFD. The capability matrix gives the same review at whole-adapter scope so a generated case shows which modes are starter-supported and which still require native Code_Saturne work. Real industrial Code_Saturne use should review the generated XML, physical groups, preset metadata, capability matrix, checklist, native physics review script, handoff artifacts when present, turbulence assumptions, and mesh quality before relying on results.

MuJoCo cases include a native rigid-body fluid-force sandbox:

- `model.xml`: an MJCF model with water-like density/viscosity and an ellipsoid-based fluid-force box body.
- `run_mujoco.py`: loads the model with the official Python bindings, advances the simulation, and writes `outputs/mujoco_fluid_force_0001.vtk` plus `outputs/summary.json`.
- `README.md`: states the CFD limitation and the local run command.

This is useful for moving valve plates, obstacles, swimmers, and pump-like mechanisms with approximate fluid drag/lift. It is not a substitute for OpenFOAM, SU2, or Code_Saturne field solves.

## API Smoke

With `npm run server` running, first check solver runtime readiness:

```bash
curl -sS http://127.0.0.1:8787/api/runtime | python3 -m json.tool
```

Each entry reports whether that tier is runnable, which execution path would be used (`browser`, `docker`, `native`, or `none`), and the concrete blockers. A real advanced CFD solve-through requires at least one advanced backend such as OpenFOAM, SU2, Code_Saturne, or MuJoCo to report `runnable: true`, followed by a queued job reaching `complete` with solver logs and result files. If every advanced backend reports `runnable: false`, FlowLab can still generate cases and run the instant browser solver, but it cannot honestly claim an advanced local CFD solve has completed on that machine.

To run the same local OpenFOAM solve-through path without starting the API server:

```bash
npm run smoke:openfoam -- --output /tmp/flowlab-openfoam-smoke.json
```

Both this desktop execution path and the straight-pipe scientific runner default
to the repository-pinned `flowlab/openfoam11-gmsh:2026-07-13` image. Set
`FLOWLAB_OPENFOAM_IMAGE` to test a replacement image through both paths without
changing source; do not treat results from different image digests as one
scientific campaign.

This generates a small port-aware Venturi project, materializes an OpenFOAM case, queues it through the same job runner, polls to terminal state, and writes a JSON evidence report. A real OpenFOAM solve-through is proven only when the report has `completed: true`, `status: complete`, `exitCode: 0`, solver logs without fatal/NaN quality failures, and collected solver-produced VTK/VTU result artifacts. If Docker or `foamRun` is missing, the command exits non-zero and writes a blocked report with the dependency reason.

The current local Docker solve-through evidence is summarized in [docs/openfoam-docker-smoke-2026-06-12.md](docs/openfoam-docker-smoke-2026-06-12.md). The advanced-mode Docker matrix for heat transfer, compressible flow, multiphase VOF, cavitation, and the original CHT guardrail is summarized in [docs/openfoam-advanced-mode-matrix-2026-06-12.md](docs/openfoam-advanced-mode-matrix-2026-06-12.md). The updated CHT multi-region generation evidence is summarized in [docs/openfoam-cht-multiregion-generation-2026-06-14.md](docs/openfoam-cht-multiregion-generation-2026-06-14.md). The latest water-hammer handoff Docker smoke evidence is summarized in [docs/openfoam-water-hammer-smoke-2026-06-15.md](docs/openfoam-water-hammer-smoke-2026-06-15.md).

To validate the local heat-transfer template specifically:

```bash
npm run smoke:openfoam -- --advanced-mode heat-transfer --output /tmp/flowlab-openfoam-heat-smoke.json
```

To inspect the current conjugate heat-transfer generation guardrail:

```bash
npm run smoke:openfoam -- --advanced-mode conjugate-heat-transfer --output /tmp/flowlab-openfoam-cht-smoke.json
```

This should currently return `status: blocked` rather than a completed CHT solve. That is intentional: FlowLab now generates region-scoped CHT dictionaries, paired mapped-wall starter meshes, an outward offset solid sleeve, an `AllmeshCheck` per-region `checkMesh` plan, a source mesh quality summary, and `caseValidation.chtInterface` readiness evidence. If Docker or native `checkMesh` is available, the job runner may execute only `bash AllmeshCheck` as `execution: preflight`, capture `logSummary.checkMeshRegions.fluid` and `.solid`, then still report `blocked` because `foamMultiRun` remains gated by `productionReady: false`. The readiness report currently passes multi-region dictionary, mapped-wall pairing, source mesh quality, non-overlapping starter-sleeve, and region-check plan checks, but fails generated production prism-layer/y-plus evidence and production-ready interface evidence.

To validate the local compressible-flow template specifically:

```bash
npm run smoke:openfoam -- --advanced-mode compressible-flow --output /tmp/flowlab-openfoam-compressible-smoke.json
```

To validate the local multiphase VOF template specifically:

```bash
npm run smoke:openfoam -- --advanced-mode multiphase-vof --output /tmp/flowlab-openfoam-vof-smoke.json
```

To validate the local cavitation template specifically:

```bash
npm run smoke:openfoam -- --advanced-mode cavitation --output /tmp/flowlab-openfoam-cavitation-smoke.json
```

To validate the current water-hammer handoff template specifically:

```bash
npm run smoke:openfoam -- --advanced-mode water-hammer --output /tmp/flowlab-openfoam-water-hammer-smoke.json
```

This runs the OpenFOAM starter solve and verifies the generated case preserves
the water-hammer handoff artifacts. The smoke report includes
`caseValidation.waterHammerHandoff` with schema
`flowlab.water_hammer_handoff.v1`, coupling mode
`pressure-wave-boundary-preview`, computed wave speed, Joukowsky pressure rise,
kinematic pressure rise, critical closure time, closure/settle timing, waveform
start/peak/end rows, waveform row count, and the OpenFOAM `0/p` pressure-table
mapping. Current generated cases also write the waveform CSV and OpenFOAM inlet
pressure table into the case bundle. The latest local Docker run completed with
`status: complete`, `exitCode: 0`, solver-produced VTK outputs, residual and
force diagnostics, result field summaries for whole-case and patch-level VTK
files, and passing `checkMesh` evidence from the updated starter mesh
(`failedChecks: 0`, `cells: 39`, `faces: 172`, `points: 112`). The smoke report
also includes a generated case manifest with `mesh/native_meshing_plan.json`,
OpenFOAM/SU2/Code_Saturne native meshing handoffs, the review-only Gmsh
production scaffold, OpenFOAM native mesh preflight, and
`mesh/production_mesh_acceptance.json`; production mesh acceptance remains
blocked. This is still not a claim of full CFD pressure-wave coupling.

To run the same local MuJoCo rigid-body fluid-force path:

```bash
FLOWLAB_MUJOCO_PYTHON=/tmp/flowlab-mujoco-venv/bin/python npm run smoke:mujoco -- --output /tmp/flowlab-mujoco-smoke.json
```

This materializes the MJCF sandbox, runs `run_mujoco.py` with the configured Python interpreter, and proves the path only when the report has `completed: true`, `status: complete`, `exitCode: 0`, and `outputs/mujoco_fluid_force_0001.vtk` in `resultFiles`.

The current local native MuJoCo solve-through evidence is summarized in [docs/mujoco-native-smoke-2026-06-12.md](docs/mujoco-native-smoke-2026-06-12.md).

To run the Code_Saturne execution path and capture its `RESU` diagnostics:

```bash
FLOWLAB_CODE_SATURNE_IMAGE=flowlab-code-saturne:local npm run smoke:code-saturne -- --output /tmp/flowlab-code-saturne-smoke.json
```

This materializes the `DATA`/`SRC`/`MESH` case, queues `code_saturne run`, and writes the same evidence report shape as the other smoke commands. A completed Code_Saturne run requires `completed: true`, `status: complete`, `exitCode: 0`, and solver-produced `RESU` diagnostics such as `residuals.csv`, `summary`, and checkpoint/postprocessing files. Failed runs still capture bounded `RESU` files such as `listing`, `error`, and monitoring tables in `diagnosticFiles` and `diagnosticSummary`, which is the expected way to inspect boundary-condition or preprocessing failures.

The current local Docker Code_Saturne solve-through evidence is summarized in [docs/code-saturne-docker-smoke-2026-06-12.md](docs/code-saturne-docker-smoke-2026-06-12.md). Code_Saturne writes EnSight Gold postprocessing files on disk; FlowLab converts the starter fluid-domain EnSight fields into `flowlab_code_saturne_fluid.vtk` for visualization and also captures bounded `RESU` diagnostics in job results. The generated physics preset now includes `supportedByAdapter`, `requestedPhysicsResolved`, `readinessChecks`, `blockingReasons`, `manualSetupRequirements`, `setupXmlModels`, and `resultExpectations` so queued starter runs remain distinguishable from unresolved advanced Code_Saturne physics.

Manual Docker checks:

```bash
docker run --rm --platform linux/amd64 --entrypoint /bin/bash flowlab/openfoam11-gmsh:2026-07-13 -lc 'source /opt/openfoam11/etc/bashrc && foamRun -help'
python3 -c 'import mujoco; print(mujoco.__version__)'
```

For MuJoCo, keep the package in a project-specific Python environment if the
system Python should stay untouched:

```bash
python3 -m venv /tmp/flowlab-mujoco-venv
/tmp/flowlab-mujoco-venv/bin/python -m pip install mujoco
export FLOWLAB_MUJOCO_PYTHON=/tmp/flowlab-mujoco-venv/bin/python
```

When `FLOWLAB_MUJOCO_PYTHON` is set, FlowLab checks for the `mujoco` module and
runs `run_mujoco.py` using that interpreter instead of the server's Python.

For SU2, install native `SU2_CFD` on `PATH`, or download an official SU2 binary release and point FlowLab at it:

```bash
mkdir -p /tmp/flowlab-su2
curl -fL --retry 2 -o /tmp/flowlab-su2/SU2-v8.5.0-linux64-omp.zip https://github.com/su2code/SU2/releases/download/v8.5.0/SU2-v8.5.0-linux64-omp.zip
unzip -q /tmp/flowlab-su2/SU2-v8.5.0-linux64-omp.zip -d /tmp/flowlab-su2
unzip -q /tmp/flowlab-su2/linux64-omp.zip -d /tmp/flowlab-su2/linux64-omp
export FLOWLAB_SU2_HOME=/tmp/flowlab-su2/linux64-omp
docker run --rm --platform linux/amd64 -v "$FLOWLAB_SU2_HOME:/opt/su2:ro" ubuntu:22.04 /bin/bash -lc 'export PATH=/opt/su2/bin:$PATH PYTHONPATH=/opt/su2/bin:$PYTHONPATH && SU2_CFD --help'
npm run smoke:su2 -- --output /tmp/flowlab-su2-smoke.json
```

When `FLOWLAB_SU2_HOME` is set and Docker is running, FlowLab mounts that binary bundle into `ubuntu:22.04` and runs `SU2_CFD case.cfg`; it does not rely on the unavailable `su2code/su2:latest` image. A completed SU2 smoke requires `completed: true`, `status: complete`, `exitCode: 0`, a generated `flowlab_su2.vtk` result file, `history.csv` residual diagnostics, and case-manifest SHA evidence for `case.cfg`, `flowlab_su2_mode_preset.json`, `flowlab_su2_native_setup_checklist.json`, and `flowlab_su2_capability_matrix.json`. On macOS, the official `SU2-v8.5.0-macos64.zip` bundle can also be placed on `PATH` for native execution.

The current local Docker SU2 solve-through evidence is summarized in [docs/su2-docker-smoke-2026-06-12.md](docs/su2-docker-smoke-2026-06-12.md).

For Code_Saturne, install native `code_saturne` on `PATH`, or build/provide a local image and point FlowLab at it:

```bash
docker build -t flowlab-code-saturne:local -<<'DOCKER'
FROM ubuntu:22.04
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends code-saturne gcc gfortran make \
  && rm -rf /var/lib/apt/lists/*
DOCKER

export FLOWLAB_CODE_SATURNE_IMAGE=flowlab-code-saturne:local
docker run --rm "$FLOWLAB_CODE_SATURNE_IMAGE" /bin/bash -lc 'code_saturne run --help'
```

Set `FLOWLAB_CODE_SATURNE_PLATFORM=linux/amd64` only when your selected image requires an explicit platform override. FlowLab does not install Code_Saturne packages during a job; the image or native command must already exist so runtime behavior is repeatable.

If any image or command fails on your machine, install/start Docker, install the corresponding native CLI (`foamRun` for OpenFOAM, `SU2_CFD` for SU2, `code_saturne` for Code_Saturne), set `FLOWLAB_SU2_HOME` to an official SU2 binary release, set `FLOWLAB_CODE_SATURNE_IMAGE` to a prepared Code_Saturne image, or install the Python `mujoco` package before expecting real solver completion.

## Validation

```bash
npm run lint
npm run build
npm test -- --run
npm run test:e2e
python3 -m pytest server/tests -q
```
