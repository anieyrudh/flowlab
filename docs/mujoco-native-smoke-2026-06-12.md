# MuJoCo Native Smoke Validation - 2026-06-12

FlowLab completed a real local MuJoCo rigid-body fluid-force sandbox run using an isolated Python environment on this machine.

Provisioning:

```bash
python3 -m venv /tmp/flowlab-mujoco-venv
/tmp/flowlab-mujoco-venv/bin/python -m pip install --upgrade pip
/tmp/flowlab-mujoco-venv/bin/python -m pip install mujoco
```

Command:

```bash
FLOWLAB_MUJOCO_PYTHON=/tmp/flowlab-mujoco-venv/bin/python npm run smoke:mujoco -- --runtime-root /tmp/flowlab-mujoco-real-runtime --output /tmp/flowlab-mujoco-real-smoke.json --timeout 120
```

Observed result:

- Smoke: `mujoco-rigid-body-fluid-forces-solve-through`
- Status: `complete`
- Completed: `true`
- Execution: `native`
- Exit code: `0`
- Python: `/tmp/flowlab-mujoco-venv/bin/python`
- Python module: `mujoco` available
- Steps: `120`
- Logs captured: `8`

Solver-produced result file captured in the smoke report:

- `outputs/mujoco_fluid_force_0001.vtk`

Solver diagnostic captured:

- `outputs/summary.json`

Latest parsed MuJoCo summary:

```json
{
  "steps": 120.0,
  "step": 119.0,
  "time": 0.24000000000000019,
  "passiveForceNorm": 1.8973973153845722,
  "position0": 0.1276728462183412,
  "position1": 0.0,
  "position2": -0.0782745212473797,
  "velocity0": 0.3611167433925441,
  "velocity1": 0.0,
  "velocity2": -0.5585561343340041
}
```

The generated case manifest recorded 10 generated files, including `model.xml`, `run_mujoco.py`, FlowLab mesh exports, and SHA-256/size metadata. This proves the MuJoCo adapter can materialize a FlowLab rigid-body fluid-force case, execute the native Python runner, export a VTK visualization artifact, collect `outputs/summary.json`, and return a complete job result on this machine.

Limitations:

- MuJoCo is used here for phenomenological rigid-body fluid forces, not Navier-Stokes CFD.
- This validates the rigid-body fluid-force sandbox path, not OpenFOAM/SU2/Code_Saturne field solves.
- The mesh remains FlowLab v1's port-aware inspection mesh; MuJoCo does not consume it as a CFD volume mesh.
