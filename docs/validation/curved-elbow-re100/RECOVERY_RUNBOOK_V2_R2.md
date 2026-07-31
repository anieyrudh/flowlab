# Curved-elbow V2 R2 diagnostics recovery

## Authority and scope

This runbook governs a parser-only re-evaluation of the immutable
`2026-07-31-v2-r1` solver campaign. It does not rerun CFD, edit retained
evidence, change the V2 contract, weaken a gate, authorize promotion, or turn
bounded numerical qualification into independent validation.

The source campaign completed OpenFOAM at all three levels. Coarse and medium
passed every per-level gate. Fine completed with exit code 0 at iteration 3000
but the job was classified as infrastructure failure because FlowLab could not
parse Foundation's component-wise vector extrema table in
`wallShearStress.dat`; the size-capped VTK fallback used on smaller levels was
not eligible for the fine surface file.

## Frozen source binding

- source campaign:
  `benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r1`;
- source solver commit:
  `2caede4223b04a5a66ecd49e2b93baec05295a5c`;
- source artifact-manifest SHA-256:
  `92c45e23af11a5bd155bee65511a33d07c794e92fa7d296160ff3bc7e3d2de5b`;
- source tree digest:
  `bf23029ef2df6732f842b795b23cd24dff6c1c7fb74a91faf97e00abde0ca2c6`;
- V2 contract SHA-256:
  `4c5117a6a6d396c2abd8dff8a66b17e7eab0452da6138a9f8edf39cb9014eadf`.

The recovery command must verify every source artifact size and SHA-256 before
evaluation. Any drift fails closed.

## Parser correction

The parser may accept the native two-vector extrema row but must retain it as
component-wise extrema in solver vector units. It must not invent pointwise
minimum, mean, maximum, magnitude, or Pascal values from that table.

## Execution

From a clean committed checkout:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.curved_elbow_campaign \
  benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r2-recovery \
  --recover-from \
  benchmarks/cases/curved-elbow-re100/campaigns/2026-07-31-v2-r1
```

The output must be new, separate, and made read-only after completion. It must
bind both the original solver-source commit and the clean evaluator commit.

## Interpretation

Qualification is a bounded numerical candidate only if all three immutable
levels pass every existing V2 per-level gate and the unchanged observed-order
and fine-grid GCI gates pass. `validated` and `promotionAuthorized` remain
false regardless of the result.
