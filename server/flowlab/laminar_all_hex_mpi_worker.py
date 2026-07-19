"""Execute one physical all-hex campaign cell under decomposed MPI.

The worker preserves the serial scientific inputs, adds only the declared
Scotch decomposition dictionary, reconstructs the final fields, and evaluates
the same force, field, residual, and conservation gates as the serial worker.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

from .cad_parabolic_smoke import _read_cell_centres
from .laminar_all_hex_campaign import (
    CAMPAIGN_ID,
    IMAGE_DIGEST,
    _canonical_sha256,
    _write_json,
)
from .laminar_all_hex_campaign_worker import physical_cell_checks, physical_spec
from .open_boundary_laminar_force_benchmark import (
    ITERATIONS,
    PATCHES,
    _case_files,
    _direct_audit,
    _force_object,
    _initialize_exact,
    _mesh_shape,
    _sum_vectors,
    _vector_error,
)
from .open_boundary_mms_runner import (
    _flux_imbalance,
    _header,
    _l2,
    _run,
    _values,
    _write,
)


SCHEMA = "flowlab.laminar-all-hex-mpi-worker.v1"


def _decompose_par_dict(ranks: int) -> str:
    if ranks not in (2, 4):
        raise ValueError("campaign MPI rank count must be 2 or 4")
    return _header("system", "decomposeParDict") + f"""numberOfSubdomains {ranks};
