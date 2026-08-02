# FlowLab preview governance

Status: **implemented UI authority contract; no solver, validation, promotion,
export, or release behavior changed**

This contract gives each workflow stage one authoritative visual surface. It
does not delete retained fixtures or evidence, and it does not turn a visual
preview into scientific evidence.

## Authoritative hierarchy

| Stage | Primary surface | Exact state label |
| --- | --- | --- |
| Define / Estimate | Editable schematic plus instant estimate; adjacent stylized physical interpretation | `Concept preview` |
| CFD, before a result exists | Canonical generated-case non-planar mesh, when present | `Generated-case mesh preview` |
| CFD, after a full case artifact exists | Solver-produced mesh/result artifact for the current case | `Solver-produced mesh` |
| Inspect | The actively loaded result with its fields, probes, and provenance linkage | Label derived from the loaded result state below |

Inspect result states use these exact labels:

- `Solver-produced mesh` for a full result linked to the current solver case;
- `Thinned artifact preview — surface only` for a bounded artifact fallback;
- `Imported result — probe only` for an operator-imported result;
- `Fixture result — developer example · probe only` for the bundled example;
- `No result loaded` when Inspect has no active result.

Full solver output outranks a thinned preview for the same artifact. A thinned
preview becomes primary only when the full result is unavailable.

## Retained fallbacks and boundaries

- `2D projection fallback — WebGL/accessibility/export` is an explicit renderer
  fallback. It does not become a separate scientific authority.
- Thinned artifact previews retain deterministic source-cell provenance but are
  surface-only. They cannot enable streamlines or pathlines.
- Imported VTK/VTU remains probe-only unless a separately verified case linkage
  exists. Importing a file does not infer solver ownership.
- Imported STL preview is setup geometry only. It is not solver-produced
  evidence and does not authorize result-field features.
- `Load fixture result` remains available under `Examples / Developer tooling`.
  It is a developer example and remains probe-only.
- The estimate particle renderer remains in source, but the primary workflow
  defaults it off. Its only opt-in label is
  `Illustrative estimate animation—not CFD`.

## Removed from UI authority, retained for regression

- Legacy planar/source-strip previews are not selected as generated-case mesh
  authority. Their generators, tests, benchmark definitions, and retained
  evidence remain unchanged.
- The bundled Venturi VTK fixture remains at
  `public/fixtures/venturi-result.vtk`; only its product placement and label
  changed.
- Artifact preview APIs, schemas, sampling tests, and regression fixtures remain
  intact.
- No solver case, retained evidence directory, project export field, undo
  history, scientific label, validation record, promotion gate, or release
  state was removed or rewritten.

## Integration notes

This contract was implemented from baseline
`d3bef15698272ac57f73ca6a82b27e0e21c11d8d`. Likely conflict hotspots for
parallel UI work are `src/App.tsx`, `src/components/SimulationCanvas.tsx`,
`src/styles/app.css`, and the editor/workspace E2E specifications. The
selection rules and exact labels are isolated in `src/previewAuthority.ts` so
conflict resolution should preserve that module as the authority rather than
recreating stage logic in the view.
