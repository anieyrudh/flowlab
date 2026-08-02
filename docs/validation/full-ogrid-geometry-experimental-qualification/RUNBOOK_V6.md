# Full-O-grid generated-geometry experimental qualification v6 (DRAFT)

**This runbook is a draft. V6 is not frozen, no runner loads it, and no campaign
may cite it.** Read `RUNBOOK_V3.md`, `RUNBOOK_V4.md`, `RUNBOOK_V5.md`,
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json`, and
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V6.json` together.

## Predecessor disposition

V3 and V4 retained directories are immutable. V5 was frozen but never executed
and no retained evidence references it; it stays frozen and byte-unchanged, and
its merge patch is carried forward into V6 verbatim so the identity algorithm
and the two checkMesh parser repairs are preserved.

## What V6 changes

V6 changes execution only, plus one gate that polices the iterative state.

1. **Opt-in MPI.** `--mpi-ranks N` runs each level with N scotch-decomposed
   ranks and `reconstructPar -latestTime` before evaluation, so every downstream
   evaluator reads the same reconstructed artifacts as a serial run. Authorized
   rank counts are 1, 2, 4 and 8. The runner refuses any other value, and
   refuses every value above 1 unless the active revision declares
   `runtime.efficiency.parallel`.
2. **Explicit iteration control.** The frozen 2,000-iteration budget is
   restated as `fixed-common-iteration-count` and gated: a level whose solver
   log shows a different iteration count, or that stopped early on
   `residualControl`, fails closed.
3. **Mesh sequencing is declared but NOT authorized.** The mechanism, its
   provenance requirements and its reproducibility consequence are written down
   so a later revision can enable it with a one-field change, but it was
   measured to be no help on this case. See below.

No geometry, physics, mesh, boundary condition, grid level, QoI, expected
trend, scientific threshold, relaxation factor, iteration budget, product
status, or promotion gate changes.

## Why the stopping rule is not touched

Measured on the frozen case with the pinned image: the pressure initial
residual plateaus near `1e-4` at coarse from about iteration 400 and near
`7e-8` at medium from about iteration 200, and never falls further at either
level. The generated `residualControl` value is `1e-8` on `p` and `U`, so it
never fires and every level already runs the full 2,000 iterations.

Two consequences follow. First, **no common absolute residual criterion is
reachable across this grid sequence**, so the v4-style "common floor plus
residual window" cannot be transplanted here; the fixed common count is what
keeps the iterative state comparable. Second, replacing the fixed count with a
per-level early stop would reproduce the laminar-all-hex v3 failure directly,
where levels stopped at different iterative states and 18 of 24 order-spread
groups failed. V6 therefore keeps 2,000 and adds a gate that makes any
divergence from it a closed failure.

## Why mesh sequencing is not authorized

`mapFields` initialization of a finer level from the converged coarser level is
scientifically safe: a converged steady solution does not depend on its initial
condition. It is implemented, opt-in, and validated - the mapped `internalField`
is spliced into the generated field file so every `boundaryField` entry,
including the `fullOGridParabolicInlet` `codedFixedValue`, is preserved
byte-for-byte, and the case manifest is rebuilt over the mapped fields.

It also does not work here. The medium level was run twice at 2,000 iterations,
once cold-started from the generated uniform field and once initialised from the
converged coarse solution:

| medium level | durable stationarity | converged pressure drop | solver wall time |
| --- | --- | --- | --- |
| cold start | iteration 185 | 0.023462337 | 51.920 s |
| mapped from coarse | iteration 202 | 0.023462337 | 49.410 s |

Sequencing moved stationarity **later**, not earlier. The mapped velocity field
does start closer - first-iteration `Ux` initial residual 9.0e-2 against 1.0
cold - but the pressure field still has to be re-established on the finer mesh,
and by iteration 100 the mapped run is behind (`p` initial residual 2.5e-3
against 1.7e-4). The coarse source is itself only converged to a 1e-4 residual
plateau, so it is not a high-quality initial condition. The wall-time difference
is noise; both runs completed the same 2,000 iterations.

On top of that, under a fixed 2,000-iteration budget a better initial condition
would save no wall time anyway, while it converts three independent level runs
into one ordered chain that can no longer be reproduced level by level. Zero
measured benefit for a real reproducibility cost, so it stays off.

It becomes worth revisiting only alongside a prospectively declared smaller
fixed common iteration count *and* a source level converged well enough to be a
useful initial condition. Durable QoI stationarity was measured at iteration 379
for coarse and 185 for medium. **The fine level was not measured, and no smaller
budget may be declared until it is.**

## Why relaxation factors are unchanged

A pressure-field relaxation sweep, one run per setting, 2,000 iterations, with
the `U` equation relaxation held at 0.7. "Stationary from" is the first
iteration after which the frozen acceptance windows hold for every later
iteration.

Medium level (19,968 cells):

