# FlowLab visualization integration report

Date: 2026-08-02  
Integration branch: `codex/flowlab-visualization-integration`  
Prompt 1 baseline: `d3bef15698272ac57f73ca6a82b27e0e21c11d8d`

## Scope and invariant

This branch integrates reviewed commits only. It does not copy working-tree
snapshots, mutate retained campaigns, change frozen scientific gates, push a
remote branch, authorize promotion, or create a release.

One authority chain governs solver-result selection and derived products:

1. the full case artifact must match the active project, case, and job;
2. its path and source-cell count must match exactly one explicit
   `resultComponentMap` binding;
3. multi-edge OpenFOAM bindings use the FlowLab-native source-cell identity
   contract and `flowlabSourceCellId`, never display geometry or legacy
   `VTK/*.vtk` ordering;
4. explicitly unowned generated cells remain probe-only;
5. imported, thinned, ambiguous, stale, unsupported, and SU2-unverified
   results cannot enable streamline, voxel, iso, or pathline products.

## Reviewed inputs and dispositions

| Input | Reviewed tip | Accepted | Rejected or withheld | Rationale |
|---|---|---|---|---|
| Axisymmetric qualification | `ed9c11558db8bd9bd373a7413308bc7b3479e5df` | Frozen contracts, generators, identity tests, and retained blocker documentation | Product exposure and any qualification/promotion claim | Source and solver signatures disagree for 888 of 2,496 cells and medium/fine evidence is incomplete. |
| Curved-elbow qualification | `ad19e8afc144a9875689b7496971d8543ae72d3d` | Canonical bounded generator, contracts, diagnostics, explicit component ranges, tests, and the existing Experimental UI choice | Validation, promotion, or general elbow support claims | V2 passed its prospective numerical gates but remains a bounded candidate awaiting independent review. |
| Y-junction qualification | `f17eaed92c84d627d33297a9dabba18da425290c` | Backend generator, schemas, frozen contracts, failed assessment, generated junction identity, unowned-cell ranges, and tests | Primary UI mesh choice and any qualification/promotion claim | V5 fine-grid GCI is 7.24847%, above the frozen 5% gate. |
| Preview governance | `4d98d5bc69f230a22726d1c7fec15d7f1ae3db9c` | Stage-specific concept/generated/full/thinned/imported/fixture authority labels, renderer ownership, tests, and browser QA | Any label that treats a preview or imported fixture as a solver result | Preview state now determines admissible interaction, not merely presentation text. |
| Solver streamlines | `e324b13d45f1b19f1d8a807a16ef6b89fde45a18` | RK4 engine, worker cancellation, backend endpoint, generator-authored inlet seeds, renderer, limits, tests, and docs | Artifact-local unlinked product derivation, SU2-unverified derivation, and legacy ordering authority | Steady streamlines now require a full artifact with explicit cell-range authority. Passive sprites remain presentation-only and are not transient pathlines. |
| Derived volume/pathlines | `9d240b2d2a89f3763e5af42d235dced451de0697` | Deterministic volume/pathline engine, bounded cache and residency contracts, binary manifests, renderer, voxel/cut/iso/pathline controls, tests, and docs | Imported probe-only product admission, duplicate streamline authority, and selection without a compatible map | Product and API admission now fail closed unless every full job artifact has explicit component-map authority. |

## Integration resolutions

- Kept one FlowLab-native artifact binding for multi-edge source-cell identity;
  legacy `VTK/*.vtk` output is not an ownership source.
- Unified full O-grid, curved-elbow, and Y-junction generation without allowing
  geometry to infer component ownership.
- Added a generator-authored inlet/outlet boundary-face manifest to the
  multi-edge full-O-grid preview so automatic seeds retain exact source-cell
  provenance.
- Preserved componentwise OpenFOAM wall-shear extrema rather than constructing
  a physically unsupported magnitude from independent component extrema.
- Kept preview authority responsible for the active dataset and labels; the
  cinema renderer owns presentation only.
