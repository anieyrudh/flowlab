"""Execute one immutable cell from the laminar all-hex campaign.

The worker intentionally knows nothing about scheduling or promotion. It
materializes one isolated scientific case, evaluates only predeclared per-cell
gates, and writes a deterministic report for later cross-lane aggregation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .laminar_all_hex_campaign import (
    AXIAL_NONLINEAR_RESIDUAL_LIMIT,
    CAMPAIGN_ID,
    COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
    FINE_FACE_TRACTION_RELATIVE_LIMIT,
    FINE_FIELD_RELATIVE_LIMIT,
    FINE_WALL_VISCOUS_RELATIVE_LIMIT,
    FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
    IMAGE_DIGEST,
    MMS_LINEAR_RESIDUAL_LIMIT,
    MMS_MASS_LIMIT,
    NONLINEAR_RESIDUAL_LIMIT,
    PHYSICAL_LINEAR_RESIDUAL_LIMIT,
    PHYSICAL_MASS_LIMIT,
    PRESSURE_FORCE_RELATIVE_LIMIT,
    PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
    TRANSVERSE_VELOCITY_RELATIVE_LIMIT,
    _json_safe,
    _write_json,
)
from .open_boundary_affine_flux_pressure_probe import (
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    run_probe,
)
from .open_boundary_affine_grid_invariance import (
    LEVEL_SCHEMA as AFFINE_LEVEL_SCHEMA,
    LINEAR_SOLVER_TOLERANCE as AFFINE_LINEAR_SOLVER_TOLERANCE,
)
from .open_boundary_laminar_force_benchmark import (
    ITERATIONS as PHYSICAL_ITERATIONS,
    PlanePoiseuille,
    _observe as observe_physical,
)
from .open_boundary_non_affine_mms import (
    NonAffineMms,
    _observe as observe_mms,
)


SCHEMA = "flowlab.laminar-all-hex-worker-result.v1"
VISCOSITY_M2_S = 0.01
HEIGHT_M = 1.0
DEPTH_M = 1.0


def physical_spec(parameters: dict[str, Any]) -> PlanePoiseuille:
    reynolds = float(parameters["reynoldsNumberHeightBased"])
    direction = int(parameters["flowDirectionSign"])
    length = float(parameters["lengthToHeightRatio"]) * HEIGHT_M
    if reynolds <= 0.0 or direction not in (-1, 1) or length <= 0.0:
        raise ValueError("invalid physical-envelope parameters")
    pressure_gradient = (
        direction
        * reynolds
        * 12.0
        * VISCOSITY_M2_S**2
        / HEIGHT_M**3
    )
    return PlanePoiseuille(
        pressure_drop_m2_s2=pressure_gradient * length,
        viscosity_m2_s=VISCOSITY_M2_S,
        length_m=length,
        height_m=HEIGHT_M,
        depth_m=DEPTH_M,
    )


def physical_cell_checks(
    observation: dict[str, Any],
    *,
    level: str,
) -> dict[str, bool]:
    force = observation.get("forceComparison", {})
    face = observation.get("faceComparison", {})
    checks = {
        "checkMesh": observation.get("checkMeshPassed") is True,
        "solverCompleted": observation.get("solverExitCode") == 0,
        "directAuditCompleted": observation.get("directAuditExitCode") == 0,
        "massBalance": float(observation.get("massRelativeImbalance", math.inf))
        <= PHYSICAL_MASS_LIMIT,
        "finalLinearResidual": float(
            observation.get("finalLinearResidual", math.inf)
        )
        <= PHYSICAL_LINEAR_RESIDUAL_LIMIT,
        "axialResidual": float(
            observation.get("finalAxialInitialResidual", math.inf)
        )
        <= AXIAL_NONLINEAR_RESIDUAL_LIMIT,
        "pressureResidual": float(
            observation.get("finalPressureInitialResidual", math.inf)
        )
        <= PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
        "transverseVelocity": float(
            observation.get("transverseVelocityRelativeL2Error", math.inf)
        )
        <= TRANSVERSE_VELOCITY_RELATIVE_LIMIT,
        "openFoamVsDirect": max(
            float(force.get("openForceObjectVsDirectAbsolute", math.inf)),
            float(force.get("wallForceObjectVsDirectAbsolute", math.inf)),
        )
        <= FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
        "analyticPressureForce": float(
            force.get("openPressureForceRelativeError", math.inf)
        )
        <= PRESSURE_FORCE_RELATIVE_LIMIT,
        "wallViscousForceCoarseThroughFine": float(
            force.get("wallViscousForceRelativeError", math.inf)
        )
        <= COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
    }
    if level == "fine":
        checks.update(
            {
                "fineWallViscousForce": float(
                    force.get("wallViscousForceRelativeError", math.inf)
                )
                <= FINE_WALL_VISCOUS_RELATIVE_LIMIT,
                "fineFaceViscousTraction": float(
                    face.get("maxViscousTractionRelativeError", math.inf)
                )
                <= FINE_FACE_TRACTION_RELATIVE_LIMIT,
                "fineFields": max(
                    float(observation.get("velocityRelativeL2Error", math.inf)),
                    float(observation.get("pressureRelativeL2Error", math.inf)),
                )
                <= FINE_FIELD_RELATIVE_LIMIT,
                "fineMomentumBalance": float(
                    force.get("totalMomentumRelativeImbalance", math.inf)
                )
                <= FINE_WALL_VISCOUS_RELATIVE_LIMIT,
            }
        )
    return checks


def mms_cell_checks(observation: dict[str, Any]) -> dict[str, bool]:
    return {
        "checkMesh": observation.get("checkMeshPassed") is True,
        "solverCompleted": observation.get("solverExitCode") == 0,
        "massBalance": float(observation.get("massRelativeImbalance", math.inf))
        <= MMS_MASS_LIMIT,
        "finalLinearResidual": float(
            observation.get("finalLinearResidual", math.inf)
        )
        <= MMS_LINEAR_RESIDUAL_LIMIT,
        "finalNonlinearResidual": float(
            observation.get("finalNonlinearResidual", math.inf)
        )
        <= NONLINEAR_RESIDUAL_LIMIT,
        "finiteVelocityError": math.isfinite(
            float(observation.get("velocityRelativeL2Error", math.inf))
        ),
        "finitePressureError": math.isfinite(
            float(observation.get("pressureRelativeL2Error", math.inf))
        ),
    }


def execute_cell(cell: dict[str, Any], output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite cell output: {output}")
    if cell.get("executionKind") != "scientific":
        raise ValueError("worker only executes scientific cells")
    lane = cell["lane"]
    parameters = cell["parameters"]
    level = parameters["level"]
    n = int(parameters.get("cellsPerHeight", parameters.get("cellsPerAxis")))
    if lane == "affine":
        observation = run_probe(
            output / "run",
            inlet_contract=FIXED_PRESSURE_TANGENTIAL_VELOCITY,
            u_solver_type="PBiCGStab",
            schema=AFFINE_LEVEL_SCHEMA,
            artifact_filename="affine-campaign-cell.json",
            n=n,
            iterations=1,
            linear_solver_tolerance=AFFINE_LINEAR_SOLVER_TOLERANCE,
        )
        checks = observation.get("checks", {})
        accepted = bool(checks) and all(checks.values())
    elif lane == "non-affine-mms":
        observation = observe_mms(output / "run", level, n, NonAffineMms())
        checks = mms_cell_checks(observation)
        accepted = all(checks.values())
    elif lane == "physical-envelope":
        spec = physical_spec(parameters)
        observation = observe_physical(
            output / "run",
            level,
            n,
            spec,
            float(parameters["axialCellAspectRatio"]),
            int(parameters.get("iterations", PHYSICAL_ITERATIONS)),
        )
        checks = physical_cell_checks(observation, level=level)
        accepted = all(checks.values())
    else:
        raise ValueError(f"unsupported scientific lane: {lane}")
    report = {
        "schema": SCHEMA,
        "campaignId": cell.get("campaignId", CAMPAIGN_ID),
        "cellId": cell["cellId"],
        "lane": lane,
        "status": "accepted" if accepted else "rejected-scientific",
        "parameters": parameters,
        "solverImageDigest": IMAGE_DIGEST,
        "checks": checks,
        "failedChecks": [name for name, passed in checks.items() if not passed],
        "observation": observation,
        "promotionAuthorized": False,
    }
    report = _json_safe(report)
    _write_json(output / "worker-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cell = json.loads(args.cell.read_text(encoding="utf-8"))
    report = execute_cell(cell, args.output.resolve())
    print(json.dumps(_json_safe(report), indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
