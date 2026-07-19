"""Physical observer with the predeclared v3 sustained-convergence stop."""
from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any, Iterable, Tuple

from .cad_parabolic_smoke import _read_cell_centres
from .laminar_all_hex_campaign import (
    AXIAL_NONLINEAR_RESIDUAL_LIMIT,
    PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
)
from .laminar_all_hex_v3_contract import (
    CHECK_INTERVAL_ITERATIONS,
    HARD_CAP_ITERATIONS,
    MINIMUM_ITERATIONS,
    SUSTAINED_WINDOW_ITERATIONS,
    termination_contract,
)
from .open_boundary_laminar_force_benchmark import (
    PlanePoiseuille,
    _case_files,
    _direct_audit,
    _initialize_exact,
    _mesh_shape,
    _sum_vectors,
    _vector_error,
)
from .open_boundary_mms_runner import (
    _flux_imbalance,
    _l2,
    _run,
    _values,
    _write,
)


Vector = Tuple[float, float, float]


def staged_case_files(
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float,
) -> dict[str, str]:
    files = _case_files(
        n,
        spec,
        axial_cell_aspect_ratio,
        HARD_CAP_ITERATIONS,
    )
    control = files["system/controlDict"]
    control = control.replace("startFrom startTime;", "startFrom latestTime;")
    control = control.replace(
        f"writeInterval {HARD_CAP_ITERATIONS};",
        f"writeInterval {CHECK_INTERVAL_ITERATIONS};",
    )
    control = control.replace(
        "deltaT 1;\n",
        "deltaT 1;\npurgeWrite 1;\n",
        1,
    )
    files["system/controlDict"] = set_stage_end_time(
        control, CHECK_INTERVAL_ITERATIONS
    )
    return files


def set_stage_end_time(control: str, end_time: int) -> str:
    if end_time <= 0 or end_time > HARD_CAP_ITERATIONS:
        raise ValueError("stage end time is outside the frozen hard cap")
    updated, count = re.subn(
        r"\bendTime\s+[0-9]+;",
        f"endTime {end_time};",
        control,
        count=1,
    )
    if count != 1:
        raise ValueError("controlDict does not contain exactly one endTime")
    return updated


def read_residual_history(case: Path) -> list[dict[str, float | int]]:
    by_iteration: dict[int, dict[str, float | int]] = {}
    paths = sorted((case / "postProcessing/residuals").glob("**/residuals.dat"))
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            values = line.split()
            if len(values) < 5 or "N/A" in values:
                continue
            try:
                iteration = int(round(float(values[0])))
                by_iteration[iteration] = {
                    "iteration": iteration,
                    "Ux": float(values[1]),
                    "p": float(values[4]),
                }
            except ValueError:
                continue
    return [by_iteration[key] for key in sorted(by_iteration)]


def sustained_window(
    history: list[dict[str, float | int]],
    *,
    minimum_iterations: int = MINIMUM_ITERATIONS,
    minimum_is_window_start: bool = True,
) -> dict[str, Any]:
    if minimum_is_window_start:
        eligible = [
            row for row in history if int(row["iteration"]) >= minimum_iterations
        ]
    else:
        eligible = (
            history
            if history and int(history[-1]["iteration"]) >= minimum_iterations
            else []
        )
    window = eligible[-SUSTAINED_WINDOW_ITERATIONS:]
    consecutive = len(window) == SUSTAINED_WINDOW_ITERATIONS and all(
        int(right["iteration"]) - int(left["iteration"]) == 1
        for left, right in zip(window, window[1:])
    )
    ux_max = max((float(row["Ux"]) for row in window), default=math.inf)
    p_max = max((float(row["p"]) for row in window), default=math.inf)
    passed = (
        consecutive
        and ux_max <= AXIAL_NONLINEAR_RESIDUAL_LIMIT
        and p_max <= PRESSURE_NONLINEAR_RESIDUAL_LIMIT
    )
    return {
        "passed": passed,
        "windowLength": len(window),
        "firstIteration": int(window[0]["iteration"]) if window else None,
        "lastIteration": int(window[-1]["iteration"]) if window else None,
        "maximumAxialResidual": ux_max,
        "maximumPressureResidual": p_max,
    }


