# Predeclared interference matrix

| Left factor | Right response/factor | Risk |
|---|---|---|
| Reynolds number | Axial cell aspect ratio | Convective error can amplify anisotropic discretization |
| Reynolds number | Grid resolution | Apparent envelope failure may be under-resolution |
| Length/height ratio | Inlet-development error | Longer domains change boundary influence |
| Boundary condition | Pressure and force accuracy | Flux compatibility can conflict with fixed traces |
| Iterative error | Observed spatial order | Incomplete convergence contaminates GCI |
| Mesh quality | Face-traction accuracy | Gradient reconstruction affects traction first |
| Parallel decomposition | Small-force reduction | Reduction order can dominate near-zero forces |
| Integrated force agreement | Field error | Cancellation can hide local errors |
| Experimental uncertainty | Numerical GCI | Neither component may be ignored in validation uncertainty |
| Resource contention | Runtime/incomplete output | Concurrent execution cannot support performance claims |

## Observed campaign interactions

| Interaction | Evidence | Treatment |
|---|---|---|
| Reynolds number × grid | Largest residual interaction; `Re=66.7`, short channel, coarse/medium cells fail | Convergence rule must be independent of grid and Reynolds number in v3 |
| Iterative error × physical accuracy | Force and field gates pass while nonlinear residual gates fail | Keep both gates; force agreement does not waive convergence |
| Direction × failure | Forward and reverse failures mirror one another | Direction is not the supported root cause |
| Parallel decomposition × small error norm | 2-rank pressure error changes by `4.41e-12`, amplified to `1.1677e-6` when divided by the small serial error | Use a declared analytic/absolute field scale in v3; preserve v2 failure |
| Experimental uncertainty × geometry | Closest data use finite-sidewall imperfect rectangular channels | Match the experiment in a separate campaign; do not map it onto spanwise symmetry |
| Instrumentation × lane | Abandoned r2 showed global diagnostic-library injection contaminated runtime | Affine-only library scope is now enforced |
| Dynamic stopping × grid resolution | V3 compared grid levels at different iterative states; 18/24 groups failed order consistency despite every cell passing | V4 uses a common 1300 minimum; 24/24 groups pass without changing tolerances |
| Common floor × runtime/storage | Fine cells require 1300 iterations and the retained v4 primary tree is materially larger | Keep one fine worker, exclude performance claims, and compact only obsolete evidence |
| Storage pressure × solver writes | One diagnostic residual-file write failed during the old-data pressure window | Preserve the failed log, clean obsolete trees, and require an identical isolated repair |
| Product version identity × promotion gate | The first v4 positive product control was rejected before scientific gate evaluation | Use accepted v4 identity plus a digest-checked evidence pointer; retain a product-only amendment audit |

The campaign emits a machine-readable `interference-register.json` and
`factorial-analysis.json`. New interactions receive issue IDs rather than being
added only as prose.
