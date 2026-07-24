# FlowLab portable macOS release QA — 2026-07-23

Status: **portable internal arm64 package QA passed; external release blocked**

This record covers macOS packaging and desktop software behavior only. It does
not change benchmark fixtures, retained scientific evidence, validation gates,
the validated registry, or axisymmetric promotion status.

## Definitive disposition

FlowLab now has a self-contained arm64 macOS package whose backend does not
depend on `/opt/anaconda3/bin/python3`, another external Python interpreter, or
a non-system absolute dynamic-library path. A clean source checkout produced
the recorded artifact, and the artifact launched after relocation outside the
repository with both the packaged UI and a fresh isolated runtime directory.

The artifact passes the declared **internal package** gate. It is **not an
external release candidate** because this machine has no Developer ID
Application identity, the package is ad-hoc signed with no Team ID, no
notarization or stapled ticket exists, and no independent clean supported Mac
was available. No signing, notarization, publication, distribution, or
release-channel action was attempted.

## Starting state and source identity

- Requested starting branch: `codex/axisymmetric-venturi-profile`
- Verified starting commit: `5ef42cca2d6c9233acafa803e48edf7838b72884`
- Starting worktree: clean
- Historical package: arm64, ad-hoc signed, and internally QA-tested, but bound
  to `/opt/anaconda3/bin/python3`; therefore internal evidence only
- Portable-package source commit:
  `4d7393d7be7217e67c33f112e2e823f21d142e9e`
- Source build state: clean isolated clone; the bundled manifest records
  `sourceTreeClean=true`

## Supported package contract

- Platform: macOS
- Supported architecture: arm64 only
- Minimum supported OS: macOS 13.0
- Backend runtime: bundled CPython 3.12
- Backend layout: PyInstaller 6.21.0 `onedir`
- External interpreter: prohibited
- UI and instant hydraulics external dependencies: none
- Advanced OpenFOAM dependency: Docker Desktop plus
  `flowlab/openfoam11-gmsh:2026-07-13`
- Default local signature: ad hoc
- External-release signature: Developer ID Application with Team ID,
  notarization acceptance, and a stapled ticket

The machine-readable contract is `desktop/macos/release-contract.json`.
`scripts/build_macos_app.sh` enforces architecture, minimum OS, Python series,
and PyInstaller version. `scripts/qa_macos_app.sh` rejects a
`python-path.txt`, non-arm64 Mach-O files, non-system absolute dynamic-library
dependencies, a minimum-OS mismatch, and the historical Anaconda path.

## Artifact identity

| Item | Recorded value |
| --- | --- |
| App | `release/FlowLab.app` |
| Archive | `release/FlowLab-0.1.0-macos-arm64.zip` |
| Version/build | `0.1.0` / `1` |
| Bundle ID | `com.flowlab.desktop` |
| Minimum macOS | `13.0` |
| Architecture | thin arm64 for every Mach-O file |
| App size | 36,168 KiB |
| Archive size | 16,750,558 bytes |
| Archive SHA-256 | `ec6f19360e635bccf46bd68b3c9c36c1e37cbf1553b5e5ae840fe7dfa5feddb5` |
| Native shell SHA-256 | `b573c1d3737b7a189be7f6b130c53b263c4c3abedcd6db48b2cfe405958b56eb` |
| Bundled backend SHA-256 | `0dbc288652e68c8d9d1abcda2083246856dd9b967eb6d97198d14b2561b6ec00` |
| Build manifest SHA-256 | `a22f4560407581644717da1540c15575a73b81b099131fce79b0b771f95b1cb9` |

The native shell records `minos 13.0` and SDK `26.5`. The package build
manifest is at `Contents/Resources/build-manifest.json`.

## Exact build inputs

- Host build OS: macOS 26.5.1 arm64
- Apple clang: 21.0.0 (`clang-2100.1.1.101`)
- macOS SDK: 26.5
- Node: 24.10.0
- npm: 11.6.0
- CPython: 3.12.3 arm64
- pip: 24.0
- `package-lock.json` SHA-256:
  `d596feed10e3364e8a2b189764945558d0cad3f9ac14c057e43860ae9359f732`

Exact top-level Node packages from the clean build:

```text
@playwright/test 1.60.0
@testing-library/jest-dom 6.9.1
@testing-library/react 16.3.2
@types/node 24.13.2
@types/react 19.2.17
@types/react-dom 19.2.3
@types/three 0.184.1
@vitejs/plugin-react 5.2.0
immer 11.1.8
jsdom 27.4.0
lucide-react 0.561.0
react 19.2.7
react-dom 19.2.7
three 0.184.0
typescript 5.9.3
vite 7.3.5
vitest 4.1.8
zod 4.4.3
zustand 5.0.14
```

Exact Python build/runtime distributions from the clean build:

```text
altgraph 0.17.5
annotated-doc 0.0.4
annotated-types 0.7.0
anyio 4.14.2
click 8.4.2
fastapi 0.124.4
h11 0.16.0
httptools 0.8.0
idna 3.18
macholib 1.16.4
packaging 26.2
pip 24.0
pydantic 2.12.5
pydantic_core 2.41.5
pyinstaller 6.21.0
pyinstaller-hooks-contrib 2026.6
python-dotenv 1.2.2
PyYAML 6.0.3
setuptools 83.0.0
starlette 0.50.0
typing-inspection 0.4.2
typing_extensions 4.16.0
uvicorn 0.38.0
uvloop 0.22.1
watchfiles 1.2.0
websockets 16.1.1
```

