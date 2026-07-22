# Straight pipe through FlowLab's PRODUCT pipeline — Track A accuracy result

**Status:** exploratory · FlowLab-pipeline accuracy · **not** a promoted or validated claim.

This runs a straight pipe through FlowLab's own product path — `adapters.generate_case()` →
`execution.JobManager.queue_job()` → OpenFOAM in the pinned container — to measure how accurate
**FlowLab's own generate→mesh→run pipeline** is (distinct from the raw solver, which the
lid-driven-cavity result covers).

## Findings

### 1. The "pipe" is a 2D planar channel, not a 3D cylinder
FlowLab's mesher (`server/flowlab/mesh.py`) emits a **2D, one-cell-thick planar quad strip**
(`frontAndBack` patch type `empty`). The edge `diameter` becomes the channel **gap** `H = diameter × 3.6 m`;
axial length comes from node **pixel spacing × 0.01** (edge `length` is ignored by the mesh).
→ The correct analytical reference is **2D plane-Poiseuille** `Δp = 12·μ·U·L / H²`, **not** the
3D **Hagen–Poiseuille** `128·μ·L·Q/(π·D⁴)` that `benchmarks/cases/straight-pipe/benchmark.json`
currently declares. **The fixture's reference is geometry-mismatched and should be corrected.**

### 2. Mass conservation is exact
The product run reports `flowBalance.relativeImbalance = 0.0` (inlet flow = outlet flow = 0.018165 m³/s,
matching `U·H·Dz`). ✓

### 3. The default pipeline run does not converge
The generated `system/controlDict` is a **50-step transient** (`endTime 0.05, deltaT 0.001`). For any
normal-length pipe this never reaches steady state; `patchMetrics.status = "partial"` and
`pressureDrops = []` — **the product's default output yields no usable pressure drop.** A converged
(steady or long-transient) run mode is needed before pressure accuracy can be assessed by users.

### 4. Mesh accuracy (solved to steady state)
Taking FlowLab's generated mesh unchanged and solving it to steady SIMPLE (Re_H = 20, laminar),
the fully-developed axial pressure gradient vs plane-Poiseuille `12·ν·U/H²`:

| `boundaryLayerLayers` | ~transverse cells | \|dp/dx\| rel. error |
|---:|---:|---:|
| 2 | 5  | 14.49 % |
| 4 | 9  | 6.62 % |
| 8 (FlowLab's finest) | 17 | **3.51 %** |

The mesh **converges** (~1st order) but **plateaus at 3.5%** at the finest transverse setting — right at
the edge of the fixture's own 5% acceptance threshold. The boundary-layer grading clusters cells at the
walls and under-resolves the parabolic core. (Total inlet→outlet Δp is +5.2% vs full-length plane-Poiseuille,
the extra being physical entrance-development excess at this Re.)

## Interpretation

FlowLab's **product pipeline** on its own generated mesh reaches ~3.5% on laminar channel Δp — usable but
markedly coarser than the raw solver stack (which matched the cavity benchmark to <0.5% on a proper mesh).
Two concrete product gaps surfaced: the **default run does not converge** (no Δp), and the **fixture's
analytical reference is for the wrong geometry**. Neither is a solver defect — both are pipeline/config issues.

## Artifacts & reproducibility
- `track-a-jobmgr-result.json` — product pipeline as-is (default transient; mass balance).
- `track-a-steady-result.json` — transverse-refinement sweep solved to steady state.
- `track-a-convergence.svg` — error-vs-resolution plot.
- Harness (scratchpad): `track_a_pipe.py` (drives `generate_case`+`JobManager`), `track_a_steady.py`.
