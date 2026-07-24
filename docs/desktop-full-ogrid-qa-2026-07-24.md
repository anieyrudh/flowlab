# FlowLab packaged full O-grid QA — 2026-07-24

Status: **bounded software/package path passed; separate v2 numerical-
verification candidate passed; controlled independent review pending**

This record covers product software, macOS packaging, and native interaction
only. It did not itself validate the O-grid campaign, qualify three-level
convergence or GCI, authorize promotion, or change a benchmark fixture or
registry entry. The later v2 scientific disposition is recorded separately
below.

## Source and package identity

- Branch: `codex/full-ogrid-product-milestone`
- Package source commit:
  `81c6562e6049ada5333912ee0f0a2e6771d589f9`
- Source tree at build: clean
- Bundle: `release/FlowLab.app`
- Target: arm64 macOS 13.0 or newer
- Bundled runtime: CPython 3.12.3 arm64
- PyInstaller: 6.21.0
- Signature: ad hoc
- Bundle size: 35 MiB
- Build manifest SHA-256:
  `cf9297e3bdcb69bc012cb5554ec105fd126c1ed3d0e360606aeccbd121d62c4a`

The package was built with an isolated temporary environment from the exact
pins in `desktop/macos/requirements-build.txt`. The repository dependency
contract was not changed.

## Automated checks

| Check | Result |
| --- | --- |
| Backend suite | 581 passed |
| Frontend unit/component suite | 81 passed |
| TypeScript lint | Passed |
| Production web build | Passed |
| Playwright desktop E2E | 16 passed |
| Portable macOS package build | Passed |
| Internal package contract QA | Passed |
| `codesign --verify --deep --strict` | Passed |
| `plutil -lint` | Passed |

The full O-grid E2E test selects OpenFOAM and the full-360 mode, confirms that
steady/laminar behavior is selected, and verifies the exact medium request
tuple: 32 axial, 8 annular-radial, 64 circumferential, and 16 core-side cells.

## Native packaged interaction

The rebuilt `release/FlowLab.app` was launched through the native macOS
interaction surface. The packaged backend reported online. The UI was changed
from Instant 1D to OpenFOAM, then from the default planar mesh to
`Full 360 O-grid (straight pipe)`.

The native accessibility tree confirmed:

- solver `OpenFOAM`;
- mode `Incompressible`;
- run mode `Steady (converged Δp)`;
- mesh mode `Full 360 O-grid (straight pipe)`;
- medium resolution;
- axial cells `32`;
- annular-radial cells `8`;
- circumferential cells `64`; and
- core-side cells `16`.

No additional scientific solver run was launched during package QA. The app was
closed after inspection.

Native capture:
`/Users/anieyrudh/.codex/visualizations/2026/07/23/019f8f45-436e-70d1-bf49-2fd00c3fe9b0/flowlab-full-ogrid-native-qa-2026-07-24.jpeg`

The capture is a software QA aid, not retained scientific campaign evidence.

## Disposition

The bounded product and package path is software-complete for the declared
straight circular-pipe mode. Historical campaign `2026-07-24-v1-r2` remains a
frozen coarse solver-gate failure. The later prospective
`2026-07-24-v2-r3` campaign preserved the physical and numerical setup, added
per-iteration QoI stability evidence, completed all three levels, and passed
every frozen numerical-verification candidate gate. Its observed order was
`1.6125950398033853`, fine-grid GCI was `0.1596996556959516%`, and immutable
package tree digest is
`11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6`.

That result does not retroactively change this package QA evidence or v1.
Controlled independent review remains unavailable, so benchmark, independent
validation, promotion, product-claim, and release states remain unchanged.