The manifest also records these source-input SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| `desktop/macos/backend_main.py` | `b901b6d88527b5fb622c8d1a3fc27a578bf5c31af683560f7f3bf6c1ec443b07` |
| `desktop/macos/Info.plist` | `55521a698f0bb3fab8e478da56c80dfba7a363161e380b24a8f58b5694ae0df1` |
| `desktop/macos/main.m` | `a86b493e35c657ad527eabe8b7f3a2d8da047f58f586ad1f869aae0163f23513` |
| `desktop/macos/release-contract.json` | `88b2073533e23fd8eeac82b8ab0e1ca6345bb5fc873c6c40e9642ce9563e83b6` |
| `desktop/macos/requirements-build.txt` | `be3b564fee4bd113519991fe0f094084ec1614e93f22ab68bd8a766c0eb120cc` |
| `scripts/build_macos_app.sh` | `5e041de84e374b9c7afe5632a62203e033b4f1247d4b049e132fad1e4aa048d9` |
| `scripts/qa_macos_app.sh` | `e18d48c5271d0ef783b20ba148ab5095bda614023c08024d7c5a96f2bf640404` |

## Automated verification

The complete declared source suite was rerun after cleanup. Package contract,
plist, codesign, checksum, health, and UI checks were then rerun against the
retained clean-built artifact.

| Check | Result |
| --- | --- |
| Backend suite | 560 passed |
| Frontend unit/component suite | 75 passed |
| TypeScript lint | Passed |
| Production web build | Passed |
| Playwright desktop E2E | 15 passed |
| Clean-clone dependency installation | Passed; Python `pip check` clean |
| Clean-clone PyInstaller arm64 build | Passed |
| `codesign --verify --deep --strict` | Passed |
| `plutil -lint` | Passed |
| Internal package contract QA | Passed |
| `python-path.txt` absent | Passed |
| Historical Anaconda path scan | Passed; absent |
| Mach-O architecture/linkage scan | Passed; arm64 and system/relative linkage only |

## Relocated and isolated execution

The clean artifact was copied to an unrelated temporary directory before
launch. Process inspection showed the running service executable and CPython
extension modules resolving under that relocated `.app`; no Python process or
library was loaded from the repository, `/opt/anaconda3`, or another external
Python installation.

The relocated full native application:

- started its bundled backend;
- served `/api/health` with HTTP 200 and
  `{"status":"ok","service":"flowlab-solver"}`;
- served the production UI and assets;
- loaded four reference cases and two validated-registry entries;
- restored a completed OpenFOAM result with seven solved field snapshots;
- switched to Cinema/3D;
- returned native surface-probe output `U @ surface`, `0.2342 m/s`, and
  `Surface XYZ`;
- accepted and restored an editor numeric-field edit.

A second launch invoked the same bundled backend on a different port with a
new runtime directory. It returned HTTP 200 for health and UI and reported
zero recent jobs, proving startup does not depend on the persisted desktop
runtime.

## Packaged browser and interaction QA

A fresh Playwright browser context exercised the UI served by the relocated
package:

- `/api/health` passed before and after reload;
- the 1440×900 workspace had no document-level horizontal or vertical
  overflow;
- the solved `venturi-result.vtk` fixture loaded and switched to 3D/Cinema;
- a normal surface click returned
  `pressure @ p5`, `72,000 Pa`, `Point XYZ 420, 265, 0`;
- the Top camera action set the Cinema pitch to `76`;
- adding a pump produced one Pump editor and undo removed it;
- pressure/velocity field toggles responded;
- browser console errors: zero;
- uncaught page errors: zero.

Native and browser screenshots were reviewed outside Git. The fixed workspace,
result surface, controls, and runtime status remained usable in both paths.
The compact header retains pre-existing dense label crowding; packaging did
not change that UI surface, and this record does not use it as external-release
evidence.

## Signing and external gate

| Gate | Result |
| --- | --- |
| Signature verification | Pass |
| Signature type | Ad hoc |
| Team ID | Absent |
| Code-directory hash | `81aa51620b3b344acd08b70ff6eaaf9f3965a554` |
| Installed valid code-signing identities | 0 |
| Developer ID Application signature | **Fail: absent** |
| Gatekeeper external assessment | **Fail** |
| Apple notarization acceptance | **Not available / not attempted** |
| Stapled notarization ticket | **Fail: absent** |
| Independent clean supported arm64 Mac | **Not available** |

`scripts/qa_macos_app.sh ... external` exits nonzero at the missing Developer
ID Application signature. These are external infrastructure/authorization
gates, not software-test or scientific-validation failures.

## Status separation

- Internal package readiness: **passed for this portable arm64 artifact**
- Scientific validation: **unchanged; empirical promotion gate blocked**
- Axisymmetric verification candidate: **unchanged; not promotion-authorized**
- External-release readiness: **blocked by Developer ID, notarization/stapling,
  and independent clean-Mac gates**
- Publication/distribution: **not authorized and not performed**
