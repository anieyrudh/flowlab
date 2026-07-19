# OpenFOAM Advanced Mode Matrix - 2026-06-12

FlowLab ran the OpenFOAM smoke path through Docker for the advanced modes below. Each command used the same server-side case generation, `JobManager` execution, result collection, diagnostic parsing, and quality gate used by the API queue.

Base command shape:

```bash
npm run smoke:openfoam -- --advanced-mode <mode> --runtime-root /tmp/flowlab-openfoam-matrix-<mode>-runtime --output /tmp/flowlab-openfoam-matrix-<mode>.json --timeout 300
```

Observed matrix:

| Mode | Status | Completed | Execution | Exit code | Latest time | Result files | Diagnostic files |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| `heat-transfer` | `complete` | `true` | `docker` | `0` | `0.05` | `4` | `2` |
| `compressible-flow` | `complete` | `true` | `docker` | `0` | `0.001` | `4` | `2` |
| `multiphase-vof` | `complete` | `true` | `docker` | `0` | `0.05` | `4` | `2` |
| `cavitation` | `complete` | `true` | `docker` | `0` | `0.05` | `4` | `2` |
| `conjugate-heat-transfer` | `blocked` | `false` | `none` | n/a | n/a | `0` | `0` |

Completed modes produced solver VTK outputs under `VTK/`, including whole-case and inlet/outlet/wall patch files. Completed modes also collected:

- `postProcessing/residuals/0/residuals.dat`
- `postProcessing/wallForces/0/forces.dat`

The diagnostic parser summarized residual and force tables for each completed mode.

The conjugate heat-transfer mode was intentionally blocked before solver launch with `Generated case validation failed.` At the time of this matrix, FlowLab emitted a `foamMultiRun`-shaped guardrail but lacked true multi-region OpenFOAM case generation with fluid/solid region dictionaries and interfaces.

Update on 2026-06-14: FlowLab now emits a `foamMultiRun` fluid/solid multi-region starter bundle with region-scoped `0/`, `constant/`, and `system/` files, plus split fluid/solid `polyMesh` directories with paired mapped-wall starter patches. The solid region is now an outward offset starter sleeve instead of a duplicate fluid strip. Runtime remains blocked because the generated interface manifest is not production-ready; production CHT still needs CAD-quality solid topology, boundary-layer/y-plus evidence, and per-region `checkMesh` evidence. See [openfoam-cht-multiregion-generation-2026-06-14.md](openfoam-cht-multiregion-generation-2026-06-14.md).

Limitations:

- These are coarse FlowLab starter cases, not production CFD validation benchmarks.
- The mesh remains a thin port-aware extrusion suitable for local smoke execution and result plumbing, not boundary-layer-resolved CAD meshing.
- CHT remains incomplete until the split region interface mesh is production-ready; the region dictionary bundle and mapped-wall starter meshes now exist.
