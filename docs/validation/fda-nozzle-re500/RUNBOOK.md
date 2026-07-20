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

That preflight historically authorized the three-grid v2 campaign defined by
`v2-full-campaign-contract.json`. The campaign has since completed through the
r3 fine-grid recovery described below and was scientifically blocked. The old
authorization is exhausted: do not use it to rerun v2 or unlock the desktop UI.

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

The retained r3 solve completed at time 800 with terminal `End`, but the frozen
assessment returned exit code 3 and status `validated-blocked`. Preserve the
directory unchanged. The failed gates are axial velocity, pressure validation,
and complete three-grid GCI.

## Prospective successor preflight

Before spending compute on another full campaign, reproduce the read-only
reference and grid-family audit. It hashes every input, refuses to overwrite an
existing output, and never writes into either retained campaign:

```bash
export R3="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v2-fine-recovery-from-750-r3"
export V2_SOURCE="$PWD/benchmarks/cases/fda-nozzle/campaigns/2026-07-19-re500-v2-full"
export V3_CONTRACT="$PWD/docs/validation/fda-nozzle-re500/V3_DIAGNOSTIC_CONTRACT.json"

PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.fda_nozzle_re500_successor \
  --campaign "$R3" \
  --fine-check-mesh "$V2_SOURCE/logs/fine/checkMesh.log" \
  --contract "$V3_CONTRACT" \
  --output /path/to/new/successor-preflight.json
```

Exit code 3 is the expected fail-closed result while either blocker remains:

- pressure-reference provenance/covariance is insufficient for promotion; or
- geometry discretization has not been prospectively bounded or separated from
  solution discretization in a successor grid-family preflight.

Pressure-reference qualification is now resolved prospectively by
`PRESSURE_REFERENCE_DISPOSITION.json`: pressure is a mandatory diagnostic but
is nonpromotional. The successor context of use is axial-velocity-field
agreement for the bounded FDA Re=500 configuration.

The first mesh-only attempt was frozen by `V3_MESH_PREFLIGHT_CONTRACT.json` and
used these three commands:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.fda_nozzle_re500_v3_mesh_preflight prepare \
  --output benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-mesh-preflight \
  --contract docs/validation/fda-nozzle-re500/V3_MESH_PREFLIGHT_CONTRACT.json \
  --pressure-disposition docs/validation/fda-nozzle-re500/PRESSURE_REFERENCE_DISPOSITION.json

PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.fda_nozzle_re500_v3_mesh_preflight mesh-all \
  --output benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-mesh-preflight \
  --max-workers 1

PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.fda_nozzle_re500_v3_mesh_preflight assess \
  --output benchmarks/cases/fda-nozzle/campaigns/2026-07-20-re500-v3-mesh-preflight \
  --compact-output docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-mesh-preflight.json \
  --compact-report docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-mesh-preflight.md
```

Those commands are a historical record, not rerun instructions. The attempt
returned exit 3 at coarse `blockMesh` because the prepared case lacked the
required `system/controlDict`. No mesh was constructed, medium/fine were not
started, no solver command was invoked, and the compact assessment records an
infrastructure-preparation failure. Preserve that raw directory. The runner now
generates a minimal mesh-only `controlDict`.

The correction was exercised once under the separately frozen
`V3_MESH_PREFLIGHT_R2_CONTRACT.json` and new
`2026-07-20-re500-v3-mesh-preflight-r2` output. It passed all mesh and geometry
gates: exact 44,256 / 354,048 / 2,832,384 all-hex cells, 8x cell ratios, full
topology/geometry checks, monotonic domain-measure convergence, and 0.1606%
fine-grid errors against the 1% contract. Preserve that campaign unchanged.
Its compact tracked result is
`audits/2026-07-20-v3-mesh-preflight-r2.json` and `.md`.

The pass authorizes only design of a prospectively frozen velocity-focused
numerical-verification campaign. It does not authorize running a solver, a full
successor campaign, or any scientific/desktop promotion. Later GCI must be
labelled combined geometry-and-solution discretization uncertainty.

## Velocity-verification design validation

The design-only contract is
`V3_VELOCITY_VERIFICATION_DESIGN_CONTRACT.json`. Validate it offline with:

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
  python3 -m server.flowlab.fda_nozzle_re500_v3_velocity_design \
  --contract docs/validation/fda-nozzle-re500/V3_VELOCITY_VERIFICATION_DESIGN_CONTRACT.json \
  --output docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-velocity-design-validation.json \
  --report docs/validation/fda-nozzle-re500/audits/2026-07-20-v3-velocity-design-validation.md
```

The retained assessment is `design-valid-execution-blocked`: all 22 design
checks pass, while every execution and promotion authorization remains false.
The validator has no campaign-preparation, Docker, solver, or postprocessing
surface and refuses to overwrite its outputs.

The intended design has six serial cases and a 12.87-hour planning estimate,
but no raw campaign directory has been prepared. Do not interpret the contract,
estimate, or offline validation as permission to run it. Independent review,
final runner/assessment hashes, a separate execution contract, explicit user
authorization, verified resources, and an absent output path are all required
before preparation.

## Hugging Face infrastructure qualification

`HF_INFRASTRUCTURE_QUALIFICATION_CONTRACT.json` prospectively permits only the
nonpromotional qualification sequence: tiny architecture and revision-pinned
volume probes, build and immutable pinning of the equivalent AMD64 image, one
coarse serial pilot on each of `cpu-upgrade` and `cpu-xl`, local recovery and
rehashing, assessment, and independent review. It explicitly denies the
six-case campaign and automatic phase progression.

The first architecture probe is retained as Job
`6a5dbfbdd216bd6f3a202dfe`. It reported `amd64` but failed before artifact
creation with HTTP 403 because the connected credential lacked private-dataset
creation rights. No solver was invoked. The local AMD64 image and mesh-only
coarse input bundle were prepared, but this does not satisfy remote image,
volume, solver, or recovery gates. Do not launch either coarse pilot until a
new unique probe demonstrates a durable write, revision-pinned read-only mount,
and write-back under the frozen repository and path contract.

Resume by reconnecting Hugging Face with a credential that can create and write
private dataset and private Space repositories under `Anieyrudh`, while
retaining Jobs permission. Never put the token in a file or command line. The
failed Job and current local raw directory are append-only; do not reuse a Job
ID, evidence prefix, or remote result path.

Independent review found that r1 could not safely accept a positive result.
Preserve it unchanged. Any resume must use
`HF_INFRASTRUCTURE_QUALIFICATION_R2_CONTRACT.json` and the r2 module, image,
probe scripts, and new raw output directory. R2 requires an exact non-source
policy hash, complete source hashes, unique tar members, atomic lane uploads,
all four terminal residuals from the final time block, cgroup/process-tree/OOM
telemetry, Space/commit/registry/Job-image binding, local artifact-manifest
rehashing, and an evidence-hash-bound independent review. A successful r2
assessment remains nonpromotional and does not itself launch six cases.

A stationary cloned-fine diagnostic, independent-solver run, or new full
campaign requires a separate prospective contract and the applicable earlier
stage in `V3_DIAGNOSTIC_CONTRACT.json` to pass. Never weaken the r3 gates or
rerun from time zero to make the retained outcome pass.
