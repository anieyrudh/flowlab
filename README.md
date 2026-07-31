# FlowLab

[![Desktop Electron candidate](https://github.com/anieyrudh/flowlab/actions/workflows/desktop-electron-candidate.yml/badge.svg)](https://github.com/anieyrudh/flowlab/actions/workflows/desktop-electron-candidate.yml)

FlowLab is a local desktop workstation for building fluid systems, running
fast hydraulic estimates, generating CFD cases, and inspecting results without
sending project data to a hosted service.

## What it does

- Provides a visual pipe-and-component editor with instant 1D hydraulics.
- Generates deterministic cases for OpenFOAM, SU2, Code_Saturne, and MuJoCo,
  with clear dependency checks before execution.
- Supports a bounded full-360 O-grid path for steady, incompressible, laminar
  flow through a straight circular pipe.
- Loads VTK/VTU results into a true 3D viewer with XYZ surface probing and
  [solver-derived steady streamlines](docs/solver-derived-streamlines.md).
- Fails closed when geometry, topology, physics, or a solver runtime is not
  supported.

## Current status

Electron candidates build and pass automated checks on macOS 13+ Apple Silicon
and Windows 11 x64. Signed public installers have not been released yet; see
the [installation guide](docs/INSTALLATION.md) for source setup and candidate
build instructions.

The full O-grid straight-pipe campaign passed its prospective three-level
numerical-verification gates. It remains a verification candidate awaiting
controlled independent review, not a general CFD validation or promotion.
Ordinary generated CFD cases remain experimental. See
[benchmarks and verification status](docs/BENCHMARKS.md) for the measured
results and exact claim boundary.

## Run from source

Prerequisites are Node.js 24, npm, and CPython 3.12. Docker Desktop is needed
only for OpenFOAM execution.

```bash
git clone https://github.com/anieyrudh/flowlab.git
cd flowlab
npm ci
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
```

Start the local service:

```bash
npm run server
```

In a second terminal, start the editor:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`. Windows commands, Electron packaging, solver
dependencies, data locations, and troubleshooting are covered in the
[installation guide](docs/INSTALLATION.md).

## Documentation

- [Installation and desktop packaging](docs/INSTALLATION.md)
- [Benchmarks and verification status](docs/BENCHMARKS.md)
- [Electron distribution status](docs/desktop-electron-distribution-status-2026-07-24.md)
- [Full O-grid contract and review packet](docs/validation/full-ogrid-straight-pipe/REVIEW_HANDOFF_V2_2026-07-24.md)
- [Evidence retention and Git boundaries](docs/evidence-retention-and-git-boundary.md)

## Verify a checkout

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
```

The complete build, E2E, and Electron verification commands are listed in the
[installation guide](docs/INSTALLATION.md#verify-a-source-checkout).
