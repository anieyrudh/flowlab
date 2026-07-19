# FlowLab desktop release — 2026-07-16

## Outcome

FlowLab has a macOS desktop-only application at `release/FlowLab.app`. The
current v4 campaign is numerically accepted but lacks an eligible independent
experimental dataset, so the validated-run action is hidden and its API fails
closed with HTTP 409.

The completed coarse plane-Poiseuille run below remains historical runtime
evidence, not current desktop-promotion authority. General project-generated
OpenFOAM, SU2, Code_Saturne, and MuJoCo cases remain experimental.

## Historical coarse product path — currently disabled

1. When every campaign gate passes, the desktop may expose `Run validated coarse preset` under `Validated regimes`.
2. `POST /api/benchmarks/validated/laminar-open-boundary-all-hex-v1/jobs` atomically creates and queues the case server-side.
3. The job can carry `status=validated-benchmark` only when every file exactly matches the generated immutable contract.
4. `Allrun` performs `blockMesh`, full `checkMesh`, exact analytic U/p initialization, OpenFOAM 11 solve, `flowlabPatchTractionAudit`, VTK export, and the structured runtime audit.
5. The job becomes complete only when the VTK fields are parseable and every validated runtime check passes.
6. The desktop displays the field artifacts and force reconciliation. It continues to prohibit general or production-CFD claims.

## Runtime gate result

The real product/API smoke is stored at:

`benchmarks/cases/open-boundary/runs/2026-07-16-desktop-validated-preset-v1/artifacts/desktop-preset-smoke.json`

It ran in the pinned Docker image and completed in 130.36 seconds under AMD64 emulation. All runtime checks passed:

| Check | Observed |
|---|---:|
| OpenFOAM force object vs direct face integration, absolute | 1.7638e-16 |
| Wall force object vs direct face integration, absolute | 1.3878e-17 |
| Direct pressure force vs analytic, relative | 1.7347e-15 |
| Direct wall viscous traction vs analytic, relative | 6.3373e-7 |
| Integrated open-boundary viscous traction vs analytic zero, relative | 7.8513e-14 |
| Maximum face viscous-traction error, relative | 6.8101e-7 |
| Mass imbalance, relative | 1.1005e-14 |

The job collected seven VTK field files and eight diagnostic files. The direct audit decomposed 864 boundary faces.

## Release verification

- Backend: 408 tests passed.
- Frontend: 57 tests passed.
- TypeScript lint: passed.
- Vite production build: passed.
- macOS bundle: ad-hoc code signature verifies; Info.plist validates.
- Packaged bundle size: 2.7 MB.
- Packaged startup: `/api/health` returned `ok` and `/` served the production UI.
- Rendered packaged UI: v4 empirical blocker visible, validated preset control absent, backend online, no blocking overlay, and no console warnings/errors.
- Screenshot capture through the in-app browser timed out twice on the WebGL-heavy workspace; DOM, API, and console verification completed successfully and screenshot retries were stopped rather than masking the failure.

## Why the repository is large but the app is small

After legacy raw-data compaction, the repository's `benchmarks/` tree is
approximately 14 GB. Almost all of that is the retained current v4 campaign;
older campaigns keep compact reports rather than generated mesh/field trees.

The desktop build copies only:

- the production UI;
- the Python backend without tests or bytecode caches;
- the reference-case registry;
- the eight pinned evidence/control files required by the validated registry; and
- the three source/build files for `flowlabPatchTractionAudit`.

It does not bundle the 14 GB research tree.

## Known deployment boundary

This is the first usable local desktop release, not a generally distributable installer. It uses the Python executable recorded at build time and requires Docker for OpenFOAM. The next packaging step for use on other Macs is to bundle the Python runtime and dependencies, then Developer ID sign, notarize, and produce a DMG. That packaging work does not change the scientific claim boundary.

Scientific promotion remains separately blocked until
`experimentalDatasetPinned` passes.
