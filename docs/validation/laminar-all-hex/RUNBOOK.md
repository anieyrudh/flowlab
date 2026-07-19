# Campaign runbook

## 1. Contract preflight

```bash
PYTHONPATH=. pytest -q \
  server/tests/test_laminar_all_hex_v3.py \
  server/tests/test_laminar_all_hex_v4.py
```

Proceed only when manifest identity, source hashing, the pinned common-floor
diagnostic, staged convergence, MPI scaling, and primary-QoI contract tests
pass.

## 2. Scientific launch

Each worker receives one immutable cell manifest and a unique writable
directory through the pinned Docker image. Source and upstream evidence mounts
are read-only. The scheduler permits at most one fine-grid job per host.

```bash
PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_v4_campaign_runner \
  --capacity 4 \
  --output benchmarks/cases/open-boundary/campaigns/<campaign-v4>

PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_analysis \
  --campaign benchmarks/cases/open-boundary/campaigns/<campaign-v4> \
  --output benchmarks/cases/open-boundary/campaigns/<campaign-v4>/factorial-analysis.json
```

Do not advance unless the campaign report status is
`numerical-lanes-accepted` and every primary numerical gate is true.

## 3. Conditional follow-ups

Run these serially on a capacity-four host to avoid cross-process fine-grid
resource interference:

```bash
PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_v4_confirmation \
  --campaign <campaign-v4> --output <followups>/confirmation-r1 \
  --capacity 4

PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_v4_reproducibility \
  --campaign <campaign-v4> --output <followups>/reproducibility-r1 \
  --capacity 4

PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_controls \
  --campaign <campaign-v4> --output <followups>/controls-r3

PYTHONPATH=. python3 -m server.flowlab.laminar_all_hex_v4_final_assessment \
  --campaign <campaign-v4> \
  --followups <followups> \
  --experimental-assessment <experimental-dataset-assessment.json> \
  --output <followups>/final-assessment-r2
```

The first product-control run is intentionally retained because it exposed a
v4 report-identity defect. `controls-r3` is authoritative and cites
`product-evaluator-amendment-r2.json` through the final assessment.

## 4. Failure handling

- Retry an infrastructure failure at most once with identical inputs.
- Never retry a scientific failure with changed inputs.
- Record raw observations, limits, source hashes, interactions, and a minimal
  reproducer.
- Confirm a scientific signature independently before assigning root cause.
- Do not reinterpret exit code 2 as infrastructure failure when a complete
  rejected structured report exists.

## 5. Promotion and pointer rule

`benchmarks/cases/open-boundary/campaigns/validated-campaign-pointer.json`
selects one digest-checked final assessment. It may point to a rejected report
so the desktop can display current blockers, but no runnable action is exposed
unless every required final gate is true and `promotionAuthorized=true`.

Never edit solver source, scientific limits, or a completed campaign manifest
to promote an existing run. Mobile remains out of scope.
