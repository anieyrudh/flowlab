# Bounded symmetric Y-junction final assessment

Status: **software path implemented; experimental numerical qualification
failed and retained**

Date: 2026-07-31

Promotion authorized: **no**

Independent empirical validation: **not performed**

## Decision

FlowLab now has one deterministic true-3D OpenFOAM path for the prospectively
declared symmetric Y-junction:

- one 27 mm inlet and two identical 27 mm circular branches at `+30` and
  `-30` degrees;
- 6 mm diameter, steady incompressible laminar water flow, nominal `Re=100`;
- equal outlet pressures plus one lower-pressure-outlet negative control;
- explicit construction-time `inlet-pipe`, `upper-branch`, and
  `lower-branch` source-cell ranges; and
- a separate unowned `generated:y-junction:junction-core:v1` cell range with
  no schematic owner.

The topology, ownership, deterministic generation, OpenFOAM execution,
result-binding, and per-case physics gates are implemented and exercised.
The bounded path is **not experimentally numerically qualified**, because the
final prospectively frozen V4 sequence missed both convergence gates:

- observed order `0.43937081563777536`, required `0.5 <= p <= 4`; and
- fine-grid primary-QoI GCI `16.84937428352456%`, required `<= 5%`.

The V4 stop rule therefore applies. No gate was changed, no retained evidence
was edited, and no further post-hoc mesh or observation tuning was run.

## Frozen authority and retained package

The V4 authority is
`QUALIFICATION_CONTRACT_V4.json` with SHA-256
`d6aa0c911a34fa13db3e1a7f57b2de5aee1ec422f9fbe3f47897d0a03b1fe4cd`.
Its runbook SHA-256 is
`c728f593491e31fd0510faa9a1e1e161401dbeb78d48a2d7e62b1f78f72e21da`.

The campaign was executed from source commit
`1b0054a73b04b1bc0c9b411ec5d8620e7b775545` in the fresh ignored directory
`benchmarks/cases/y-junction/campaigns/2026-07-31-v4-r1`.

The retained evidence identities are:

- campaign manifest:
  `fb43dfe18ba02abd71e0f8bf5b55abbac8d2b94ab2f4d3febfeacc28ad45cd99`;
- campaign assessment:
  `8a11c43aa0ff076d58d2a4cf3c3fa6393ab7f4eebb7be9257b5a685ab2f643d3`;
- immutable package manifest:
  `21465353cdea1a3590b2e2287a762e417f2d120a2a73fe2599102943efca67ea`;
- immutable package tree:
  `b7877c9be15d285dc49d1a05cac75b01ecdc0fe8a5474d0cd8aac7aae4143315`;
  and
