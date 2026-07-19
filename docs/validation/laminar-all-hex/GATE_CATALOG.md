# Gate catalog

The executable source of truth is `gate-catalog.json` in each campaign run.
The values below are copied from the existing accepted campaign modules.

## Non-affine MMS

| Gate | Limit |
|---|---:|
| Mass relative imbalance | `1e-8` |
| Final linear residual | `1e-10` |
| Final nonlinear residual | `1e-8` |
| Minimum observed order | `1.5` |
| Maximum order spread | `0.75` |
| Fine GCI relative to analytic norm | `0.01` |
| GCI safety factor | `1.25` |

## Physical force and field benchmark

| Gate | Limit |
|---|---:|
| Mass relative imbalance | `1e-8` |
| Final linear residual | `1e-10` |
| Axial initial residual | `1e-6` |
| Pressure initial residual | `1e-8` |
| Transverse velocity relative L2 | `1e-5` |
| Force object vs direct integration | `1e-10` absolute |
| Analytic pressure force | `1e-8` relative |
| Coarse-through-fine wall viscous force | `0.06` relative |
| Fine wall viscous force | `0.02` relative |
| Fine face traction | `0.03` relative |
| Fine velocity/pressure fields | `0.02` relative |

Every planned cell must be accounted for, every source hash must match, and no
claim-blocking conflict may remain unresolved. Missing empirical evidence
blocks an empirical validation claim without invalidating already accepted
numerical verification evidence.
