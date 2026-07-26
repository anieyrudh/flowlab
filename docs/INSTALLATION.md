# Installing FlowLab

FlowLab supports macOS 13+ on Apple Silicon and Windows 11 x64 through its
Electron desktop application.

## Downloading a release

Signed installers will be published on the GitHub
[Releases](https://github.com/anieyrudh/flowlab/releases) page after the
platform signing and release gates pass:

- macOS: DMG and ZIP;
- Windows: Setup EXE and ZIP.

There is not yet a signed public release. The repository's
`Desktop Electron candidate` workflow builds short-lived macOS and Windows
artifacts for engineering review, but those are not release installers.

## Running from source

Install these prerequisites:

- Git;
- Node.js 24 with npm;
- CPython 3.12; and
- Docker Desktop only if you want to execute OpenFOAM jobs.

Clone the repository and install the application dependencies:

```bash
git clone https://github.com/anieyrudh/flowlab.git
cd flowlab
npm ci
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
```

On Windows PowerShell, create and activate the environment with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server\requirements.txt
```

Start the local service:

```bash
npm run server
```

In a second terminal, start the editor:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`. FlowLab binds its development services to the
loopback interface.

## Building the Electron application

The packaged application carries its own Python backend. Prepare the exact
build environment first.

macOS:

```bash
python3.12 -m venv .venv/electron-build
.venv/electron-build/bin/python -m pip install -r desktop/electron/requirements-build.txt
export FLOWLAB_BUILD_PYTHON=.venv/electron-build/bin/python
npm ci
npm run desktop:electron:make -- --platform=darwin --arch=arm64
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv\electron-build
.\.venv\electron-build\Scripts\python.exe -m pip install -r desktop\electron\requirements-build.txt
$env:FLOWLAB_BUILD_PYTHON = ".\.venv\electron-build\Scripts\python.exe"
npm ci
npm run desktop:electron:make -- --platform=win32 --arch=x64
```

Build output is written under `out/`. Run the package and installer checks
before sharing a candidate:

```bash
npm run desktop:electron:qa -- --package out/FlowLab-darwin-arm64/FlowLab.app --mode internal
npm run desktop:electron:smoke -- --package out/FlowLab-darwin-arm64/FlowLab.app
npm run desktop:electron:qa:artifacts -- --directory out/make --platform darwin --arch arm64 --mode internal
```

Use `out/FlowLab-win32-x64`, `win32`, and `x64` for the corresponding Windows
checks. Packaging must be performed on the target operating system.

## OpenFOAM execution

The editor, project files, instant hydraulics, result viewer, and probes work
without Docker. Advanced OpenFOAM execution requires Docker Desktop and the
repository-pinned `flowlab/openfoam11-gmsh:2026-07-13` image.

After starting Docker Desktop, verify the runtime from the repository root:

```bash
docker image inspect flowlab/openfoam11-gmsh:2026-07-13
npm run smoke:openfoam -- --output /tmp/flowlab-openfoam-smoke.json
```

FlowLab reports a blocked dependency state when the configured solver runtime
is unavailable; it does not substitute a simulated result.

## Local data and logs

Packaged jobs and `flowlab-backend.log` are stored in the operating system's
application-data directory:

- macOS: `~/Library/Application Support/FlowLab/`;
- Windows: `%APPDATA%\FlowLab\`.

Generated development jobs are stored under `runtime/flowlab/jobs/`.

## Verify a source checkout

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
npm run build
npm run desktop:electron:test
npm run test:e2e
```

The machine-readable package requirements are in
`desktop/electron/release-contract.json`.