def first_sustained_joint_pass_iteration(
    history: list[dict[str, float | int]],
    *,
    minimum_iterations: int = MINIMUM_ITERATIONS,
    minimum_is_window_start: bool = True,
) -> int | None:
    for index in range(SUSTAINED_WINDOW_ITERATIONS - 1, len(history)):
        prefix = history[: index + 1]
        result = sustained_window(
            prefix,
            minimum_iterations=minimum_iterations,
            minimum_is_window_start=minimum_is_window_start,
        )
        if result["passed"]:
            return int(history[index]["iteration"])
    return None


def _latest_table_row(paths: Iterable[Path]) -> str:
    latest_time = -math.inf
    latest_row: str | None = None
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            try:
                time_value = float(line.split()[0])
            except (ValueError, IndexError):
                continue
            if time_value >= latest_time:
                latest_time = time_value
                latest_row = line
    if latest_row is None:
        raise ValueError("no numeric function-object rows found")
    return latest_row


def _latest_force_object(case: Path, name: str) -> dict[str, Vector]:
    row = _latest_table_row(
        (case / "postProcessing" / name).glob("**/force*.dat")
    )
    vectors = re.findall(
        r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)",
        row,
    )
    if len(vectors) < 2:
        raise ValueError(f"invalid force output for {name}")
    return {
        "pressure": tuple(float(value) for value in vectors[0]),
        "viscous": tuple(float(value) for value in vectors[1]),
    }


def _combined_solver_log(artifacts: Path, stages: list[dict[str, Any]]) -> str:
    chunks = []
    for stage in stages:
        path = artifacts / stage["log"]
        chunks.append(
            f"\n// stage endTime={stage['endIteration']}\n"
            + path.read_text(encoding="utf-8", errors="replace")
        )
    combined = "".join(chunks)
    (artifacts / "foamRun.log").write_text(combined, encoding="utf-8")
    return combined


