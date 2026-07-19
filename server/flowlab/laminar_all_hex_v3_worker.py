"""Execute one immutable v3 cell with sustained physical convergence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .laminar_all_hex_campaign import IMAGE_DIGEST, _json_safe, _write_json
from .laminar_all_hex_campaign_worker import (
    SCHEMA,
    execute_cell as execute_v2_compatible_cell,
    physical_cell_checks,
    physical_spec,
)
from .laminar_all_hex_v3_contract import CAMPAIGN_ID, termination_contract
from .laminar_all_hex_v3_observer import observe_physical_v3


def execute_cell(cell: dict, output: Path) -> dict:
    if cell.get("campaignId") != CAMPAIGN_ID:
        raise ValueError("v3 worker requires the frozen v3 campaign identity")
    if cell.get("lane") != "physical-envelope":
        return execute_v2_compatible_cell(cell, output)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite cell output: {output}")
    parameters = cell["parameters"]
    if parameters.get("convergenceControl") != termination_contract():
        raise ValueError("physical cell convergence contract is not frozen v3")
    level = str(parameters["level"])
    observation = observe_physical_v3(
        output / "run",
        level,
        int(parameters["cellsPerHeight"]),
        physical_spec(parameters),
        float(parameters["axialCellAspectRatio"]),
    )
    checks = physical_cell_checks(observation, level=level)
    convergence = observation.get("convergenceControl", {})
    checks["sustainedConvergenceBeforeHardCap"] = (
        convergence.get("achieved") is True
        and convergence.get("stopIteration", 0)
        <= termination_contract()["hardCapIterations"]
    )
    accepted = all(checks.values())
    report = {
        "schema": SCHEMA,
        "campaignId": CAMPAIGN_ID,
        "cellId": cell["cellId"],
        "lane": cell["lane"],
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
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
