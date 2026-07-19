# FlowLab desktop Computer Use QA — 2026-07-16

## Release verdict

The current packaged macOS application **passes desktop workflow QA, but the
bounded validated solver preset remains unavailable**. The native file bridge,
persisted recent runs, result reconciliation, and editor controls work. The v4
campaign gate panel shows only `experimentalDatasetPinned`; the run action is
absent and the backend returns HTTP 409.

The historical coarse OpenFOAM job below passed its own runtime audit. Its
restored card now states that the current bounded-regime promotion is blocked,
so persisted evidence cannot visually override the live campaign gate. No
mobile implementation or mobile QA is in scope.

Distribution remains a separate concern: the macOS bundle is ad-hoc signed rather than Developer ID signed/notarized, and a Windows installer has not yet been produced.

## Evidence summary

- Packaged app: `release/FlowLab.app`
- Real desktop job: `job-b87a21e8d0`
- Historical bounded runtime: `laminar-open-boundary-all-hex-v1`
- Terminal status: `Job complete`
- Scientific status: coarse runtime audit passed; current campaign promotion blocked
- Runtime artifacts: 7 unique VTK field files and 8 solver diagnostics
- Result summary: `~/Library/Application Support/FlowLab/runtime/jobs/job-b87a21e8d0/case/postProcessing/validated-benchmark-summary.json`
- Native project QA artifact: `~/Documents/venturi-cavitation-lab.flowlab.json`
- Automated regression:
  - Backend: 408 passed
  - Frontend unit/component: 59 passed
  - Desktop Playwright E2E: 11 passed
  - TypeScript/lint: passed
  - Production web build: passed
  - Native macOS build and ad-hoc signing: passed

Validated force evidence displayed in the packaged app:

| Comparison | Relative error |
|---|---:|
| OpenFOAM force object vs direct face integration | `1.76e-16` |
| Direct pressure force vs analytic pressure force | `1.73e-15` |
| Wall viscous traction vs analytic state | `6.34e-7` |
| Open-boundary viscous traction vs analytic zero | `7.85e-14` |

## Functional coverage

| Area | Desktop result | Supporting automation | Verdict |
|---|---|---|---|
| App startup, backend health, restart | Started, backend online, restarted cleanly | Backend smoke/tests | Pass |
| Pause/run and recompute | State changed correctly | Frontend tests | Pass |
| Design/simulate/sweep/analyze modes | Controls responded and exposed selected state | E2E workspace tests | Pass |
| Presets and reference cases | Presets switched; all four reference plans loaded | Frontend/API tests | Pass |
| Component creation | Components and a third pipe were added; success feedback appeared | E2E create/delete tests | Pass |
| Canvas editing | Packaged-app actions responded | Raycast drag, rotate, aim, delete, port drag and endpoint edit passed in E2E | Pass |
| Edge/node inspector | Numeric edits, resets, rotation presets and pump head worked | Store and E2E tests | Pass |
| Workspace rail and project/layer controls | Navigation and layer toggles now perform their named actions | Frontend tests | Pass |
| Mesh/adaptation controls | Toggles and values worked | Frontend tests | Pass |
| Reviewed STL workflow | Pasted and multi-file STL paths passed; native wrapper uses the same open-panel bridge verified by project/VTK import | Three-file upload and production mesh QA passed in E2E | Pass |
| Cinema/schematic rendering | Both rendered; camera presets worked | WebGL object and camera E2E | Pass |
| Instant sweep | Eight sweep points appeared | Store/frontend tests | Pass |
| Fixture result viewer | Native picker loaded `venturi-result.vtk`; 2D/3D, fields, color maps, stepping, playback and loop worked | Result playback E2E | Pass |
| Validated preset | Historical OpenFOAM runtime passed; current action absent and launch returns HTTP 409 | API/backend validation tests | Blocked by design |
| Artifact index/preview | Index and sequence preview worked without increasing the unique snapshot count | Backend artifact and frontend tests | Pass |
| Project persistence | Edited project restored after restart | Reload E2E | Pass |
| Job/result persistence | Completed job, case metadata and seven result artifacts restored after restart | Backend persistence and frontend restore tests | Pass |
| Native project import/export | Saved valid JSON through `NSSavePanel`, then reopened it through `NSOpenPanel` | Native bridge unit test | Pass |
| Native result-bundle export | Native folder chooser opened and supports multi-file writes | Native bridge unit test | Pass |
| Native VTK import | Native picker opened the repository fixture and loaded its five fields | Parser/E2E tests | Pass |
| Timeline CSV export | Routed through the same native save bridge as project export | Native bridge unit test | Pass |

## Resolved adoption blockers

### Native open/save integration

The macOS shell now implements `WKUIDelegate`, a script-message bridge, `NSSavePanel`/`NSOpenPanel`, and `WKDownloadDelegate` fallback behavior. The bridge is restricted to the local FlowLab origin. Project export produced valid JSON on disk, project import reopened that file, VTK import loaded the fixture, and result-bundle export opened a native directory chooser. Save, cancel and error outcomes are returned to the React UI.

### Durable solver runs and lazy result restoration

Jobs and case records are atomically persisted in each runtime job directory. `GET /api/jobs` exposes recent runs. The UI remembers the active job ID, restores its case and terminal state after restart, indexes its artifacts, and lazily selects the last available result rather than persisting large VTK payloads in browser storage. Interrupted non-terminal records are marked failed honestly on backend restart.

### Result identity and field reconciliation

Full results, previews and chunked artifacts share canonical `job + path` identities, so repeated previewing does not duplicate the timeline. Compatible `velocity/U`, `pressure/p/p_rgh`, and temperature aliases are reconciled only when field location and kind agree. Overlay changes clear incompatible pinned fields.

### Control semantics and feedback

The workspace rail, project tree and layer rows now navigate or toggle real state. Static section headings no longer impersonate buttons. Component connection failures report why they failed. Selected-component actions are named for what they do (`Edit`, `Analyze`, `Geometry`). Modes, overlays and layers expose `aria-pressed`.

### Solver context clarity

The configured solver is labeled `New case solver`; a separate chip identifies the selected completed run, for example `Viewing: OpenFOAM · complete`. This removes the former visual contradiction between the next case configuration and the restored result.

## Remaining constraints

1. Sign and notarize the macOS app before external distribution.
2. Produce and test a Windows desktop package separately; no Windows binary is included in this macOS build.
3. Obtain and pin a compatible independent dataset and uncertainty packet before enabling the structured all-hex laminar preset.
4. Treat production mesh readiness, turbulence, transients, multiphase flow, arbitrary CAD and general open boundaries as unvalidated.

## Adoption recommendation

Use this build for internal desktop editing and result-inspection QA only. Do
not treat the laminar preset as currently runnable or empirically validated.
Preserve the force-audit card, current-gate warning, and bounded-claim language
in every restored run. Packaging/signing can proceed independently of the
remaining experimental validation work.
