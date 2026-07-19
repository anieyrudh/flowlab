# Laminar all-hex v4 campaign contract

Status: **completed; numerical lanes accepted**

Campaign ID: `laminar-all-hex-v4`

V4 preserves every v3 solver input, mesh family, boundary condition,
scientific tolerance, force definition, and MPI equivalence rule. It changes
only the physical-cell termination policy so every grid level is compared at a
common iterative state.

## Predeclared remedy evidence

A 12-cell diagnostic sampled four representative three-grid groups at a fixed
1300 iterations. All 12 cells and all four groups passed the existing gates;
order spread improved to `0.3058`–`0.4640`. The one initial failed write was
repeated identically after storage cleanup and accepted, supporting transient
storage I/O rather than a scientific failure.

## Common-floor stopping rule

For every physical cell:

1. do not stop before iteration 1300;
2. at checkpoint 1300 and every 100 iterations thereafter, inspect the latest
   25 consecutive iterations;
3. require axial initial residual at or below `1e-6` and pressure initial
   residual at or below `1e-8` for the entire window;
4. stop at the first passing checkpoint;
5. reject if no passing window exists by iteration 2000.

The minimum applies to the stop iteration, so the first eligible window ends at
1300. All 72 completed v4 physical cells stopped at 1300; no cell reached the
hard cap.

## Matrix and dependent gates

- 3 affine 12/24/48 cells;
- 3 non-affine boundary-compatible MMS cells;
- 72 physical cells over Reynolds number, direction, length/height, axial cell
  aspect, and coarse/medium/fine level;
- 6 independent repeats of the historically residual-sensitive set;
- 2 cold repeats plus 2- and 4-rank MPI;
- 6 negative controls and 4 desktop product contracts;
- independent experimental validation.

The primary scientific count is 78. Promotion is conjunctive: a numerically
accepted campaign remains unavailable in the desktop until the compatible
experimental dataset and every other final gate pass. Mobile is out of scope.
