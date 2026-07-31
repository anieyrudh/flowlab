# Axisymmetric generated-geometry experimental qualification runbook v1

Status: **prospective experimental-software contract; no retained result observed at freeze**

The machine-readable authority is
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V1.json`. This campaign qualifies only the
bounded software path for straight-axis circular wedge geometry. It does not
validate CFD, promote a fixture or preset, change a campaign pointer, or
authorize a release.

## Frozen scope

Generation-only cases cover Venturi, contraction, expansion, and nozzle edges.
The runtime case is a five-edge inlet-pipe, contraction, throat, expansion, and
recovery path at three exact logical refinements. The existing
`axisymmetric_straight_pipe_campaign.py` campaign and all of its thresholds and
retained evidence remain unchanged.

SU2 does not implement the axisymmetric wedge or full O-grid paths. Those
requests must fail closed and may not fall back to a planar mesh.

## Pre-execution gate

Before any retained solve:

1. commit the contract, runner, evaluators, result-identity implementation, and
   focused tests;
2. require a clean exact source commit;
3. hash this contract and bind the hash into every runtime request;
4. resolve `flowlab/openfoam11-gmsh:2026-07-13` to an immutable local image ID;
5. materialize every generation-only case and runtime level twice and require
   identical generated-file hashes;
6. require a generated source-cell identity contract with ordering assumptions
   prohibited;
7. use a new empty ignored campaign directory.

If any condition fails, record infrastructure or contract failure separately
and stop before solver execution.

## Execution

Run from the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.axisymmetric_geometry_qualification \
  benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v1-r1 \
  --run
```

The runner must use `adapters.generate_case`, `materialize_case_files`, and
`JobManager.queue_job`. It must stop before the next level after a mandatory
failure. It may not invoke an alternate direct solver path, reuse an existing
output directory, edit a retained case, or tune a frozen threshold.

## Required evaluation

Each level requires exact boundary patches, a three-dimensional positive-volume
mesh, `checkMesh` success, finite `p` and `U`, normal solver termination, mass
imbalance no greater than 0.1%, pressure-tail span no greater than 0.5%, and
flow-tail span no greater than 0.1%.

The three-level pressure-drop sequence requires a valid observed order from 0.5
through 4.0 and fine-grid GCI no greater than 5%. The fine result must show the
prospectively declared contraction, throat, and recovery trends.

Result linkage requires a one-to-one mapping between actual OpenFOAM `polyMesh`
cell vertex signatures and generated source-cell signatures. The mapping is
embedded as `flowlabSourceCellId` in the FlowLab-native result. Full and bounded
preview loading must retain it. Only one declared edge range may own a selected
source cell; connector or otherwise unowned cells remain probe-only.

## Evidence and handoff

Raw cases, meshes, fields, logs, histories, and reports remain in the ignored
campaign tree. The handoff reports the exact branch SHA, contract SHA-256,
container tag and image ID, generated-case hashes, per-level gates, result
identity hashes, focused and full test results, limitations, and likely
integration conflicts.

No fixture, registry, validated pointer, retained-evidence allowlist, promotion,
or release file may change.
