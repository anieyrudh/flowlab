# FlowLab

[![Desktop Electron candidate](https://github.com/anieyrudh/flowlab/actions/workflows/desktop-electron-candidate.yml/badge.svg)](https://github.com/anieyrudh/flowlab/actions/workflows/desktop-electron-candidate.yml)

FlowLab is a desktop application for fluid systems. You draw a system of pipes
and components. FlowLab gives you an immediate hydraulic estimate. You can then
run a full CFD case on the same model.

Your project data stays on your computer.

## Two solvers

| Solver | Speed | Gives you |
|---|---|---|
| **Instant 1D** | Immediate | Flow rate, pressure loss, Reynolds number, cavitation risk |
| **CFD** | Minutes to hours | Velocity and pressure fields from OpenFOAM |

The instant solver needs nothing but the application. The CFD solver needs
Docker Desktop.

## Get FlowLab

**Signed installers are not released yet.** Automated candidate builds pass on
macOS 13 or later on Apple Silicon, and on Windows 11 x64. But these builds are
not signed. Your operating system will refuse to open them.

Build FlowLab from source until a signed release exists. Refer to
[the installation guide](docs/INSTALLATION.md).

## Start using it

Read [the user guide](docs/USER_GUIDE.md). It shows you how to build a system,
read an estimate, and run a CFD case.

The application has four steps: **Define**, **Estimate**, **CFD**, **Inspect**.
Do them in that sequence.

## Run from source

You need Node.js 24, npm, and CPython 3.12.

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

Open a second terminal. Then start the editor:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`.

To build the desktop application, refer to
[the installation guide](docs/INSTALLATION.md#building-the-electron-application).

## What FlowLab will not do

FlowLab **fails closed**. If it cannot make an honest case, it refuses and
gives the reason. It does not guess.

**No result is validated against a physical experiment.** All CFD output is
experimental. The accuracy evidence covers steady, incompressible, laminar flow
only. It does not cover turbulence, transient flow, multiphase flow, or
compressible flow.

Refer to [benchmarks and verification status](docs/BENCHMARKS.md). That page
gives the measured results and the exact limit of each claim.

## Documentation

| Page | Content |
|---|---|
| [User guide](docs/USER_GUIDE.md) | How to use the application |
| [Installation](docs/INSTALLATION.md) | Install, build, and package |
| [Benchmarks](docs/BENCHMARKS.md) | Measured accuracy and claim limits |
| [Streamlines](docs/solver-derived-streamlines.md) | How derived flow lines work |
| [Evidence rules](docs/evidence-retention-and-git-boundary.md) | How results are retained |

## Verify a checkout

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
```

Seven tests fail if the local evidence archives are missing. That is expected.
The complete command list is in
[the installation guide](docs/INSTALLATION.md#verify-a-source-checkout).