method scotch;
"""


def _input_digest(case: Path) -> str:
    records: dict[str, str] = {}
    for relative in (
        "0/U",
        "0/p",
        "constant/momentumTransport",
        "constant/physicalProperties",
        "system/blockMeshDict",
        "system/controlDict",
        "system/decomposeParDict",
        "system/fvSchemes",
        "system/fvSolution",
        "benchmark-definition.json",
    ):
        records[relative] = _canonical_sha256((case / relative).read_text(encoding="utf-8"))
    return _canonical_sha256(records)


def _last_initial(solver_log: str, field: str) -> float:
    values = re.findall(
        rf"Solving for {field}, Initial residual = ([0-9.eE+-]+)", solver_log
    )
    return float(values[-1]) if values else math.inf


def _collect_observation(
    *,
    case: Path,
    artifacts: Path,
    level: str,
    n: int,
    spec: Any,
    axial_cell_aspect_ratio: float,
    iterations: int,
    ranks: int,
    serial_check: int,
    parallel_check: int,
    solver: int,
    reconstruct: int,
    utility: int,
) -> dict[str, Any]:
    csv_path = artifacts / "face-traction.csv"
    mesh_shape = _mesh_shape(n, spec, axial_cell_aspect_ratio)
    try:
        cell_centres = _read_cell_centres(case / "0/C")
        actual_u = _values(case / str(iterations) / "U", True)
        actual_p = _values(case / str(iterations) / "p", False)
        exact_u = [spec.velocity(*point) for point in cell_centres]
        velocity_error = _l2(actual_u, exact_u)
        pressure_error = _l2(
            actual_p, [(spec.pressure(*point),) for point in cell_centres]
        )
        velocity_norm = sum(value[0] ** 2 for value in exact_u)
        transverse_velocity_error = math.sqrt(
            sum(value[1] ** 2 + value[2] ** 2 for value in actual_u)
            / velocity_norm
        )
        direct = _direct_audit(csv_path, spec)
        force_open = _force_object(case, "forcesOpen")
        force_walls = _force_object(case, "forcesWalls")
    except (OSError, ValueError, StopIteration, ZeroDivisionError, IndexError):
        velocity_error = pressure_error = transverse_velocity_error = math.inf
        direct = {}
        force_open = force_walls = {}
    solver_log = (artifacts / "foamRun.parallel.log").read_text(
        encoding="utf-8", errors="replace"
    )
    final = [
        float(value)
        for value in re.findall(r"Final residual = ([0-9.eE+-]+)", solver_log)
    ]
    analytic_open_pressure = spec.analytic_open_pressure_force
    analytic_wall_viscous = spec.analytic_wall_viscous_force
    scale = max(abs(analytic_open_pressure[0]), 1.0e-300)
    metrics = {
        "openForceObjectVsDirectAbsolute": max(
            (
                _vector_error(force_open[key], direct["open"][key])
                for key in ("pressure", "viscous")
            ),
            default=math.inf,
        )
        if direct and force_open
        else math.inf,
        "wallForceObjectVsDirectAbsolute": max(
            (
                _vector_error(force_walls[key], direct["walls"][key])
                for key in ("pressure", "viscous")
            ),
            default=math.inf,
        )
        if direct and force_walls
        else math.inf,
        "openPressureForceRelativeError": _vector_error(
            direct["open"]["pressure"], analytic_open_pressure
        )
        / scale
        if direct
        else math.inf,
        "wallViscousForceRelativeError": _vector_error(
            direct["walls"]["viscous"], analytic_wall_viscous
        )
        / scale
        if direct
        else math.inf,
        "openIntegratedViscousRelativeMagnitude": math.sqrt(
            sum(value * value for value in direct["open"]["viscous"])
        )
        / scale
        if direct
        else math.inf,
        "totalMomentumRelativeImbalance": _vector_error(
            _sum_vectors((direct["open"]["pressure"], direct["walls"]["viscous"])),
            (0.0, 0.0, 0.0),
        )
        / scale
        if direct
        else math.inf,
    }
    return {
        "level": level,
        "cellsPerHeight": n,
        "cellsPerAxis": n if mesh_shape == (n, n, n) else None,
        "meshShape": list(mesh_shape),
        "axialCellAspectRatio": axial_cell_aspect_ratio,
        "effectiveSpacing": spec.height_m / n,
        "iterations": iterations,
        "mpiRanks": ranks,
        "checkMeshPassed": serial_check == 0 and parallel_check == 0,
        "parallelCheckMeshPassed": parallel_check == 0,
        "solverExitCode": solver if reconstruct == 0 else 127,
        "reconstructExitCode": reconstruct,
        "directAuditExitCode": utility,
        "velocityRelativeL2Error": velocity_error,
        "transverseVelocityRelativeL2Error": transverse_velocity_error,
        "pressureRelativeL2Error": pressure_error,
        "massRelativeImbalance": _flux_imbalance(case),
        "finalLinearResidual": max(final[-4:]) if final else math.inf,
        "finalAxialInitialResidual": _last_initial(solver_log, "Ux"),
        "finalPressureInitialResidual": _last_initial(solver_log, "p"),
        "forceComparison": metrics,
        "faceComparison": {
            "maxViscousTractionRelativeError": direct.get(
                "maxFaceViscousTractionRelativeError", math.inf
            ),
            "maxPressureRelativeError": direct.get(
                "maxFacePressureRelativeError", math.inf
            ),
        },
        "openFoamForces": {"open": force_open, "walls": force_walls},
        "directFaceIntegration": direct,
        "analytic": {
            "openPressureForce": analytic_open_pressure,
            "wallViscousForce": analytic_wall_viscous,
            "openIntegratedViscousForce": (0.0, 0.0, 0.0),
        },
        "artifacts": {
            "case": str(case),
            "solver": str(artifacts / "foamRun.parallel.log"),
            "decomposition": str(artifacts / "decomposePar.log"),
            "reconstruction": str(artifacts / "reconstructPar.log"),
            "faceCsv": str(csv_path),
        },
    }


def execute(cell: dict[str, Any], output: Path, ranks: int) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite non-empty output: {output}")
    if cell.get("lane") != "physical-envelope":
        raise ValueError("MPI reproducibility requires a physical-envelope cell")
    parameters = cell["parameters"]
    level = str(parameters["level"])
    n = int(parameters["cellsPerHeight"])
    aspect = float(parameters["axialCellAspectRatio"])
    iterations = int(parameters.get("iterations", ITERATIONS))
    spec = physical_spec(parameters)
    case = output / "run" / level / "case"
    artifacts = output / "run" / level / "artifacts"
    for name, content in _case_files(n, spec, aspect, iterations).items():
        _write(case / name, content)
    block = _run(["blockMesh"], case, artifacts / "blockMesh.log")
    serial_check = (
        _run(
            ["checkMesh", "-allGeometry", "-allTopology"],
            case,
            artifacts / "checkMesh.serial.log",
        )
        if block == 0
        else 127
    )
    centres = (
        _run(
            ["foamPostProcess", "-func", "writeCellCentres", "-time", "0"],
            case,
            artifacts / "writeCellCentres.log",
        )
        if serial_check == 0
        else 127
    )
    if centres == 0:
        _initialize_exact(case, spec)
    _write(case / "system/decomposeParDict", _decompose_par_dict(ranks))
    input_digest = _input_digest(case)
    decompose = (
        _run(["decomposePar", "-force"], case, artifacts / "decomposePar.log")
        if centres == 0
        else 127
    )
    mpi_prefix = ["mpirun", "--allow-run-as-root", "-np", str(ranks)]
    parallel_check = (
        _run(
            mpi_prefix + ["checkMesh", "-parallel", "-allGeometry", "-allTopology"],
            case,
            artifacts / "checkMesh.parallel.log",
        )
        if decompose == 0
        else 127
    )
    solver = (
        _run(
            mpi_prefix
            + ["foamRun", "-solver", "incompressibleFluid", "-parallel"],
            case,
            artifacts / "foamRun.parallel.log",
        )
        if parallel_check == 0
        else 127
    )
    reconstruct = (
        _run(
            ["reconstructPar", "-latestTime"],
            case,
            artifacts / "reconstructPar.log",
        )
        if solver == 0
        else 127
    )
    csv_path = artifacts / "face-traction.csv"
    utility = (
        _run(
            [
                "flowlabPatchTractionAudit",
                "-time",
                str(iterations),
                "-allFlowPatches",
                "-output",
                str(csv_path),
            ],
            case,
            artifacts / "face-traction.log",
        )
        if reconstruct == 0
        else 127
    )
    observation = _collect_observation(
        case=case,
        artifacts=artifacts,
        level=level,
        n=n,
        spec=spec,
        axial_cell_aspect_ratio=aspect,
        iterations=iterations,
        ranks=ranks,
        serial_check=serial_check,
        parallel_check=parallel_check,
        solver=solver,
        reconstruct=reconstruct,
        utility=utility,
    )
    checks = physical_cell_checks(observation, level=level)
    checks["decompositionCompleted"] = decompose == 0
    checks["parallelMeshPassed"] = parallel_check == 0
    checks["reconstructionCompleted"] = reconstruct == 0
    accepted = all(checks.values())
    report = {
        "schema": SCHEMA,
        "campaignId": cell.get("campaignId", CAMPAIGN_ID),
        "cellId": cell["cellId"],
        "sourceCellId": cell.get("sourceCellId"),
        "lane": "reproducibility",
        "mode": "mpi-decomposition",
        "mpiRanks": ranks,
        "status": "accepted" if accepted else "rejected-scientific",
        "parameters": parameters,
        "solverImageDigest": IMAGE_DIGEST,
        "inputTreeSha256": input_digest,
        "checks": checks,
        "failedChecks": [name for name, passed in checks.items() if not passed],
        "observation": observation,
        "promotionAuthorized": False,
    }
    _write_json(output / "worker-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cell", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ranks", type=int, required=True)
    args = parser.parse_args()
    cell = json.loads(args.cell.read_text(encoding="utf-8"))
    report = execute(cell, args.output.resolve(), args.ranks)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