def observe_physical_staged(
    root: Path,
    label: str,
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
    *,
    minimum_iterations: int = MINIMUM_ITERATIONS,
    minimum_is_window_start: bool = True,
    convergence_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = convergence_contract or termination_contract()
    if minimum_iterations != int(contract["minimumIterations"]):
        raise ValueError("observer minimum does not match the frozen contract")
    if int(contract["hardCapIterations"]) != HARD_CAP_ITERATIONS:
        raise ValueError("observer hard cap does not match the staged case")
    if int(contract["checkIntervalIterations"]) != CHECK_INTERVAL_ITERATIONS:
        raise ValueError("observer checkpoint interval does not match the staged case")
    if int(contract["sustainedWindowIterations"]) != SUSTAINED_WINDOW_ITERATIONS:
        raise ValueError("observer sustained window does not match the staged case")
    case = root / label / "case"
    artifacts = root / label / "artifacts"
    files = staged_case_files(n, spec, axial_cell_aspect_ratio)
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
        else 127
    )
    centres = (
        _run(
            ["foamPostProcess", "-func", "writeCellCentres", "-time", "0"],
            case,
            artifacts / "writeCellCentres.log",
        )
        if check == 0
        else 127
    )
    if centres == 0:
        _initialize_exact(case, spec)

    control_template = (case / "system/controlDict").read_text(encoding="utf-8")
    stages: list[dict[str, Any]] = []
    history: list[dict[str, float | int]] = []
    final_window = sustained_window(
        history,
        minimum_iterations=minimum_iterations,
        minimum_is_window_start=minimum_is_window_start,
    )
    if centres == 0:
        for end_iteration in range(
            CHECK_INTERVAL_ITERATIONS,
            HARD_CAP_ITERATIONS + 1,
            CHECK_INTERVAL_ITERATIONS,
        ):
            _write(
                case / "system/controlDict",
                set_stage_end_time(control_template, end_iteration),
            )
            stage_log = f"foamRun-stage-{end_iteration:04d}.log"
            exit_code = _run(
                ["foamRun", "-solver", "incompressibleFluid"],
                case,
                artifacts / stage_log,
            )
            stages.append(
                {
                    "endIteration": end_iteration,
                    "exitCode": exit_code,
                    "log": stage_log,
                }
            )
            if exit_code != 0:
                break
            history = read_residual_history(case)
            final_window = sustained_window(
                history,
                minimum_iterations=minimum_iterations,
                minimum_is_window_start=minimum_is_window_start,
            )
            if final_window["passed"]:
                break

    solver_log = _combined_solver_log(artifacts, stages) if stages else ""
    solver = 0 if stages and all(stage["exitCode"] == 0 for stage in stages) else 127
    stop_iteration = int(stages[-1]["endIteration"]) if stages else 0
    first_joint_pass = first_sustained_joint_pass_iteration(
        history,
        minimum_iterations=minimum_iterations,
        minimum_is_window_start=minimum_is_window_start,
    )
    achieved = solver == 0 and final_window["passed"] is True
    csv_path = artifacts / "face-traction.csv"
    utility = (
        _run(
            [
                "flowlabPatchTractionAudit",
                "-time",
                str(stop_iteration),
                "-allFlowPatches",
                "-output",
                str(csv_path),
            ],
            case,
            artifacts / "face-traction.log",
        )
        if achieved
        else 127
    )

    mesh_shape = _mesh_shape(n, spec, axial_cell_aspect_ratio)
    try:
        cell_centres = _read_cell_centres(case / "0/C")
        actual_u = _values(case / str(stop_iteration) / "U", True)
        actual_p = _values(case / str(stop_iteration) / "p", False)
        exact_u = [spec.velocity(*point) for point in cell_centres]
        velocity_error = _l2(actual_u, exact_u)
        pressure_error = _l2(
            actual_p,
            [(spec.pressure(*point),) for point in cell_centres],
        )
        velocity_norm = sum(value[0] ** 2 for value in exact_u)
        transverse_velocity_error = math.sqrt(
            sum(value[1] ** 2 + value[2] ** 2 for value in actual_u)
            / velocity_norm
        )
        direct = _direct_audit(csv_path, spec)
        force_open = _latest_force_object(case, "forcesOpen")
        force_walls = _latest_force_object(case, "forcesWalls")
    except (OSError, ValueError, StopIteration, ZeroDivisionError, IndexError):
        velocity_error = pressure_error = transverse_velocity_error = math.inf
        direct = {}
        force_open = force_walls = {}

    final_linear = [
        float(value)
        for value in re.findall(r"Final residual = ([0-9.eE+-]+)", solver_log)
    ]
    final_history = history[-1] if history else {"Ux": math.inf, "p": math.inf}
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
        "level": label,
        "cellsPerHeight": n,
        "cellsPerAxis": n if mesh_shape == (n, n, n) else None,
        "meshShape": list(mesh_shape),
        "axialCellAspectRatio": axial_cell_aspect_ratio,
        "effectiveSpacing": spec.height_m / n,
        "iterations": stop_iteration,
        "checkMeshPassed": block == 0
        and check == 0
        and "Mesh OK"
        in (artifacts / "checkMesh.log").read_text(
            encoding="utf-8", errors="replace"
        ),
        "solverExitCode": solver,
        "directAuditExitCode": utility,
        "velocityRelativeL2Error": velocity_error,
        "transverseVelocityRelativeL2Error": transverse_velocity_error,
        "pressureRelativeL2Error": pressure_error,
        "massRelativeImbalance": _flux_imbalance(case),
        "finalLinearResidual": max(final_linear[-4:])
        if final_linear
        else math.inf,
        "finalAxialInitialResidual": float(final_history["Ux"]),
        "finalPressureInitialResidual": float(final_history["p"]),
        "convergenceControl": {
            "contract": contract,
            "achieved": achieved,
            "stopIteration": stop_iteration,
            "hardCapReached": stop_iteration == HARD_CAP_ITERATIONS,
            "firstSustainedJointPassIteration": first_joint_pass,
            "finalWindow": final_window,
            "stages": stages,
        },
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
            "solver": str(artifacts / "foamRun.log"),
            "faceCsv": str(csv_path),
            "faceAuditLog": str(artifacts / "face-traction.log"),
        },
    }


def observe_physical_v3(
    root: Path,
    label: str,
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
) -> dict[str, Any]:
    """Run the original v3 observer contract unchanged."""

    return observe_physical_staged(
        root,
        label,
        n,
        spec,
        axial_cell_aspect_ratio,
        minimum_iterations=MINIMUM_ITERATIONS,
        minimum_is_window_start=True,
        convergence_contract=termination_contract(),
    )
