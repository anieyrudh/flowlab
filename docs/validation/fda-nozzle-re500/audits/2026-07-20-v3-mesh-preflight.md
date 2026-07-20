# FDA Re=500 V3 mesh-only preflight

Status: **mesh-preflight-blocked**

Pressure is formally nonpromotional. This preflight assesses only the new strict-all-hex mesh family; it does not run a solver or authorize scientific or desktop promotion.

| Level | Status | Cells | Boundary segments | Volume error | Inlet error | Outlet error | Throat error |
|---|---|---:|---:|---:|---:|---:|---:|
| coarse | failed | not available | 16 | not available | not available | not available | not available |
| medium | not-run | not available | 32 | not available | not available | not available | not available |
| fine | not-run | not available | 64 | not available | not available | not available | not available |

## Failure classification

- Classification: `infrastructure-preparation-failure`
- Stage: `coarse-blockMesh`
- Reason: `missing-required-controlDict`

## Gates

- `pressureDispositionResolvedNonpromotional`: pass
- `allMeshCommandsComplete`: fail
- `allMeshesOpenFoamOk`: fail
- `allMeshesStrictHex`: fail
- `allExpectedCellCountsExact`: fail
- `volumetricCellRatioEight`: fail
- `allGeometryQuantitiesConvergeMonotonically`: fail
- `allFineGeometryErrorsWithinOnePercent`: fail
- `onlyMeshCommandsInvoked`: pass
- `frozenContractIntegrity`: pass

Later numerical uncertainty must be labelled `combined-geometry-and-solution-discretization`. A passing mesh preflight authorizes only design of the next numerical-verification campaign.