- OpenFOAM image:
  `flowlab/openfoam11-gmsh@sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
  on `linux/amd64`.

Raw cases and the 680-file immutable package remain ignored local evidence.
They are not admitted to Git.

## Deterministic generation and ownership

Every case was materialized twice. The complete generated-file hash maps
matched.

| Case | Cell size | Cells | Generation SHA-256 | Generated-file tree SHA-256 |
|---|---:|---:|---|---|
| coarse | 0.75 mm | 5,328 | `8b648d2e2f8dfb9c59ba8b544053e65db46a59553672a5a0ca918a784bb3619a` | `bbae4466d4a00946e5b6e35e86685ffd8670e18af7b5c77d2e59090c742538a4` |
| medium | 0.375 mm | 42,460 | `ae72e02823f716511cbed19885b1bb6f841bf8a5606e19ea12ffcab72f382ac8` | `18039720cff2e2b9458bdb117f151381959c98e03c634330724328196cc7ac3a` |
| fine | 0.1875 mm | 336,472 | `ac9f7cca9c227092b370d950c85508e30b92884ac82e1c26169aa0dd60ea4933` | `d66322ab4e6f191acb4c0f7db3f4d859ace935455d7d974bb86e3cd29c5161ba` |
| fine asymmetric control | 0.1875 mm | 336,472 | `ac9f7cca9c227092b370d950c85508e30b92884ac82e1c26169aa0dd60ea4933` | `b1b4428bdb799d5ccc9057e6b9425f568a4c40805c8ddecb71f2e6107344ad98` |

The fine equal-pressure and asymmetric cases have identical mesh generation
identities; their complete file trees differ because the declared outlet
pressure boundary condition differs.

Fine-grid result ownership is:

| Region | Cell start | Cell count | Schematic ownership |
|---|---:|---:|---|
| `inlet-pipe` | 0 | 97,440 | explicit edge |
| `upper-branch` | 97,440 | 96,550 | explicit edge |
| `lower-branch` | 193,990 | 96,550 | explicit edge |
| `generated:y-junction:junction-core:v1` | 290,540 | 45,932 | none |

The ranges are construction artifacts, not viewer geometry inference.
Junction cells are outside every branch edge range. Result linking fails
closed for the generated-unowned range.

## Per-case results

All four cases passed `checkMesh`, had one connected all-hex fluid region,
positive minimum cell volume, exactly the declared inlet, two outlet, and wall
patches, finite `p` and `U`, zero solver exit status, and a normal retained
termination marker.

| Equal-pressure case | Minimum volume (m3) | Mass imbalance | Upper flow fraction | Mirrored p error | Mirrored U error | Branch-drop difference | Pressure-drop QoI (Pa) |
|---|---:|---:|---:|---:|---:|---:|---:|
| coarse | 4.21875e-10 | 2.98197e-8 | 0.500000010 | 3.68080e-5 | 0.00555907 | 0 | 0.614664195 |
| medium | 5.2734375e-11 | 1.14851e-9 | 0.500000000 | 5.32152e-7 | 0.000747217 | 0 | 0.659761220 |
| fine | 6.591796875e-12 | 4.95816e-10 | 0.499999943 | 3.51374e-6 | 0.000221789 | 0 | 0.693018299 |

These pass the per-case limits of `0.1%` mass imbalance, `0.50 +/- 0.01`
outlet split, `2%` mirrored pressure and velocity error, and `1%`
branch-pressure-drop difference.

The asymmetric fine case also passed its declared negative control:

- upper outlet pressure: `0 Pa` gauge;
- lower outlet pressure: `-0.2 Pa` gauge;
- upper outflow: `1.44665537047e-7 m3/s`;
- lower outflow: `3.31115707263e-7 m3/s`; and
- relative mass imbalance: `1.19593e-8`.

The lower-pressure outlet therefore carried more flow.

## Sequence failure and stop

The equal-pressure primary pressure-drop QoIs were:

1. coarse: `0.6146641946203 Pa`;
2. medium: `0.6597612199256999 Pa`; and
3. fine: `0.6930182988526999 Pa`.

They form a mathematically evaluable monotone sequence at `r=2`, but its
observed order and fine GCI do not meet the frozen qualification thresholds.
This is a scientific qualification failure, not an infrastructure failure and
not a per-case software/runtime failure.

Earlier retained V2 and V3 campaigns had already shown a non-monotone or
negative-order sequence. V4 prospectively moved the unchanged operator to the
finer nested 8/16/32-cells-across-diameter family. Its failure activates the
declared stop condition; the next step, if ever authorized, must be a separate
new hypothesis and contract rather than another tuning iteration.

## Claim boundary

This work establishes software completeness for the bounded generator,
provenance, fail-closed junction ownership, OpenFOAM case path, evaluator, and
visualization path. It does **not** establish:

- experimental numerical qualification of the pressure-drop QoI;
- independent empirical validation;
- product or benchmark promotion;
- arbitrary branching networks, angles, or diameters;
- CAD-exact wall geometry;
- turbulence, transient, multiphase, compressible, or hybrid-mesh behavior;
- native performance evidence; or
- a notarized, portable, or external release.

`promotionAuthorized` and `validated` remain `false`. No validated benchmark
registry or promotion pointer is changed.

