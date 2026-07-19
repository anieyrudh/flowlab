# Result Field Inventory - 2026-06-14

FlowLab now exposes loaded VTK/VTU field arrays as a first-class desktop field
inventory in the bottom analysis dock.

## What Changed

- Added normalized point/cell field inventory metadata for loaded results:
  field name, location, scalar/vector kind, tuple count, min, max, and matching
  overlay when one is known.
- Added explicit result-field selection separate from the coarse overlay mode.
  This lets the user pin `cell p` even when `point p` also exists, or inspect
  solver-specific custom fields without changing the project schema.
- Canvas rendering and probe sampling use the explicitly selected result field
  when one is pinned. If that field is missing in the active timestep, FlowLab
  reports it as unavailable instead of silently falling back to an overlay
  field. Overlay mapping is used only when no explicit field is pinned.
- The dock lists point and cell fields with compact location badges and min/max
  ranges, keeping the center canvas dominant and uncluttered.
- The field list now has a compact filter box. Users can narrow loaded fields
  by field name, point/cell location, scalar/vector kind, inferred unit, or
  overlay mapping, with a visible shown/total count and no-match state.
- Loaded fields now carry conservative inferred units for common CFD outputs:
  pressure (`Pa`), velocity (`m/s`), temperature (`K`), phase/residual values
  (`1`), density (`kg/m3`), force (`N`), moment (`N m`), power (`W`), and heat
  flux (`W/m2`). The field list, source text, histogram, min/max readout, and
  trend summaries, coverage labels, and probe samples display those units
  without claiming solver-specific unit systems for unknown custom arrays.
- The dock also summarizes loaded solver snapshots as a compact field trend:
  each timestep reports the active field's mean and max, remains clickable, and
  marks missing pinned fields without inventing fallback values.
- The dock now adds a field coverage summary across loaded snapshots, reporting
  how many timesteps contain the active overlay or pinned point/cell field and
  listing missing snapshot labels when a solver output drops that array.
- The dock adds a compact distribution histogram for the active point/cell
  scalar or vector-magnitude field, using the same values as the color scale,
  field trend, and probe sampling.
- The active field readout now includes deterministic descriptive statistics:
  count-backed mean, population standard deviation, P50, and P95, with the same
  unit metadata as the histogram and probe readout.
- The active field trend can be exported as CSV. Rows preserve every loaded
  snapshot, including missing-field rows, so downstream analysis keeps the same
  coverage evidence shown in the dock.
- Pinned vector fields now expose a component selector for magnitude, X, Y,
  and Z. The chosen component drives the canvas color scale, min/max readout,
  histogram, trend, and probe sampling while arrows still show the underlying
  vector direction.
- Loaded result fields now expose selectable Turbo, Viridis, Thermal, and
  Grayscale colormaps. The selected map drives the VTK/VTU canvas fill colors
  and the bottom-dock color ramp while instant 1D overlays keep their
  established overlay palettes.
- Time-series playback now has deterministic previous/next frame controls, a
  speed selector, and a loop/hold toggle so users can scrub progressive or
  completed result snapshots without losing the pinned field context.
- Snapshot ordering uses parsed solver times when available. OpenFOAM VTK names
  such as `case_50.vtk` now map against zero-based `logSummary.timeSteps` when
  the solver log includes `Time = 0`, avoiding an off-by-one display time for
  `foamToVTK` outputs.
- The bottom dock now includes a solver diagnostics panel. It reads normalized
  `logSummary` residuals, latest time/iteration, warnings/errors, and
  `diagnosticSummary` table/excerpt rows so residual convergence and solver
  diagnostic files remain visible after the advanced case card scrolls away.

## Validation

Focused validation run:

```bash
npm test -- --run src/results/vtk.test.ts src/App.test.tsx
python3 -m pytest server/tests/test_mesh_results.py -q
```

Results:

- Frontend focused tests cover the VTK parser and app-level result dock flows.
- Backend result/mesh focused tests: `16` tests passed.

## Remaining Limits

- Field inventory and coverage summaries cover loaded in-memory ASCII VTK/VTU
  snapshots and bounded preview snapshots. Full large-result streaming beyond
  bounded preview/chunk loading is still pending.
- The canvas remains a 2D projected viewer. Section cuts, volume rendering,
  streamlines, derived fields beyond common field/unit inference, and full
  multi-file residual/time-series plotting remain future work.
