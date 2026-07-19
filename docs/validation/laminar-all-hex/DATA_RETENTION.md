# Validation data retention

Status: **legacy raw data compacted; current v4 evidence retained**

On 2026-07-16 the benchmark tree was reduced from approximately 63 GB to
14 GB after the v4 numerical campaign and desktop QA completed. Available disk
space increased from 143 GiB to 191 GiB, recovering approximately 49 GB.

## Retained in full

- `2026-07-16-laminar-all-hex-v4-campaign-r1`, including all 78 execution
  attempts;
- `2026-07-16-laminar-all-hex-v4-followups`;
- the digest-checked validated-campaign pointer and v4 final assessment;
- the affine-lane instrumented OpenFOAM library at
  `runs/2026-07-15-forced-mms-v10/trace-outlet-constrain/lib/`
  (`SHA-256 157ac7086c82dcf5bc7fd1fde1a3ff83e8cc3710ae9b83d9d085711a97f076de`)
  and its minimal reproducible source/build log;
- source code, schemas, campaign contracts, current issue/interference ledgers,
  and desktop product tests.

## Retained in compact form

- v2/v3 campaign reports, manifests, cell `result.json` files, event streams,
  issue records, and follow-up assessments;
- historical run JSON, JSONL, CSV, Markdown, text, log, hash, and YAML evidence
  files no larger than 2 MB;
- all small pinned reports required by the desktop benchmark registry.

## Deleted

- v2/v3 `execution` and `execution-attempt-*` trees;
- legacy OpenFOAM meshes, time directories, decomposed processor fields, VTK
  fields, archived run payloads, compiled run-local binaries, and other
  generated non-report files;
- historical structured files larger than 2 MB that embedded bulky mesh or
  field payloads.

The compaction is intentionally not reversible from the workspace alone.
Reproducing deleted data requires rerunning the frozen historical contract.
The current v4 campaign remains the audit source for active numerical claims;
its raw evidence must not be compacted until the external experimental gate is
resolved and a separate archive decision is recorded.

## Post-cleanup integrity check

The first post-cleanup backend regression correctly detected that the
instrumented affine-lane library had been removed by the broad legacy-run
rule. The binary was rebuilt from the recorded mixed-provenance recipe: module
sources seeded from
`flowlab/openfoam11-gmsh415-immutable:2026-07-14-arm64-v1`, including that
image's `moveMesh.C`, then compiled in
`flowlab/openfoam11-gmsh:2026-07-13` for `linux/amd64`. The rebuilt binary
reproduced the frozen SHA-256 exactly. The library, source files, and build log
are now explicit retention exceptions; transient object files and `lnInclude`
links are not.
