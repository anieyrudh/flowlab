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
| CFD, before a result exists, with a generated-case mesh | Canonical generated-case non-planar mesh | `Generated-case mesh preview` |
| CFD, before a result exists, with no generated-case mesh | The stylized concept drawing, plus the notice below | `Concept preview` |
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

## The concept-drawing notice in CFD

A generated case does not always have a mesh file to show. The CFD stage then
keeps the stylized concept drawing. A user must not read that drawing as the
mesh their case will solve on, so the product states the fact and the setting
that changes it. The notice reads:

```text
Not this case’s mesh
```

The reason below the title is one of these two exact strings:

- `The case is meshed in planar 2D, which has no 3D preview, so this stays the
  concept drawing. Pick a 3D mesh mode in Solver settings to preview the real
  mesh.` — when the mesh mode is `planar-2d` or is not set;
- `The generated case has not produced a mesh file to preview yet, so this
  stays the concept drawing.` — for every other mesh mode.

This notice states a product fact to a user. It is not a developer note about
which preview generator remains in the source tree, and it must not be reworded
into one.

## Retained fallbacks and boundaries

- The flat-canvas renderer is an explicit fallback. It does not become a
  separate scientific authority. Its two exact labels name which of the two
  causes applies: `Simplified view — 3-D graphics turned off` when the user
  turned it on with the `Simplified` control, and
  `Simplified view — 3-D graphics are unavailable on this machine` when the
  machine has no WebGL. `Simplified` is a renderer choice, not a camera
  orientation; the named camera planes are `Iso`, `XY`, `XZ`, and `YZ`.
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

## Retained in the source tree, not shown as authority

Everything in this section stays in the repository. None of it is a product
surface, and none of it may be quoted to a user as a state label.

- The planar and source-strip preview generators are not selected as
  generated-case mesh authority. Their generators, tests, benchmark
  definitions, and retained evidence remain unchanged. The product does not
  name them. Where the concept drawing stands in for a mesh, the user reads the
  `Not this case’s mesh` notice above instead.
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

The concept-drawing notice keeps the CSS class name `legacy-preview-notice`.
That class name is history, not a description. Do not restore developer wording
to that element because of its class name.
