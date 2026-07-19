"""Run the affine MMS with a well-posed pressure/tangential-velocity inlet."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .open_boundary_affine_flux_pressure_probe import (
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    run_probe as _run_probe,
)


SCHEMA = "flowlab.open-boundary-affine-pressure-tangential-one-step.v1"
ARTIFACT = "affine-pressure-tangential-one-step.json"
LINEAR_SOLVER_TOLERANCE = 1.0e-14


def run_probe(output: Path) -> dict[str, Any]:
    """Execute the fail-closed one-step gate for the compatible inlet contract."""
    return _run_probe(
        output,
        inlet_contract=FIXED_PRESSURE_TANGENTIAL_VELOCITY,
        u_solver_type="PBiCGStab",
        schema=SCHEMA,
        artifact_filename=ARTIFACT,
        linear_solver_tolerance=LINEAR_SOLVER_TOLERANCE,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_probe(args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "authorized" else 2


if __name__ == "__main__":
    raise SystemExit(main())
