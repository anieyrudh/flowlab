# Axisymmetric generated-geometry experimental qualification v3

V3 is a **new standalone prospective contract**, not a revision of V1 or V2. Read
`EXPERIMENTAL_QUALIFICATION_CONTRACT_V3.json` together with `RUNBOOK_V1.md`,
`RUNBOOK_V2.md`, and `BLOCKED_HANDOFF_2026-07-31.md`.

Nothing here authorizes validation, promotion, release, a registry or fixture
pointer change, or any change to the existing stricter straight-pipe campaign.

## Predecessor disposition

The V1 and V2 campaign directories are immutable retained failures:

- `benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v1-r1`
- `benchmarks/cases/axisymmetric-geometry/campaigns/2026-07-31-v2-preflight-r1`

Do not edit, resume, compact, delete, or reuse them. **No V1 or V2 observation
may be presented as a V3 outcome.** V3 starts from zero.

## What V3 corrects, and why it is not a relaxation

The V1 mesh gate froze `geometricDirections: 3`. OpenFOAM classifies a wedge as:

```
Mesh has 2 geometric (non-empty/wedge) directions (1 1 0)
Mesh has 3 solution (non-empty) directions (1 1 1)
```

So the V1 condition is **unsatisfiable for any wedge topology**. It was not a
statement about mesh quality; it was copied from the solution-direction line.
The retained V1 mesh was otherwise healthy: `Mesh OK`, positive volume, one
region, and exactly the six required patches.

There was a second, independent defect in the evaluator: it searched for the
literal text `Mesh has 3 geometric (non-empty) directions`. OpenFOAM 11 always
writes the geometric line with the `(non-empty/wedge)` classification, so **even
correcting the count alone would still never match**. V3 parses the reported
integers instead of matching a fixed string.

Asserting 2 geometric **and** 3 solution directions together is strictly more
discriminating than V1's unsatisfiable pair. This is a correction toward a
stronger satisfiable gate, not a weakening.

Every other threshold, level, physical case, trend, identity rule, retention
rule, and non-promotion flag is carried over from V1 **unchanged**. The single
changed gate value is `geometricDirections`, from 3 to 2. The identity algorithm
carries forward the value V2 established.

## Recorded product decision

The wedge route is wanted for round, straight, axisymmetric parts; the
full-revolution O-grid route covers the general case. The two are complementary.
This decision is recorded in the contract under `reviewDecision`, and it is what
authorizes opening V3 rather than closing the qualification as a retained
failure.

## Execution

Commit the exact V3 contract, runbook, evaluator, and tests first.
`_source_control_identity` runs `git status --porcelain` over the frozen paths
and refuses to launch the solver if any is dirty. There is no bypass.

Choose a new empty ignored directory:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 \
  -m server.flowlab.axisymmetric_geometry_qualification \
  benchmarks/cases/axisymmetric-geometry/campaigns/<new-v3-run-id> \
  --materialize-only
```

`--materialize-only` leaves its target non-empty, so use a **second** new empty
directory for `--run`.

## Retention and status

Raw campaigns stay in the ignored `benchmarks/cases/axisymmetric-geometry/campaigns`
tree. Never edit retained evidence in place. Regardless of outcome, `validated`,
`promotionAuthorized`, and `releaseAuthorized` all stay false, and no fixture
pointer, registry entry, validated preset, or `benchmarks/tracked-evidence.txt`
entry may change. Record the outcome in a new dated handoff and leave the
existing handoff unchanged.
