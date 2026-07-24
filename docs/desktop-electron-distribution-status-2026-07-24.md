# FlowLab Electron distribution status — 2026-07-24

## Disposition

FlowLab now has a bounded Electron distribution implementation for macOS
Apple Silicon and Windows x64. The implementation is ready to produce and test
internal candidates on both platform-native builders. It is not yet an
externally released product.

This record keeps four states separate:

- Electron software and packaging implementation: **implemented**.
- Local macOS arm64 internal candidate: **passed package, launch, visual, and
  installer QA**.
- Windows x64 candidate: **implemented but not executed on a Windows runner in
  this session**.
- External GitHub release: **blocked**.

No benchmark fixture, registry entry, validation state, scientific promotion,
or full O-grid claim was changed by this distribution work.

## Distribution contract

`desktop/electron/release-contract.json` freezes the first platform boundary:

- macOS 13+ on Apple Silicon (`arm64`): DMG and ZIP;
- Windows 11 on `x64`: Squirrel Setup executable and ZIP;
- Electron renderer with context isolation, sandboxing, and no Node.js
  integration;
- a narrow preload bridge for bounded text-file export only;
- one platform-native CPython 3.12/PyInstaller 6.21.0 backend per package;
- no external Python or Node.js requirement on the user's machine; and
- Docker Desktop remains an explicit dependency only for advanced OpenFOAM
  execution.

Unsupported desktop platforms and malformed export payloads fail closed.
Renderer navigation and file-export IPC are restricted to the exact ephemeral
loopback origin owned by the bundled backend. Export filenames, counts, and
payload sizes are bounded, and multi-file replacement requires a native
confirmation.

## Implemented product path

The Electron main process:

1. obtains an unused loopback port;
2. launches the platform-native bundled `FlowLabBackend` executable;
3. waits for `/api/health`;
4. opens the production React application served by that backend;
5. keeps solver jobs and logs in the platform application-data directory; and
6. terminates the backend with the desktop application.

The existing editor, case-generation, JobManager, result loading, Cinema 3D
view, and XYZ probing code remains shared with the browser and prior macOS
shell. The React export path now prefers the Electron bridge and retains the
legacy WebKit bridge only for the historical macOS shell.

## GitHub delivery path

`.github/workflows/desktop-electron-candidate.yml` defines a non-publishing
candidate workflow:

- one complete source-verification job;
- one `macos-15` arm64 package, launch, installer, and artifact-manifest job;
- one `windows-2025` x64 package, launch, installer, and artifact-manifest job;
- no release creation; and
- 14-day workflow artifacts for review.

`.github/workflows/desktop-electron-release.yml` defines a separate manual
external-release workflow. It cannot proceed unless:

- `FLOWLAB_EXTERNAL_RELEASE_AUTHORIZED` is exactly `true`;
- `FLOWLAB_OGRID_REVIEW_ACCEPTED_DIGEST` exactly equals
  `11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6`;
- the protected `external-release` GitHub Environment permits the jobs;
- macOS Developer ID and Apple notary credentials are present;
- Windows Authenticode credentials are present;
- both native platform builds pass source, package, launch, and installer QA;
- the macOS application and DMG pass notarization/stapling checks; and
- the Windows Setup executable has a valid timestamped Authenticode signature.

Only then may the workflow create a bounded GitHub prerelease. The release
notes explicitly keep full O-grid CFD limited to steady incompressible laminar
straight circular pipes and state that independent validation and scientific
promotion are not authorized by the software release.

## Verification completed in this session

Source verification from the repository root:

- backend: `589 passed`;
- frontend: `82 passed`;
- Electron runtime/bridge: `5 passed`;
- TypeScript lint: passed;
- production renderer build: passed;
- desktop Playwright E2E: `16 passed`;
- locked Node advisory audit: `0 vulnerabilities`;
- Electron and workflow JavaScript syntax: passed; and
- candidate/release workflow YAML parse: passed.

Final local macOS package checks:

- PyInstaller backend: CPython `3.12.3`, PyInstaller `6.21.0`, arm64;
- strict recursive code-signature verification: passed with the expected
  internal ad-hoc signature;
- bundled backend health: passed;
- production UI reachability: passed;
- DMG presence/non-empty QA: passed; and
- ZIP presence/non-empty QA: passed.

The first packaged launch exposed a macOS dyld rejection: applying hardened
runtime library validation to an ad-hoc identity produced unrelated Team IDs
for the Electron executable and framework. The fix is deliberately
mode-specific:

- internal ad-hoc candidates are signed without the hardened-runtime flag; and
- real Developer ID candidates require hardened runtime, explicit
  entitlements, Developer ID verification, Gatekeeper assessment,
  notarization, and stapling.

This does not weaken the external-release path.

Native desktop interaction on the rebuilt package confirmed:

- the bundled service reached `Online`;
- OpenFOAM could be selected;
- `Full 360 O-grid (straight pipe)` forced steady mode;
- medium resolution set axial, annular-radial, circumferential, and core-side
  counts to `32 / 8 / 64 / 16`;
- the bundled result fixture loaded;
- Analyze/Probes mode activated; and
- the result view switched from Schematic 2D to Cinema 3D without visible
  clipping, broken layering, or a renderer error.

The managed-session Playwright Electron launcher could not attach directly to
the packaged executable because that skill requires an unsandboxed Codex
session. Packaged-process health was therefore checked by the deterministic
smoke script, while native window controls and visuals were checked through
macOS accessibility/desktop control. Browser E2E independently covers Cinema
raycasting, view-mode coherence, result playback, layout containment, and the
full O-grid request contract.

## Local candidate artifacts

The final local development candidate produced:

- `out/make/FlowLab-0.1.0-macOS-arm64.dmg`;
- `out/make/zip/darwin/arm64/FlowLab-darwin-arm64-0.1.0.zip`; and
- `out/make/flowlab-release-manifest-darwin-arm64.json`.

Local SHA-256 values:

- DMG:
  `36e4e335e1b5c6dd4551165bbf4919f3d93fce8d598f0dd5b3c53ae680937846`;
- ZIP:
  `0a4102772de2317593fd446c64bf00395e37d055cb9fed3e56ebf7a0fd1b3b11`;
- artifact manifest:
  `7a9259b76c6110cc93f51ff8e541d5c3b3e597be3b854ca17f011fdd4773ef0d`.

These paths are ignored by Git and remain local. They are ad-hoc-signed
internal artifacts, not retained scientific evidence and not public download
assets. A publishable artifact must be rebuilt from a clean reviewed tag by
the protected external-release workflow.

## Exact blockers

External GitHub download availability remains blocked on:

1. the exact private GitHub owner/repository name and remote have not been
   verified or configured;
2. the Windows workflow has not run, so no Windows build/install/launch
   evidence exists yet;
3. controlled independent review has not accepted the exact full O-grid
   evidence digest;
4. no macOS Developer ID/notary credentials are configured here;
5. no Windows Authenticode certificate is configured here; and
6. no clean-machine macOS or Windows installer acceptance has been recorded.

The next honest deployment step is to verify the intended private GitHub
repository, push this branch, run the candidate workflow, and inspect both
platform artifacts. Signing credentials and external release authorization
should be added only after the controlled review and candidate evidence are
accepted.
