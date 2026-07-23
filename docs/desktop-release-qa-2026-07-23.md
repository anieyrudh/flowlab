# FlowLab portable macOS release QA — 2026-07-23

Status: **portable-package implementation in progress; no external-release claim**

This record covers macOS packaging and desktop software behavior only. It does
not change benchmark fixtures, retained scientific evidence, validation gates,
the validated registry, or axisymmetric promotion status.

## Starting state

- Requested starting branch: `codex/axisymmetric-venturi-profile`
- Verified starting commit: `5ef42cca2d6c9233acafa803e48edf7838b72884`
- Starting worktree: clean
- Historical package: arm64, ad-hoc signed, and internally QA-tested, but bound
  to `/opt/anaconda3/bin/python3`; therefore not portable evidence

## Declared package contract

- Platform: macOS
- Supported architecture: arm64 only
- Minimum supported OS: macOS 13.0
- Backend runtime: bundled CPython 3.12 and exactly pinned dependencies
- Backend layout: PyInstaller 6.21.0 `onedir`
- External interpreter: prohibited
- UI and instant hydraulics external dependencies: none
- Advanced OpenFOAM dependency: Docker Desktop plus
  `flowlab/openfoam11-gmsh:2026-07-13`
- Default local signature: ad hoc
- External-release signature: Developer ID Application with Team ID,
  notarization acceptance, and a stapled ticket

The machine-readable contract is `desktop/macos/release-contract.json`.
`scripts/build_macos_app.sh` enforces the architecture, minimum OS, Python
series, and PyInstaller version. `scripts/qa_macos_app.sh` rejects a
`python-path.txt`, non-arm64 Mach-O files, non-system absolute dynamic-library
dependencies, a minimum-OS mismatch, and the historical Anaconda path.

## Provisional implementation evidence

The first self-contained prototype was built from the working tree after the
portable-backend change:

| Check | Result |
| --- | --- |
| Focused release-contract and evidence-bundle tests | 4 passed |
| Full backend suite | 560 passed |
| Frontend unit/component suite | 75 passed |
| TypeScript lint | Passed |
| Production web build | Passed |
| Playwright desktop E2E | 15 passed |
| PyInstaller arm64 backend build | Passed |
| AppKit shell minimum macOS | 13.0 |
| Internal package contract QA | Passed |
| `python-path.txt` absent | Passed |
| Relocated package static QA outside repository | Passed |
| Relocated bundled `/api/health` | HTTP 200, `flowlab-solver` OK |
| Relocated packaged UI | HTTP 200 |
| Relocated reference registry | Loaded, 4 cases |
| Relocated validated registry | Loaded, 2 entries |

The prototype is not the release-QA artifact because its manifest correctly
records `sourceTreeClean=false`. It proves the packaging approach is viable,
not that the final candidate has cleared the declared release surface.

## Remaining gates

- commit the coherent packaging implementation;
- rebuild from a clean checkout of the exact source commit in a fresh locked
  build environment;
- record the exact build inputs, dependency versions, package/archive
  checksums, executable hashes, plist, and signature details for that artifact;
- launch the relocated full `.app`, not only its backend, and repeat packaged
  health and browser-console checks;
- load a solved 3D result, confirm XYZ surface probing, and preserve editor
  interactions in native desktop QA;
- run the external package gate.

External release remains blocked unless the final artifact has a Developer ID
Application signature, Apple notarization acceptance, a stapled ticket, and a
successful launch on a clean supported arm64 Mac. No signing, notarization,
distribution, or release-channel action is authorized by this work.

## Status separation

- Internal package readiness: **in progress**
- Scientific validation: **unchanged; promotion blocked**
- External-release readiness: **blocked pending all remaining gates**
- Publication/distribution: **not authorized**
