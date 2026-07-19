# Error taxonomy

| Kind | Meaning | Campaign treatment |
|---|---|---|
| Error | Metric deviation, failed gate, or missing expected quantity | Preserve raw evidence and confirm independently |
| Conflict | Trustworthy evidence supports incompatible conclusions | Block affected claim until reconciled |
| Interference | One factor changes another factor's observed effect | Record interaction and avoid one-factor causal claims |
| Infrastructure | OOM, container, compiler, permission, or transfer failure | One identical retry; never count as scientific pass |
| Provenance | Image, source, input, artifact, or threshold hash mismatch | P0 fail closed |
| Known limitation | Scope deliberately unavailable or excluded | Document and block only affected claims |

Error sources are classified further as specification, analytic/manufactured
solution, discretization, iterative convergence, boundary implementation,
mesh, force/post-processing reduction, floating-point/decomposition,
experimental input/measurement, or orchestration/provenance.

Relative metrics with analytically zero denominators are prohibited. Use an
absolute scale or a physically meaningful reference, and retain the raw
quantity so apparent improvements cannot hide cancellation.
