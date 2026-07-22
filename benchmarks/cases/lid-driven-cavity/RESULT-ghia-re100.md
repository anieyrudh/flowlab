# Lid-driven cavity Re=100 — external benchmark result (Ghia et al. 1982)

**Status:** exploratory · solver-stack accuracy · **not** a promoted or validated FlowLab claim.

## What this measures (and what it does not)

This runs a **laminar, steady** lid-driven cavity in the pinned OpenFOAM 11 container
(`flowlab/openfoam11-gmsh:2026-07-13`, `linux/amd64` under emulation on an arm64 host)
and compares centreline velocity profiles to the canonical reference:

> U. Ghia, K.N. Ghia, C.T. Shin, *High-Re solutions for incompressible flow using the
> Navier–Stokes equations and a multigrid method*, J. Comput. Phys. 48 (1982) 387–411.

It measures the **solver stack FlowLab orchestrates**, **not** FlowLab's own case-generation
pipeline — FlowLab's mesher only expresses through-flow channel geometry
(`SUPPORTED_EDGE_TYPES = {pipe, venturi, expansion, contraction, nozzle}`), so a closed
lid-driven cavity cannot be generated natively. Emulated-container evidence supports
**correctness only**, never native-performance claims (consistent with `AGENTS.md`).

## Setup

- OpenFOAM 11 `foamRun -solver incompressibleFluid`, `ddtSchemes steadyState` (SIMPLE).
- Convection `bounded Gauss linearUpwind grad(U)`; `pRefCell/pRefValue` pinned (closed domain).
- `simulationType laminar`; Re = U·L/ν = 1·0.1/1e-3 = **100**.
- Three uniform grids **32² / 64² / 128²** (refinement ratio 2) for a Richardson/GCI study.

## Result

**Grid convergence (self-convergence, reference-independent):**

| QoI | 32² | 64² | 128² | observed order *p* | GCI(fine), Fs=1.25 |
|---|---|---|---|---|---|
| u_min (vertical centreline)   | −0.21054 | −0.21318 | −0.21368 | 2.41 | **0.067 %** |
| v_min (horizontal centreline) | −0.24936 | −0.25290 | −0.25330 | 3.15 | **0.025 %** |
| v_max (horizontal centreline) |  0.17627 |  0.17869 |  0.17903 | 2.83 | **0.039 %** |

The solution is **grid-converged to < 0.1 %** with observed order 2.4–3.2 (consistent with
the second-order-upwind momentum scheme).

**Agreement with Ghia 1982 (fine grid, 128²):**

- Full centreline profiles: **u ≈ 0.23 % RMS, v ≈ 0.48 % RMS** (max ~0.9 %).
- Peak vortex QoIs: **u_min 1.3 %, v_max 2.1 %, v_min 3.3 %** relative.

## Interpretation

The converged solution sits ~1–3 % from Ghia's 1982 *peak* values while being self-consistent
to < 0.1 %. This gap is within the known spread between Ghia's finite-difference values and
modern spectral references (e.g. Botella & Peyret 1998); i.e. it is largely **reference error**,
not solver error. The full-profile RMS agreement (~0.2 %) is the more robust accuracy statement.

**Takeaway:** the solver stack is grid-converged to < 0.1 % and reproduces the canonical
cavity benchmark to sub-percent on the centreline profiles.

## Artifacts & reproducibility

- `ghia-re100-report.json` — consolidated machine report (grids, GCI, per-point errors).
- `ghia-re100-profiles.svg` — profiles vs Ghia plot.
- `runs/re100_n{32,64,128}/` — raw OpenFOAM cases + logs (git-ignored per evidence boundary).
- Harness (scratchpad): `run_cavity.sh`, `analyze_cavity.py`, `compute_gci.py`, `finalize.py`.
