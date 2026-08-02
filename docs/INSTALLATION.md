# Installing FlowLab

FlowLab is an Electron desktop application. It runs on macOS 13 or later on
Apple Silicon. It also runs on Windows 11 x64.

## Downloading a release

**There is no signed public release. You cannot download and install FlowLab
yet.** To use FlowLab now, run it from source. Refer to
[Running from source](#running-from-source).

The project will publish signed installers on the GitHub
[Releases](https://github.com/anieyrudh/flowlab/releases) page. It will publish
them only after the platform signing gate and the release gate pass. The
installers will be:

- macOS: DMG and ZIP;
- Windows: Setup EXE and ZIP.

The repository's `Desktop Electron candidate` workflow builds short-lived macOS
and Windows artifacts. These artifacts are for engineering review. They are not
release installers. They are not signed, and your operating system will refuse
to open them.

## Running from source

Install this software first:

- Git;
- Node.js 24 with npm;
- CPython 3.12; and
- Docker Desktop, only if you want to run OpenFOAM jobs.

Clone the repository. Then install the dependencies:

```bash
git clone https://github.com/anieyrudh/flowlab.git
cd flowlab
npm ci
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
```

On Windows, use these PowerShell commands instead. They create the environment
and activate it:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server\requirements.txt
```

Start the local service:

```bash
npm run server
```

Open a second terminal. Then start the editor:

```bash
npm run dev
```

Open `http://127.0.0.1:5173`. FlowLab binds its development services to the
loopback interface.

## Building the Electron application

The packaged application contains its own Python backend. Prepare the exact
build environment first.

**You must build the package on the target operating system.**

On macOS, use these commands:

```bash
python3.12 -m venv .venv/electron-build
.venv/electron-build/bin/python -m pip install -r desktop/electron/requirements-build.txt
export FLOWLAB_BUILD_PYTHON=.venv/electron-build/bin/python
npm ci
npm run desktop:electron:make -- --platform=darwin --arch=arm64
```

On Windows, use these PowerShell commands:

```powershell
py -3.12 -m venv .venv\electron-build
.\.venv\electron-build\Scripts\python.exe -m pip install -r desktop\electron\requirements-build.txt
$env:FLOWLAB_BUILD_PYTHON = ".\.venv\electron-build\Scripts\python.exe"
npm ci
npm run desktop:electron:make -- --platform=win32 --arch=x64
```

The build writes its output to `out/`. Run these package and installer checks
before you share a candidate:

```bash
npm run desktop:electron:qa -- --package out/FlowLab-darwin-arm64/FlowLab.app --mode internal
npm run desktop:electron:smoke -- --package out/FlowLab-darwin-arm64/FlowLab.app
npm run desktop:electron:qa:artifacts -- --directory out/make --platform darwin --arch arm64 --mode internal
```

For the Windows checks, use `out/FlowLab-win32-x64`, `win32`, and `x64`
instead.

## OpenFOAM execution

The editor, project files, instant hydraulics, result viewer, and probes work
without Docker Desktop. To run OpenFOAM jobs, you need Docker Desktop. You also
need the `flowlab/openfoam11-gmsh:2026-07-13` image that the repository pins.

Start Docker Desktop first. Then check the runtime from the repository root:

```bash
docker image inspect flowlab/openfoam11-gmsh:2026-07-13
npm run smoke:openfoam -- --output /tmp/flowlab-openfoam-smoke.json
```

FlowLab reports a blocked dependency state if the configured solver runtime is
not available. It does not substitute a simulated result.

## Local data and logs

FlowLab stores packaged jobs and `flowlab-backend.log` in the application-data
directory of the operating system:

- macOS: `~/Library/Application Support/FlowLab/`;
- Windows: `%APPDATA%\FlowLab\`.

FlowLab stores the generated development jobs under `runtime/flowlab/jobs/`.

## Verify a source checkout

Run these commands:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
npm run build
npm run desktop:electron:test
npm run test:e2e
```

The file `desktop/electron/release-contract.json` holds the machine-readable
package requirements.
