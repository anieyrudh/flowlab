# Bounded symmetric Y-junction qualification runbook V5

Status: **prospective contract frozen before first V5 OpenFOAM execution**

The machine-readable authority is `QUALIFICATION_CONTRACT_V5.json`. V1 through
V4 evidence remains retained and immutable. In particular, do not edit
`benchmarks/cases/y-junction/campaigns/2026-07-31-v4-r1`.

V4 passed every per-case and negative-control gate, but its three independently
generated staircase masks failed the frozen sequence gates: observed order was
`0.43937081563777536` and fine GCI was `16.84937428352456%`. V5 does not change
those thresholds. It tests the prospectively declared hypothesis that one
fixed generated domain is needed to measure solution-grid convergence without
changing the staircase volume and port areas at each level.

## Fixed-master construction

Generate the V4 coarse 0.75 mm mask once. For the coarse, medium, and fine
levels, subdivide every retained master hexahedron by factors 1, 2, and 4 in
each coordinate direction. Children inherit their parent's explicit inlet,
upper-branch, lower-branch, or junction region. Exterior child faces inherit
their master's patch identity.

The subdivision must never re-run primitive membership, region classification,
or patch classification. The junction retains the dedicated generated
artifact identity and no schematic owner.

Before execution, require:

1. identical `masterGeometrySha256`, total volume, and patch-area maps;
2. 5,328 / 42,624 / 340,992 cells;
3. one valid parent index per child and exactly factor-cubed children per
   master cell;
4. every region count scaled by factor cubed and every boundary face count
   scaled by factor squared;
5. duplicate complete generated-file hashes;
6. one connected all-hex region, exactly three ports, positive volumes, and
   fail-closed explicit ownership;
7. the exact committed source and locally resolved immutable OpenFOAM image;
8. a fresh absent or empty campaign directory.

## Execution and observation

OpenFOAM writes patch flow, patch pressure, and fixed `cellPoint` probes every
25 iterations. In addition to all inherited V4 gates, each case requires at
least five common inlet/upper/lower patch-pressure samples in its inclusive
final 200-iteration window. The primary pressure-drop relative range in that
window must be no greater than 0.001.

After committing the frozen source:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m server.flowlab.y_junction_campaign \
  benchmarks/cases/y-junction/campaigns/2026-07-31-v5-r1 \
  --run-and-package
```

The runner executes three equal-pressure cases followed by the fine asymmetric
negative control. Once execution begins, the campaign directory is immutable
except to the owning runner.

## Stop rule and claim boundary

If V5 fails any inherited V4 threshold, fixed-master hierarchy gate, iterative
stability gate, observed-order bound of 0.5 to 4, or fine GCI maximum of 5%,
retain the failure and stop. Do not change the contract.

A complete pass would be software and numerical qualification of this exact
bounded fixed-master generated path. It would not be independent empirical
validation, arbitrary network support, product promotion, or external release.
`promotionAuthorized` remains `false`.