| `p` relaxation | stationary from | converged pressure drop | outcome |
| --- | --- | --- | --- |
| 0.3 (current) | iteration 185 | 0.023462337 | stable |
| 0.5 | iteration 186 | 0.023462336 | stable |
| 0.7 | iteration 187 | 0.023462337 | stable |
| 0.9 | never | `nan` | **diverged at iteration 1,417** |

Coarse level (2,496 cells):

| `p` relaxation | stationary from | converged pressure drop | trailing-100 span |
| --- | --- | --- | --- |
| 0.3 (current) | iteration 379 | 0.019982992 | 2.52e-3 |
| 0.5 | iteration 557 | 0.019980925 | 1.59e-3 |
| 0.7 | iteration 265 | 0.020000320 | 1.29e-4 |

**Nothing changes.** At medium the three stable settings are indistinguishable -
two iterations out of 185, and a pressure drop agreeing to eight significant
figures. At coarse, 0.7 does give a tighter limit cycle than 0.3, but 0.5 is
worse than both, so the response is non-monotone and one unreplicated run per
setting cannot separate a real effect from the phase of an oscillation. At 0.9
the solve diverges, and it looked healthy for 1,416 iterations first, so a short
rehearsal would not have caught it. This case is laminar and low-Reynolds and
its cost is dominated by the pressure-Poisson inner solve, not by
outer-iteration under-relaxation. `p` 0.3 and `U` 0.7 are restated as frozen
inputs so a later revision cannot change them silently.

Incidentally, the coarse converged pressure drop moves in its fifth significant
figure across relaxation settings, about `1e-3` relative. Pressure drop is the
QoI feeding the Richardson extrapolation, so the coarse level carries roughly
`1e-3` relative iterative uncertainty in the grid-convergence input. That is an
observation about the existing frozen configuration, not a proposed change.

## Execution

Commit the exact V6 contract, runner, identity implementation, and tests first;
`_source_control_identity` refuses to run with any frozen path uncommitted.

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.full_ogrid_geometry_qualification \
  benchmarks/cases/full-ogrid-geometry/campaigns/<new-v6-run-id> \
  --materialize-only
```

Then the same command with `--run` against a second new empty directory, adding
`--mpi-ranks 4` if parallel execution is wanted.

The parallel equivalence requirement is not optional: before any level's
parallel result is used, run the same mesh and numerics serially and confirm the
pressure drop and measured volumetric flow rate agree to `1e-6` relative. Wall
time is not evidence of equivalence.

## Expected cost

Measured on an Apple Silicon host with 16 CPUs allocated to Docker, running the
pinned amd64 image under emulation. Per-iteration cost falls sharply once
residuals drop, so do not extrapolate a full run from its first 200 iterations.

- Coarse (2,496 cells): about 16 ms per iteration.
- Medium (19,968 cells): 104 ms per iteration over the first 200; 26 ms averaged
  over the full 2,000 (51.9 s for the whole level).
- Fine (159,744 cells): 1,957 ms per iteration over the first 200.

Parallel solver speedup, fixed 200 iterations:

| level | serial | 2 ranks | 4 ranks | 8 ranks |
| --- | --- | --- | --- | --- |
| coarse (2,496) | 16.4 ms/it | 16.9 ms/it (**slower**) | - | - |
| medium (19,968) | 20.888 s | 11.568 s (1.81x) | 6.559 s (3.18x) | 4.416 s (4.73x) |
| fine (159,744) | 391.414 s | - | 104.039 s (3.76x) | - |

At the medium level's full 2,000 iterations, four ranks took 15.684 s against
51.920 s serial: 3.31x on solver time, 2.35x end-to-end once the 5.8 s
`decomposePar` and 0.6 s `reconstructPar` are included. Parallel efficiency
rises with problem size, so run the fine level in parallel and **keep the coarse
level serial** - `decomposePar` alone costs more there than the whole solve.
These numbers are specific to this host, this Docker CPU allocation and this
emulated image, and are not a portable performance claim.

Serial and four-rank results on the medium level at its full 2,000 iterations
agree to every printed digit: pressure drop 0.023462337 in both, inlet and outlet
flow rates agreeing to 2e-8 relative. At the fine level truncated to 200
iterations, still deep in its transient, the two differ by 4.4e-4 relative -
which is why equivalence must be assessed at the declared stopping point, not at
a truncated one, and must be demonstrated per campaign rather than inherited from
this note.

Note that the RUNBOOK_V5 expectation of about 13 minutes for the medium level
came from extrapolating a small scratch case at a constant cost per
cell-iteration. Measured, the medium level takes 51.9 s of solver time. The fine
level at its full budget has **not** been measured, so no full-campaign
wall-clock figure here is measured end to end.

All V3 scientific, identity, retention, and nonpromotion requirements remain
conjunctive and unchanged. `promotionAuthorized` stays false regardless of
outcome.
