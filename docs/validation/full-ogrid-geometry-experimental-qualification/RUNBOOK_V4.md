# Full-O-grid generated-geometry experimental qualification v4

V4 uses the complete V3 runbook and base contract without changing any
scientific gate. Read `RUNBOOK_V3.md`,
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json`, and
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V4.json` together.

The V3 retained directory is immutable. It stopped before solver launch because
the JobManager incorrectly routed the new direct-blockMesh profile through
`snappyHexMesh`. Do not edit, resume, or reuse it.

For V4, choose a new empty ignored directory and use:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.full_ogrid_geometry_qualification \
  benchmarks/cases/full-ogrid-geometry/campaigns/<new-v4-run-id> \
  --materialize-only
```

After committing the exact V4 contract, runner, routing implementation, and
tests, execute the same command with `--run`. The runtime preflight must record
`surfaceFeatureExtract` and `snappyHexMesh -overwrite` as not applicable and
must execute only `blockMesh` and `checkMesh` before the solver.

All V3 scientific, identity, retention, and nonpromotion requirements remain
conjunctive and unchanged.
