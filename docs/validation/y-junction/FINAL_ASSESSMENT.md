# Bounded symmetric Y-junction final assessment

Status: **software path implemented; V5 experimental numerical qualification
failed and retained**

Date: 2026-07-31

Promotion authorized: **no**

Independent empirical validation: **not performed**

## Decision

FlowLab implements one bounded deterministic true-3D OpenFOAM path:

- one 27 mm inlet and two identical 27 mm branches at `+30` and `-30`
  degrees, all nominally 6 mm diameter;
- steady incompressible laminar water flow at nominal `Re=100`;
- equal outlet pressures plus one lower-pressure-outlet negative control;
- explicit construction-time `inlet-pipe`, `upper-branch`, and
  `lower-branch` source-cell ranges; and
- a separate `generated:y-junction:junction-core:v1` range with no schematic
  owner.

V5 prospectively froze the retained V4 coarse Cartesian mask and uniformly
subdivided every master hexahedron by factors 1, 2, and 4. Child region
ownership and exterior patch identity were inherited from the master; neither
was reclassified from geometry.

The fixed-master hierarchy, duplicate generation, `checkMesh`, solver,
conservation, symmetry, branch-drop, iterative-stability, and asymmetric
negative-control gates all passed. The pressure-drop sequence had valid
observed order `1.0014977186639435`, within the unchanged `0.5` to `4` gate.
Fine-grid GCI was `7.248473067832084%`, above the unchanged `5%` maximum.

V5 therefore has terminal status `qualification-gate-failed-retained`.
`validated=false` and `promotionAuthorized=false`. The V5 stop rule applies:
no threshold was changed, no retained evidence was edited, and no V6
experiment is authorized by this assessment.

## Frozen authority and retained evidence

V5 was frozen and executed from source commit
`e5a2ad8ad521bd1c2d7381708ef186d6d8d1dbb5`.

| Artifact | SHA-256 |
|---|---|
| `QUALIFICATION_CONTRACT_V5.json` | `3dad361d124edb4e0b8c91ea641f1781ed3f1b760fa44da15c34a10a0b29ee9c` |
| `RUNBOOK_V5.md` | `6e662968e30d0d3fa6b387ae9377d1865124efa7f491278b0316f138dc8ffdf0` |
| V5 campaign manifest | `04a5d8a0b9d5facf7730efa40a8497bd1fa8c3c3b1eb4f67e1caa2a55b46301d` |
| V5 campaign assessment | `b03f274af078fbb174c1eca550c9baa4f26f5f42cfd2fa7c5fa9e80dbf5fbd65` |
| V5 immutable package manifest | `09b4b5597223517eaae7ff06f684b8d8e009e3006658d8dcab54861da81da637` |
| V5 immutable package tree | `c3c75b176b5e175390f2774abbe2ba7202f3eefcdc3b6d87642f97afae6241ed` |

The fresh ignored campaign is
`benchmarks/cases/y-junction/campaigns/2026-07-31-v5-r1`. Its immutable
package contains 680 files. Raw cases and the package remain ignored local
evidence and are not admitted to Git.

The OpenFOAM runtime was
`flowlab/openfoam11-gmsh@sha256:4fa4e4961b90b0df2781d70b6c033be7e67d324c17e129667469099abf6568fe`
on `linux/amd64`.

V4 remains separately retained and unchanged:

| V4 artifact | SHA-256 |
|---|---|
| campaign assessment | `8a11c43aa0ff076d58d2a4cf3c3fa6393ab7f4eebb7be9257b5a685ab2f643d3` |
| immutable package manifest | `21465353cdea1a3590b2e2287a762e417f2d120a2a73fe2599102943efca67ea` |
| immutable package tree | `b7877c9be15d285dc49d1a05cac75b01ecdc0fe8a5474d0cd8aac7aae4143315` |

## Fixed-master hierarchy and ownership

Every V5 case was materialized twice. Complete generated-file maps and
canonical generation hashes matched.

The common master identity is
`ae7ffe7bac80e83399dc8513de30a7830c775e4333d10c6314bff655d38d17a8`.
All levels have exact generated volume `2.24775e-6 m3` and identical physical
patch areas:

- inlet: `6.525e-5 m2`;
- upper outlet: `6.75e-5 m2`;
- lower outlet: `6.75e-5 m2`; and
- walls: `0.00192825 m2`.

