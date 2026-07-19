# OpenFOAM Docker Smoke Validation - 2026-06-12

FlowLab completed a real local OpenFOAM solve-through using Docker on this machine.

Command:

```bash
npm run smoke:openfoam -- --runtime-root /tmp/flowlab-openfoam-real-runtime --output /tmp/flowlab-openfoam-real-smoke.json --timeout 300
```

Observed result:

- Smoke: `openfoam-incompressible-navier-stokes-solve-through`
- Status: `complete`
- Completed: `true`
- Execution: `docker`
- Exit code: `0`
- Docker image: `openfoam/openfoam11-graphical-apps`
- Native fallback: `foamRun` was not on `PATH`; Docker was used.
- Latest OpenFOAM time: `0.05`
- Logs captured: `541`

Solver-produced result files captured in the smoke report:

- `VTK/case_50.vtk`
- `VTK/inlet/inlet_50.vtk`
- `VTK/outlet/outlet_50.vtk`
- `VTK/walls/walls_50.vtk`

Solver diagnostics captured:

- `postProcessing/residuals/0/residuals.dat`
- `postProcessing/wallForces/0/forces.dat`

Latest parsed residual row:

```json
{
  "Time": 0.05,
  "Ux": 0.000331810643,
  "Uy": 0.00830476905,
  "p": 0.00113401409
}
```

Latest parsed wall-force row includes `pressureFx: 10.9281661`.

The generated case manifest recorded 28 generated files, including fitted `constant/polyMesh/*`, field dictionaries, solver dictionaries, mesh exports, and SHA-256/size metadata. This proves the OpenFOAM Docker execution path can materialize a FlowLab case, run the solver, post-process VTK output, collect diagnostics, and return a complete job result on this machine.

Limitations:

- This validates the incompressible Navier-Stokes OpenFOAM smoke case, not every advanced mode.
- The mesh remains FlowLab v1's thin port-aware extrusion, not a production CAD-quality boundary-layer mesh.
- Conjugate heat transfer now generates a fluid/solid multi-region starter bundle, but runtime remains intentionally blocked until the generated CHT interface mesh is production-ready and has captured per-region `checkMesh` evidence.
