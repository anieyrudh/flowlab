"""OpenFOAM negative control proving invalid topology is rejected."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .laminar_all_hex_campaign import _write_json
from .open_boundary_laminar_force_benchmark import PlanePoiseuille, _case_files
from .open_boundary_mms_runner import _run, _write


SCHEMA = "flowlab.laminar-all-hex-invalid-mesh-control.v1"


def run(output: Path) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    case = output / "case"
    artifacts = output / "artifacts"
    files = _case_files(4, PlanePoiseuille())
    files["system/blockMeshDict"] = files["system/blockMeshDict"].replace(
        "faces ((0 4 7 3));", "faces ((0 4 7 7));"
    )
    for name, content in files.items():
        _write(case / name, content)
    block = _run(["blockMesh"], case, artifacts / "blockMesh.log")
    check = (
        _run(
            ["checkMesh", "-allGeometry", "-allTopology"],
            case,
            artifacts / "checkMesh.log",
        )
        if block == 0
        else None
    )
    mesh_ok = False
    if check == 0:
        mesh_ok = "Mesh OK" in (artifacts / "checkMesh.log").read_text(
            encoding="utf-8", errors="replace"
        )
    rejected = block != 0 or check != 0 or not mesh_ok
    report = {
        "schema": SCHEMA,
        "control": "invalid-mesh-topology",
        "mutation": "inlet quad repeats vertex 7 and omits vertex 3",
        "expectedOutcome": "rejected",
        "observed": {
            "blockMeshExitCode": block,
            "checkMeshExitCode": check,
            "meshOk": mesh_ok,
        },
        "status": "rejected-as-expected" if rejected else "control-failed",
        "passed": rejected,
    }
    _write_json(output / "control-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
