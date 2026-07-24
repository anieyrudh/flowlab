# Full O-grid straight-pipe verification runbook v2

Status: **prospective v2 contract; no retained v2 scientific result observed at
freeze**

This runbook governs the successor to the stopped v1 campaign. The retained v1
failure remains immutable and is not reclassified. V2 preserves the same
physical case, meshes, boundary conditions, discretization, relaxation,
residual controls, and 2,000-iteration budget. It adds per-iteration pressure
and flow histories and prospectively replaces v1's worst-linear-solve decision
with output stability, conservation, and normal-completion gates. The
machine-readable authority is `VERIFICATION_CONTRACT_V2.json`.

## Decision rationale

V1's coarse solution passed mesh, pressure, velocity-profile, and conservation
checks, but failed because one near-zero transverse velocity component and one
continuity sample narrowly exceeded absolute tail thresholds. Those v1 gates
remain failed. They are not a sufficiently direct decision operator for a
bounded engineering claim.

V2 requires the last 100 consecutive SIMPLE iterations to show:

- pressure-drop relative span no greater than `0.0005` (0.05%);
- measured-flow relative span no greater than `0.000025` (0.0025%);
- relative inlet/outlet flow imbalance no greater than `0.0001`;
- normal solver termination; and
- either a SIMPLE convergence declaration or completion of the declared
  2,000-iteration budget.

The pressure stability allowance is one thirtieth of the frozen maximum
fine-grid GCI. The flow stability allowance is one quarter of the existing
conservation and target-flow gates. Residual and continuity tails remain
retained diagnostics.

## Scope and state boundaries

The only candidate claim remains numerical verification of FlowLab's
five-block full-360 O-grid product path for one steady, incompressible,
laminar, constant-diameter straight circular pipe against Hagen-Poiseuille.
This is not independent experimental validation, fixture or registry
promotion, product promotion, or external release.

Keep software completeness, native mesh verification, solver execution,
three-level numerical verification, controlled independent review, promotion,
and release as separate states. `promotionAuthorized` remains `false`.

## Frozen sequence

| Level | Axial | Annular radial | Circumferential | Core side | Cells |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coarse | 16 | 4 | 32 | 8 | 3,072 |
| Medium | 32 | 8 | 64 | 16 | 24,576 |
| Fine | 64 | 16 | 128 | 32 | 196,608 |

Every material discretization dimension doubles. Richardson order and GCI
therefore describe the combined solution-discretization and wall-geometry
realization sequence.

## Pre-execution gate

Before a retained v2 run:

1. preserve both v1 campaign directories without modification;
2. confirm the desktop-release QA prerequisite has a definitive disposition;
3. commit this contract, runbook, campaign code, adapter, execution, topology,
   result parser, schemas, verification utilities, and focused tests;
4. require all frozen paths to be clean at that commit;
5. resolve the configured Docker image to an immutable image ID;
6. materialize every level twice and require identical generated-file hashes;
7. verify that QoI function objects write every iteration; and
8. use a new empty ignored campaign directory.

If any precondition fails, retain the exact disposition and stop.

## Execution

Run from the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.full_ogrid_straight_pipe_campaign \
  benchmarks/cases/full-ogrid-straight-pipe/campaigns/2026-07-24-v2-r1 \
  --run-and-package
```

The runner must use `adapters.generate_case` and `JobManager`, evaluate only
retained outputs, and stop before the next level after any mandatory failure.
It must not substitute a direct `Allrun` invocation or edit retained evidence.

## Evidence and review

Retain generated cases, mesh logs and topology, solver logs, per-iteration QoI
histories, raw VTK fields, patch metrics, per-level evaluation, Richardson/GCI
assessment, source and runtime provenance, and a content-addressed immutable
package. Raw campaigns remain ignored.

A controlled reviewer independent of implementation and execution must bind
their decision to the exact package tree digest. Until acceptance, the highest
state is `verification-candidate-awaiting-independent-review`. Do not alter a
benchmark fixture, registry, validation state, product claim, promotion state,
or release state.
