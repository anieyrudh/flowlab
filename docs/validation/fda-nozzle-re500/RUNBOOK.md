# FDA Re=500 campaign runbook

Run from the repository root. The campaign refuses to overwrite an existing
nonempty output directory.

```bash
export CAMPAIGN="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1"
export IMAGE="flowlab/openfoam11-gmsh415-immutable:2026-07-14-arm64-v1"

PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500 prepare \
  --output "$CAMPAIGN" \
  --experiment-archive /path/to/pinned/SE_exp_0500.zip
```

Before execution, verify the archive hash is
`c33d8d604c072edd0298890274e9b8d625bb39281e2b691c51dac639aaf95d58`.
The prepare command enforces it.

Build the retained direct face-integration utility in the pinned environment:

```bash
docker run --rm -v "$PWD:$PWD" \
  -w "$PWD/benchmarks/tools/flowlabFdaPatchAudit" "$IMAGE" bash -lc \
  "source /opt/openfoam11/etc/bashrc; \
   export FOAM_USER_APPBIN='$CAMPAIGN/bin'; wmake"
```

Execute the nominal grids and input-sensitivity cases. Independent cases may be
run concurrently when host memory permits.

```bash
for label in coarse medium fine input-minus-5pct input-plus-5pct; do
  PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500 execute-case \
    --output "$CAMPAIGN" --label "$label" --image "$IMAGE"
done
```

Postprocess every completed case. This runs OpenFOAM probes, the force function
object reconciliation, boundary-flow checks, and the independent per-face
pressure/viscous-traction integration.

```bash
for label in coarse medium fine input-minus-5pct input-plus-5pct; do
  PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500 postprocess-case \
    --output "$CAMPAIGN" --label "$label" --image "$IMAGE"
done
```

Only after every observation is present, calculate three-grid GCI, all four
uncertainty contributions, ASME V&V 20 comparison errors, and the fail-closed
promotion decision:

```bash
PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500 assess \
  --output "$CAMPAIGN"
```

Exit code 0 means every mandatory gate passed. Exit code 3 means the assessment
completed but promotion remains blocked. Inspect `REPORT.md`, `assessment.json`,
and `issues.jsonl`; do not reinterpret code 3 as an infrastructure failure.

After successful postprocessing, iterations 50 through 700 may be deleted. Keep
the initial case, iterations 750 and 800, solver/checkMesh logs, probe map,
observations, direct integration CSVs, source archive, contract, issue ledger,
and final assessment.

## V2 preflight matrix

The completed diagnostic preflight is under
`benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight`. It is a
frozen 2^3 matrix over short/extended outlet, bounded/second-order scheme, and
base/enhanced local resolution. To reproduce it from the immutable v1 evidence:

```bash
export V1="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v1"
export PREFLIGHT="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight"
export IMAGE="flowlab/openfoam11-gmsh415-immutable:2026-07-14-arm64-v1"

PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight prepare \
  --output "$PREFLIGHT" --v1 "$V1"

for label in \
  short__bounded__base short__bounded__enhanced \
  short__second-order__base short__second-order__enhanced \
  extended__bounded__base extended__bounded__enhanced \
  extended__second-order__base extended__second-order__enhanced; do
  PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight \
    execute-case --output "$PREFLIGHT" --label "$label" --image "$IMAGE"
  PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight \
    postprocess-case --output "$PREFLIGHT" --label "$label" --image "$IMAGE"
done

for label in coarse medium fine input-minus-5pct input-plus-5pct; do
  PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight \
    reanalyze-v1-case --output "$PREFLIGHT" --label "$label" --image "$IMAGE"
done

PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight assess \
  --output "$PREFLIGHT"
```

The frozen assessment selected `short__second-order__enhanced`, passed all
eight numerical gates and all four matched outlet checks, and wrote a
`frozen-ready-to-prepare` full-campaign contract. The extended-enhanced cases
are retained as iterative-risk evidence: every pressure solve reached its
1,000-iteration cap and each run took roughly 25,500 seconds.

The next authorized execution is the three-grid v2 campaign defined by
`v2-full-campaign-contract.json`. It must retain the exact inlet velocity,
fixed outlet pressure, strict-all-hex topology, source-backed PIV averaging,
offset-free pressure differences, all uncertainty terms, direct integration,
force reconciliation, and every existing promotion gate. Preflight success is
not scientific validation and must not unlock the desktop UI.

After the assessment and report exist, compact only reproducible raw fields:

```bash
PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_preflight compact \
  --output "$PREFLIGHT"
```

## V2 fine-grid infrastructure recovery from time 750

The original full-v2 fine solve in `2026-07-19-re500-v2-full` was interrupted
after writing time 750. It did not reach time 800 and did not produce an
assessment. Preserve that directory unchanged. The original one-shot contract
in `V2_FINE_RECOVERY_CONTRACT.json` is exhausted: its first launch failed before
solver time because of the space-bearing host path, and the corrected r2 launch
was OOM-killed at time 773 by the 7.9 GB Docker VM. Preserve both failed
recovery directories and retained containers unchanged.

The user prospectively authorized one r3 infrastructure recovery in
`V2_FINE_RECOVERY_R3_CONTRACT.json`. It retains the identical serial solver,
pinned native-arm64 image, source checkpoint, end time, and scientific gates.
Before launch it requires Docker Desktop to expose 16 CPUs, approximately 16 GB
RAM, approximately 4 GB swap, and native arm64. It also requires append-only
five-second resource telemetry. The infrastructure allocation and telemetry do
not change or relax any numerical or scientific criterion.

Materialize a separate recovery directory. Preparation verifies the exact
failed-run records, rescue log, 23-file checkpoint snapshot, image digest, and
frozen campaign contract. It copies only the retained fine state and the
already completed lanes, then changes exactly one cloned line:
`startFrom startTime;` to `startFrom latestTime;`.

```bash
export SOURCE="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-19-re500-v2-full"
export RECOVERY="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v2-fine-recovery-from-750-r3"
export RECOVERY_CONTRACT="$PWD/docs/validation/fda-nozzle-re500/V2_FINE_RECOVERY_R3_CONTRACT.json"
export CONTAINER="flowlab-fda-v2-fine-recovery-20260720-r3"

PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_campaign \
  prepare-fine-recovery --source "$SOURCE" --output "$RECOVERY" \
  --contract "$RECOVERY_CONTRACT"

PYTHONPATH=. python3 -m server.flowlab.fda_nozzle_re500_v2_campaign \
  run-fine-recovery --output "$RECOVERY" --container "$CONTAINER"
```

The recovery container is named and deliberately not auto-removed. If the
host-side finisher is interrupted, rerun only `finalize-fine-recovery` against
the retained container; do not create another solver container. Finalization
requires container exit code 0, a written time-800 checkpoint, `Time = 800s`,
and terminal `End` before it creates a successful fine execution record. Only
then does it run the existing postprocessing and unchanged fail-closed
assessment. Resource telemetry is retained at
`logs/fine/resource-telemetry.jsonl` and hashed into the execution and final
assessment. Recovery success is not scientific acceptance; every original gate
still decides promotion.