| Case | Factor | Cells | Generation SHA-256 | Generated-file tree SHA-256 |
|---|---:|---:|---|---|
| coarse | 1 | 5,328 | `a5a513f9ecaf046f58d46fc668be550a29ac9ad698c6f02d95b4f3930b58ea00` | `2a1ea9b3f5a6a751082dea01b3a6877b5dda347775dfbaf0dfd8a5338f98f24c` |
| medium | 2 | 42,624 | `8ad2f97cb26d6953410a82155b5a386a236368199d4eecd129dd62dd284dec20` | `c0a0b3a74c9b39ce8a78f3a41ab76c6f82a17cdce74db6482439087d75fc6862` |
| fine | 4 | 340,992 | `6f361c407ea1fc450cf763ea5c17ed0b73b71feae97c1e3ca09459843334d798` | `2d82ce2829a6a2d9b568b8e4620670eb8d5e391ec269f69570c0b51d1717b13f` |
| fine asymmetric control | 4 | 340,992 | `6f361c407ea1fc450cf763ea5c17ed0b73b71feae97c1e3ca09459843334d798` | `7b2ae225176b1c7fdbd21ef1b91e68c165d68b5a4969ea357bec6715c32b7b94` |

Fine-grid result ownership is:

| Region | Cell start | Cell count | Schematic ownership |
|---|---:|---:|---|
| `inlet-pipe` | 0 | 99,840 | explicit edge inherited from master |
| `upper-branch` | 99,840 | 97,408 | explicit edge inherited from master |
| `lower-branch` | 197,248 | 97,408 | explicit edge inherited from master |
| `generated:y-junction:junction-core:v1` | 294,656 | 46,336 | none |

Every fine cell has one master-parent index; every master cell has exactly 64
fine children. Region counts scale by factor cubed and boundary face counts by
factor squared. Junction children remain outside every edge range. Result
linking fails closed for the generated-unowned range.

## Per-case results

All four cases passed `checkMesh`, had one connected all-hex fluid region,
positive minimum cell volume, exactly three port patches plus `walls`, finite
`p` and `U`, zero solver exit code, and normal termination.

| Equal-pressure case | Minimum volume (m3) | Mass imbalance | Upper flow fraction | Mirrored p error | Mirrored U error | Branch-drop difference | Stability range | Pressure drop (Pa) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| coarse | 4.21875e-10 | 2.98197e-8 | 0.500000010 | 3.68080e-5 | 0.00555907 | 0 | 9.25221e-10 | 0.614664195 |
| medium | 5.2734375e-11 | 8.74051e-10 | 0.499904675 | 1.28781e-4 | 8.66707e-4 | 0 | 2.26168e-5 | 0.701282715 |
| fine | 6.591796875e-12 | 4.22632e-8 | 0.500000140 | 2.19053e-6 | 2.63904e-4 | 0 | 7.23406e-8 | 0.744547037 |

These pass the inherited `0.1%` mass-imbalance, `0.50 +/- 0.01` split, `2%`
mirrored pressure/velocity, and `1%` branch-drop gates. They also pass the new
prospective final-window primary-QoI relative-range maximum of `0.001`.

The asymmetric fine case passed its declared negative control:

- upper outlet pressure: `0 Pa` gauge;
- lower outlet pressure: `-0.2 Pa` gauge;
- upper outflow: `1.691473409346e-7 m3/s`;
- lower outflow: `3.183526583548e-7 m3/s`;
- upper outlet flow fraction: `0.346968904987`; and
- relative mass imbalance: `1.45764e-9`.

The lower-pressure outlet carried more flow.

## Sequence failure and interpretation

The equal-pressure primary pressure-drop QoIs were:

1. coarse: `0.6146641946203 Pa`;
2. medium: `0.7012827146402 Pa`; and
3. fine: `0.7445470369278 Pa`.

They form a monotone mathematically qualified `r=2` sequence. Observed order
`1.0014977186639435` passes. Fine-grid GCI `7.248473067832084%` fails the
unchanged `5%` limit. This is a numerical qualification failure, not an
infrastructure, mesh-validity, solver-termination, conservation, ownership,
stability, or negative-control failure.

Compared with V4, fixing the generated domain changed the sequence from order
`0.43937081563777536` and GCI `16.84937428352456%` to approximately
first-order convergence and GCI `7.24847%`. This supports the prospective
hypothesis that changing staircase geometry contaminated V4's convergence
estimate, but it does not make V5 pass and does not establish why the
remaining discretization error exceeds the gate.

Any further test would require a separately authorized hypothesis, prospective
contract, fresh directory, and unchanged treatment of V4/V5 evidence. This
assessment does not authorize that work.

## Claim boundary

This work establishes software completeness for the bounded generator,
fixed-master subdivision, explicit parent provenance, fail-closed junction
ownership, OpenFOAM case path, evaluator, and result visualization. It does
**not** establish:

- numerical qualification of the primary pressure-drop QoI;
- independent empirical validation;
- product or benchmark promotion;
- arbitrary branching networks, angles, or diameters;
- CAD-exact wall geometry;
- turbulence, transient, multiphase, compressible, or hybrid-mesh behavior;
- native performance evidence; or
- a notarized, portable, or external release.

No validated benchmark registry, campaign promotion pointer, UI promotion
state, or release claim is changed.