- Kept steady streamline and transient pathline implementations separate while
  sharing the same artifact/component authority boundary.
- Removed sibling-branch conflict notes and contradictory imported/SU2 labels.
- Retained all frozen qualification outcomes; no evidence directory was edited.

## Verification evidence

### Exact repository commands

| Command | Result |
|---|---|
| `env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests` | 677 passed, 3 skipped, 7 failed only because ignored local campaign evidence is absent from the isolated worktree. |
| Same command with `-m 'not local_evidence'` | 677 passed, 3 skipped, 7 deselected. |
| `npm test -- --run` | 129 passed in 14 files. |
| `npm run lint` | Passed. |
| `npm run build` | Passed; production Vite bundle built. |
| `npx playwright test` | 28 passed, including desktop browser QA with no console errors. |

The seven unavailable local-evidence checks are the FDA nozzle retained
contract hash and six open-boundary campaign/remedy/physical-scope checks.
They fail closed because ignored campaign archives are not present; they were
not copied into or mutated by this integration.

### Focused integration checks

- Geometry/provenance/execution union: 230 Python tests passed.
- Project/schema/result-link union: 46 frontend tests passed.
- Streamline/derived/result-identity backend: 36 tests passed.
- Preview/streamline/derived frontend: 51 tests passed.
- Multi-edge full-O-grid identity and inlet-manifest regression: 17 tests
  passed.
- Governed preview, negative-streamline, max-seed performance, imported-derived
  rejection, and linked-derived browser scenarios: 6 Playwright tests passed.

Negative controls cover imported and fixture results, thinned previews,
unmatched artifacts, stale project/map hashes, source-cell-count mismatch,
ambiguous cells, paths outside the case or under `mesh/`, planar SU2 volume,
SU2 streamlines without stable identity, timestamp/geometry/cell-order drift,
missing velocity, budget overflow, and the numerically unqualified Y-junction
UI option.

### Visualization budgets

- Steady streamlines retain 64 default / 256 maximum seeds, 1,024 vertices per
  line, 65,536 total vertices, and 256 passive sprites. The reviewed branch
  evidence measured 1.30 ms p95 derivation at 256 seeds and 5,695 vertices in
  0.793 s for the real artifact. Integration E2E also passed the `<16 ms`
  max-seed cinema-frame budget.
- Derived products retain 64³ default / 96³ maximum volume grids, 48 MiB full
  source-artifact admission, 96 MiB browser residency, 256 MiB per-job cache,
  512 seeds, 250,000 pathline vertices, and 500,000 iso triangles. Overflow is
  rejected; it is never silently clamped.

## Readiness states

1. **Software completeness:** Complete for this bounded integration scope. All
   reviewed code is reconciled and source-controlled tests are green.
2. **Mesh/solver execution:** No new native solver campaign was run. Historical
   input-branch execution evidence is retained: elbow execution completed;
   Y-junction execution completed but failed numerical qualification;
   axisymmetric qualification remains incomplete/blocked.
3. **Numerical verification:** No state change. Curved elbow remains a bounded
   qualification candidate; Y-junction failed its frozen GCI gate;
   axisymmetric/full-path identity remains blocked where documented.
4. **Independent validation:** Not established by this integration.
5. **Promotion:** Not authorized. No benchmark or campaign pointer was promoted.
6. **Release:** Not performed. The branch is local, unpushed, and is not a
   notarized or portable external release.

## Unresolved limitations

- The seven ignored campaign-evidence checks require their governed local
  archives before they can be rerun in this isolated worktree.
- Axisymmetric qualification requires a new prospectively declared recovery;
  this integration does not resolve the retained signature mismatch.
- Y-junction needs a new frozen hypothesis/campaign to address the failed GCI
  gate before UI exposure can be reconsidered.
- SU2 result-cell identity is not yet strong enough for derived product
  admission.
- Derived visualization remains ASCII VTK/VTU, bounded linear-cell,
  visualization-only functionality; it does not support arbitrary binary,
  higher-order, adaptive, or general production CFD data.
