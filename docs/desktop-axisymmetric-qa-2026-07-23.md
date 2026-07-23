# FlowLab packaged axisymmetric QA — 2026-07-23

Status: **internal macOS package QA passed; external distribution blocked**

This QA covers the current axisymmetric/Venturi software path, 3D result surface,
solver-space XYZ probing, and the local macOS bundle. It is software/release
evidence, not scientific promotion or empirical validation.

## Environment

- Host: macOS arm64
- Bundle: `release/FlowLab.app`
- Bundle ID: `com.flowlab.desktop`
- Version: `0.1.0` build `1`
- Minimum macOS: `13.0`
- Bundle size: `3.5M`
- Python recorded by the bundle: `/opt/anaconda3/bin/python3`
- Signature: ad hoc; no Team ID

## Automated checks

| Check | Result |
| --- | --- |
| Backend tests | 557 passed |
| Frontend tests | 75 passed |
| TypeScript lint | Passed |
| Production web build | Passed |
| Playwright desktop E2E | 15 passed |
| macOS package build | Passed |
| `codesign --verify --deep --strict` | Passed |
| `plutil -lint` | Passed |
| Packaged `/api/health` | HTTP 200, `flowlab-solver` OK |
| Packaged static UI | HTTP 200 |

## Native packaged interaction

The rebuilt app was launched fresh after quitting the prior bundle. The local
solver service moved from the startup screen to the working UI in approximately
1.5 seconds. The UI reported the backend online and restored a saved OpenFOAM
result.

The result was switched to Cinema/3D and the visible solver surface was clicked.
The packaged accessibility tree reported:

- `Probe sample`;
- `U @ surface`;
- `0.2451 m/s`;
- `Surface XYZ`.

This confirms that the native app is running the barycentric point-field surface
sampling path rather than the old nearest-vertex readout. A browser inspection
against the same packaged backend independently reported:

- `p @ surface`;
- `0.0154 Pa`;
- `Surface XYZ 0.229138, 0.766354, -0`;
- no browser console warnings or errors.

Editor objects retain pointer-down priority when they overlap result geometry;
a stationary non-object click probes the solver surface. The full E2E suite
also verifies that Cinema node dragging remains functional after the probing
change.

## Evidence

- Browser capture:
  `/Users/anieyrudh/.codex/visualizations/2026/07/23/019f8eaf-3fad-7740-a404-af258b2ced9b/flowlab-axisymmetric-xyz-browser.png`
- Native capture:
  `/Users/anieyrudh/.codex/visualizations/2026/07/23/019f8eaf-3fad-7740-a404-af258b2ced9b/flowlab-packaged-native-surface.jpeg`
- Packaged executable SHA-256:
  `18a83dc86acb2d1dd313c75eef0183b648151df2ece00ec715792636e76be5e5`

The captures are QA aids, not retained scientific campaign evidence.

## Release disposition

The package is suitable for controlled internal use on this configured Mac. It
is not an externally distributable release because:

- the signature is ad hoc and has no Apple Developer Team ID;
- the bundle records a machine-local Python executable rather than carrying a
  relocatable runtime and dependencies;
- it has not been Developer ID signed, notarized, stapled, or packaged as a
  clean-machine-tested DMG;
- Docker/OpenFOAM remains an externally provisioned runtime dependency.

These release blockers are separate from the straight-pipe fixture hold,
empirical validation, and product-promotion gates.
