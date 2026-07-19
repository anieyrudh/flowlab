"""Run the 12/24/48 affine exact-state grid-invariance regression.

This is deliberately not an observed-order study.  The manufactured velocity
and pressure are affine and exactly representable by the selected finite-volume
operators, so the useful assertion is that the exact discrete state and all
face-compatibility gates survive refinement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .open_boundary_affine_flux_pressure_probe import (
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    run_probe as _run_probe,
)
from .open_boundary_affine_pressure_tangential_coarse import (
    SCHEMA as COARSE_SCHEMA,
)
from .open_boundary_affine_pressure_tangential_probe import (
    LINEAR_SOLVER_TOLERANCE,
)
from .open_boundary_mms_runner import _write


SCHEMA = "flowlab.open-boundary-affine-grid-invariance.v1"
LEVEL_SCHEMA = "flowlab.open-boundary-affine-grid-invariance-level.v1"
ARTIFACT = "affine-grid-invariance.json"
LEVELS = (("coarse", 12), ("medium", 24), ("fine", 48))
ITERATIONS = 1


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorized_upstream(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != COARSE_SCHEMA:
        raise ValueError("upstream report is not the affine coarse gate")
    if report.get("status") != "authorized":
        raise ValueError("upstream affine coarse gate is not authorized")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError("upstream affine coarse report does not pass every check")
    next_stage = report.get("nextStage", {})
    if next_stage.get("threeGridValidation") != "authorized":
        raise ValueError("upstream affine coarse report does not authorize refinement")
    return report


def _level_summary(label: str, n: int, report: dict[str, Any]) -> dict[str, Any]:
    observation = report.get("observation", {})
    diagnostics = observation.get("diagnostics", {})
    face = diagnostics.get("faceCompatibility", {})
    pressure_residual = (
        diagnostics.get("pressurePreSolve", {})
        .get("initializedPressureResidual", {})
        .get("max", math.inf)
    )
    return {
        "level": label,
        "cellsPerAxis": n,
        "effectiveSpacing": 1.0 / n,
        "status": report.get("status"),
        "allChecksPassed": bool(report.get("checks"))
        and all(report["checks"].values()),
        "velocityRelativeL2Error": observation.get(
            "velocityRelativeL2Error", math.inf
        ),
        "pressureRelativeL2Error": observation.get(
            "pressureRelativeL2Error", math.inf
        ),
        "massRelativeImbalance": observation.get("mass", {}).get(
            "relativeImbalance", math.inf
        ),
        "finalLinearResidual": observation.get("finalLinearResidual", math.inf),
        "maxAbsoluteCorrectionMismatch": face.get(
            "maxAbsoluteCorrectionMismatch", math.inf
        ),
        "maxAbsoluteNormalPressureGradientError": face.get(
            "maxAbsoluteNormalPressureGradientError", math.inf
        ),
        "maxAbsoluteBoundaryPressureError": face.get(
            "maxAbsoluteBoundaryPressureError", math.inf
        ),
        "pressureEquationExactStateResidualMax": pressure_residual,
    }


def run_grid_invariance(
    output: Path,
    coarse_report: Path,
) -> dict[str, Any]:
    """Run all three affine grids after the 100-iteration coarse gate passes."""
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    upstream = _authorized_upstream(coarse_report)
    summaries: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for label, n in LEVELS:
        level_root = output / label
        level_report = _run_probe(
            level_root,
            inlet_contract=FIXED_PRESSURE_TANGENTIAL_VELOCITY,
            u_solver_type="PBiCGStab",
            schema=LEVEL_SCHEMA,
            artifact_filename="affine-grid-invariance-level.json",
            n=n,
            iterations=ITERATIONS,
            linear_solver_tolerance=LINEAR_SOLVER_TOLERANCE,
        )
        level_path = level_root / "artifacts" / "affine-grid-invariance-level.json"
        summaries.append(_level_summary(label, n, level_report))
        evidence.append(
            {
                "level": label,
                "path": str(level_path),
                "sha256": _sha(level_path),
            }
        )

    level_gate = all(
        item["status"] == "authorized" and item["allChecksPassed"]
        for item in summaries
    )
    exact_state_gate = all(
        math.isfinite(float(item[key]))
        for item in summaries
        for key in (
            "velocityRelativeL2Error",
            "pressureRelativeL2Error",
            "massRelativeImbalance",
            "finalLinearResidual",
            "maxAbsoluteCorrectionMismatch",
            "maxAbsoluteNormalPressureGradientError",
            "maxAbsoluteBoundaryPressureError",
            "pressureEquationExactStateResidualMax",
        )
    )
    spacing_gate = [item["effectiveSpacing"] for item in summaries] == [
        1.0 / 12,
        1.0 / 24,
        1.0 / 48,
    ]
    checks = {
        "authorizedCoarseStabilityGate": True,
        "resolutionSequence12_24_48": spacing_gate,
        "everyLevelPassesEveryExistingGate": level_gate,
        "allGridMetricsFinite": exact_state_gate,
    }
    passed = all(checks.values())
    report = {
        "schema": SCHEMA,
        "status": "accepted" if passed else "rejected",
        "scientificStatus": "discrete-affine-grid-invariance-regression",
        "validated": False,
        "interpretation": (
            "Exact-state invariance across refinement; observed order and GCI are "
            "intentionally not reported because the affine fields are exactly "
            "representable and round-off dominated."
        ),
        "contract": {
            "inletPressure": "exact fixedValue",
            "outletPressure": "exact fixedValue",
            "inletVelocity": (
                "pressureInletOutletVelocity with exact tangential velocity and "
                "zero-gradient normal component"
            ),
            "solver": "incompressibleFluid/PBiCGStab/DILU",
            "linearSolverTolerance": LINEAR_SOLVER_TOLERANCE,
            "iterationsPerGrid": ITERATIONS,
            "coarseStabilityEvidenceIterations": upstream.get("execution", {}).get(
                "iterations"
            ),
        },
        "levels": summaries,
        "checks": checks,
        "failedChecks": [name for name, passed_ in checks.items() if not passed_],
        "upstreamGate": {
            "path": str(coarse_report),
            "sha256": _sha(coarse_report),
            "schema": upstream["schema"],
            "status": upstream["status"],
            "allChecksPassed": True,
        },
        "evidence": evidence,
        "nextStage": {
            "nonAffineMms": "authorized" if passed else "blocked",
            "executed": False,
        },
    }
    _write(output / "artifacts" / ARTIFACT, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coarse-report", type=Path, required=True)
    args = parser.parse_args()
    report = run_grid_invariance(
        args.output.resolve(),
        args.coarse_report.resolve(),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
