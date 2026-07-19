# Laminar all-hex v3 campaign contract

Status: **frozen before execution**

Campaign ID: `laminar-all-hex-v3`

This campaign replaces the v2 fixed-iteration execution policy. It does not
change the validated regime, solver image, mesh families, physical checks, or
acceptance thresholds.

## Scientific matrix

- affine grid invariance at 12/24/48 cells per axis;
- non-affine, boundary-compatible manufactured solution at 12/24/48;
- 72 physical cells spanning Reynolds number, flow direction, length/height,
  axial cell aspect ratio, and coarse/medium/fine mesh level;
- six independently repeated cells selected before execution from the exact v2
  residual-sensitive set;
- serial, two-rank, and four-rank reproducibility on the predeclared
  representative physical cell;
- all existing negative controls and desktop product-contract controls;
- independent experimental validation.

There are 78 primary scientific cells: 3 affine, 3 non-affine MMS, and 72
physical-envelope cells.

## Dynamic stopping rule

For every physical cell, the host supervises a steady OpenFOAM solve in
100-iteration stages:

1. do not accept before iteration 300;
2. at each checkpoint, inspect the last 25 consecutive iterations;
3. require both `Ux` initial residual below `1e-6` and pressure initial
   residual below `1e-8` for every iteration in that window;
4. stop at the first checkpoint satisfying the joint sustained rule;
5. reject if the rule is not satisfied by the 2,000-iteration hard cap.

The solver restarts from `latestTime`, retains only the latest time directory,
and reconstructs the force and direct face-audit artifacts at the accepted stop
time. The convergence history, stage logs, first sustained passing iteration,
final window, and hard-cap state are retained per cell.

## MPI equivalence

Primary physical QoIs use the frozen `1e-6` relative serial/parallel limit:

- open-boundary pressure force;
- open-boundary integrated viscous force;
- wall viscous force;
- mass imbalance.

Direct serial/parallel field differences use the norm of the corresponding
analytic field as the scale, with a limit of `1e-10`. This avoids normalizing a
small decomposition difference by an already-small numerical error. The v2
error-norm-relative ratios remain recorded as diagnostic quantities and are not
promotion gates.

## Promotion rule

Promotion is conjunctive. The desktop preset remains unavailable unless every
primary cell, confirmation, reproducibility, control, product-contract, and
experimental-data gate passes in the final machine assessment. No subset or
post-hoc envelope may be promoted under this campaign ID.

Mobile is out of scope.
