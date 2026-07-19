"""Run the gated coarse affine pressure/tangential-velocity validation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .open_boundary_affine_flux_pressure_probe import (
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    run_probe as _run_probe,
)
from .open_boundary_affine_pressure_tangential_probe import SCHEMA as ONE_STEP_SCHEMA
from .open_boundary_affine_pressure_tangential_probe import LINEAR_SOLVER_TOLERANCE
from .open_boundary_mms_runner import _write


SCHEMA = "flowlab.open-boundary-affine-pressure-tangential-coarse.v1"
ARTIFACT = "affine-pressure-tangential-coarse.json"
N = 12
ITERATIONS = 100


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorized_upstream(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != ONE_STEP_SCHEMA:
        raise ValueError("upstream report is not the pressure/tangential one-step gate")
    if report.get("status") != "authorized":
        raise ValueError("upstream one-step gate is not authorized")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError("upstream one-step report does not pass every check")
    return report


def run_coarse(output: Path, one_step_report: Path) -> dict[str, Any]:
    """Run coarse validation only after a complete authorized one-step gate."""
    upstream = _authorized_upstream(one_step_report)
    report = _run_probe(
        output,
        inlet_contract=FIXED_PRESSURE_TANGENTIAL_VELOCITY,
        u_solver_type="PBiCGStab",
        schema=SCHEMA,
        artifact_filename=ARTIFACT,
        n=N,
        iterations=ITERATIONS,
        linear_solver_tolerance=LINEAR_SOLVER_TOLERANCE,
    )
    passed = report["status"] == "authorized" and all(report["checks"].values())
    report["scientificStatus"] = "coarse-exact-state-and-face-compatibility-gate"
    report["upstreamGate"] = {
        "path": str(one_step_report),
        "sha256": _sha(one_step_report),
        "schema": upstream["schema"],
        "status": upstream["status"],
        "allChecksPassed": True,
    }
    report["nextStage"] = {
        "coarseValidation": "passed" if passed else "blocked",
        "coarseValidationExecuted": True,
        "threeGridValidation": "authorized" if passed else "blocked",
        "threeGridValidationExecuted": False,
    }
    _write(
        output / "artifacts" / ARTIFACT,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--one-step-report", type=Path, required=True)
    args = parser.parse_args()
    report = run_coarse(
        args.output.resolve(),
        args.one_step_report.resolve(),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "authorized" else 2


if __name__ == "__main__":
    raise SystemExit(main())
