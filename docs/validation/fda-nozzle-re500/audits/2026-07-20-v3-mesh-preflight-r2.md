# FDA Re=500 V3 mesh-only preflight

Status: **mesh-preflight-passed**

Pressure is formally nonpromotional. This preflight assesses only the new strict-all-hex mesh family; it does not run a solver or authorize scientific or desktop promotion.

| Level | Status | Cells | Boundary segments | Volume error | Inlet error | Outlet error | Throat error |
|---|---|---:|---:|---:|---:|---:|---:|
| coarse | passed | 44,256 | 16 | -2.550464% | -2.550464% | -2.550464% | -2.550464% |
| medium | passed | 354,048 | 32 | -0.641315% | -0.641315% | -0.641315% | -0.641315% |
| fine | passed | 2,832,384 | 64 | -0.160561% | -0.160561% | -0.160561% | -0.160561% |

## Gates

- `pressureDispositionResolvedNonpromotional`: pass
- `allMeshCommandsComplete`: pass
- `allMeshesOpenFoamOk`: pass
- `allMeshesStrictHex`: pass
- `allExpectedCellCountsExact`: pass
- `volumetricCellRatioEight`: pass
- `allGeometryQuantitiesConvergeMonotonically`: pass
- `allFineGeometryErrorsWithinOnePercent`: pass
- `onlyMeshCommandsInvoked`: pass
- `frozenContractIntegrity`: pass

Later numerical uncertainty must be labelled `combined-geometry-and-solution-discretization`. A passing mesh preflight authorizes only design of the next numerical-verification campaign.
