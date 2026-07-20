# FDA Re=500 V3 velocity-verification compute estimate

Status: **planning estimate; not execution authorization**

The intended six-case design contains two serial fine-grid stationarity/cold-
repeat lanes, one coarse nominal lane, one medium nominal lane, and two medium
input-sensitivity lanes. The estimate scales retained v2 serial solver wall
times by the exact cell-count ratio `2.165590135055784` between the v2 and
accepted V3 mesh families.

| Intended case | Retained v2 basis | Cell-scaled estimate |
|---|---:|---:|
| `coarse-nominal` | 43.88 s | 95.02 s |
| `medium-nominal` | 515.04 s | 1,115.38 s |
| `fine-stationarity-a` | 5,151.81 s | 11,156.71 s |
| `fine-stationarity-b` | 5,151.81 s | 11,156.71 s |
| `input-minus-5pct-medium` | 503.36 s | 1,090.08 s |
| `input-plus-5pct-medium` | 521.96 s | 1,130.35 s |

Baseline solver-only total is approximately **7.15 hours**. Applying a 1.8x
planning multiplier for meshing, postprocessing, pressure-solver variance, and
I/O gives **12.87 hours**. Reserve a **16-hour** run window.

Prospective minimum host allocation for any separately authorized execution:

- pinned native-arm64 image;
- one concurrent solver case;
- 16 GiB Docker memory and 4 GiB swap;
- 20 GiB free disk before preparation;
- append-only resource telemetry;
- no parallel fine-grid lanes.

This is a capacity estimate, not evidence that runtime scales linearly with
cells. It does not authorize campaign preparation, Docker execution, solver
execution, scientific promotion, or desktop promotion.
