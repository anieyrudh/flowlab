"""Run the fail-closed affine MMS fixed-flux-pressure one-step probe."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from .cad_parabolic_smoke import (
    _nonuniform_scalar_field,
    _nonuniform_vector_field,
    _read_cell_centres,
)
from .open_boundary_affine_probe import (
    FIELD_ERROR_LIMIT,
    ITERATIONS,
    LINEAR_RESIDUAL_LIMIT,
    MASS_LIMIT,
    N,
    _case_files as _baseline_case_files,
    _flux_balance,
    _init_exact as _init_exact_internal_fields,
)
from .open_boundary_mms_redesign import AffineCrossflowMms, preflight
from .open_boundary_mms_runner import _header, _l2, _run, _values, _write
from .open_boundary_operator_audit import (
    _boundary_face_counts,
    _surface_scalars,
    pressure_presolve_audit,
    trace_flux_audit,
)


SCHEMA = "flowlab.open-boundary-affine-flux-pressure-one-step.v1"
COMPATIBILITY_LIMIT = 1.0e-12
PRESSURE_EQUATION_RESIDUAL_LIMIT = 1.0e-12
PRESSURE_PATCHES = ("inlet", "yMin", "yMax")
ALL_PATCHES = ("inlet", "outlet", "yMin", "yMax", "zMin", "zMax")
FIXED_VELOCITY_FLUX_PRESSURE = "fixed-velocity-fixed-flux-pressure"
FIXED_PRESSURE_TANGENTIAL_VELOCITY = "fixed-pressure-tangential-velocity"
SUPPORTED_INLET_CONTRACTS = (
    FIXED_VELOCITY_FLUX_PRESSURE,
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
)
SUPPORTED_U_SOLVERS = ("smoothSolver", "PBiCGStab")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _definition_manifest(
    spec: AffineCrossflowMms,
    inlet_contract: str,
) -> dict[str, Any]:
    manifest = spec.manifest()
    if inlet_contract == FIXED_PRESSURE_TANGENTIAL_VELOCITY:
        manifest["boundaryTreatment"]["inlet"] = {
            "U": (
                "pressureInletOutletVelocity; analytic tangential velocity; "
                "analytic dU/dx=0 for the normal component"
            ),
            "p": "fixedValue G; upstream pressure trace",
        }
        manifest["boundaryTreatment"]["outlet"]["p"] = (
            "fixedValue 0; downstream pressure trace"
        )
    return manifest


def _pressure_field(
    spec: AffineCrossflowMms,
    inlet_contract: str = FIXED_VELOCITY_FLUX_PRESSURE,
) -> str:
    if inlet_contract not in SUPPORTED_INLET_CONTRACTS:
        raise ValueError(f"unsupported inlet contract: {inlet_contract}")
    gradient = spec.pressure_gradient_m2_s2_per_m
    inlet = (
        f"inlet {{ type fixedFluxPressure; value uniform {gradient:.17g}; "
        f"gradient uniform {gradient:.17g}; }}"
        if inlet_contract == FIXED_VELOCITY_FLUX_PRESSURE
        else f"inlet {{ type fixedValue; value uniform {gradient:.17g}; }}"
    )
    return _header("0", "p", "volScalarField") + f"""dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField {{
 {inlet}
 outlet {{ type fixedValue; value uniform 0; }}
 yMin {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 yMax {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }}
}}
"""


def _case_files(
    n: int,
    spec: AffineCrossflowMms,
    *,
    inlet_contract: str = FIXED_VELOCITY_FLUX_PRESSURE,
    u_solver_type: str = "smoothSolver",
    iterations: int = ITERATIONS,
    linear_solver_tolerance: float = 1.0e-10,
) -> dict[str, str]:
    if inlet_contract not in SUPPORTED_INLET_CONTRACTS:
        raise ValueError(f"unsupported inlet contract: {inlet_contract}")
    if u_solver_type not in SUPPORTED_U_SOLVERS:
        raise ValueError(f"unsupported U solver: {u_solver_type}")
    files = _baseline_case_files(n, spec)
    if iterations < 1:
        raise ValueError("iterations must be positive")
    if not 0.0 < linear_solver_tolerance < 1.0:
        raise ValueError("linear solver tolerance must be between zero and one")
    one_step_control = (
        "stopAt endTime; endTime 1; deltaT 1;\n"
        "writeControl timeStep; writeInterval 1;"
    )
    staged_control = (
        f"stopAt endTime; endTime {iterations}; deltaT 1;\n"
        f"writeControl timeStep; writeInterval {iterations};"
    )
    if one_step_control not in files["system/controlDict"]:
        raise ValueError("baseline time/write control block was not found")
    files["system/controlDict"] = files["system/controlDict"].replace(
        one_step_control, staged_control, 1
    )
    default_tolerance = "tolerance 1e-10;"
    tolerance_count = files["system/fvSolution"].count(default_tolerance)
    if tolerance_count != 2:
        raise ValueError("expected pressure and velocity linear solver tolerances")
    files["system/fvSolution"] = files["system/fvSolution"].replace(
        default_tolerance,
        f"tolerance {linear_solver_tolerance:.17g};",
    )
    files["0/p"] = _pressure_field(spec, inlet_contract)
    if inlet_contract == FIXED_PRESSURE_TANGENTIAL_VELOCITY:
        inlet_values = [
            spec.velocity(0.0, (j + 0.5) / n, (k + 0.5) / n)
            for k in range(n)
            for j in range(n)
        ]
        fixed_inlet = (
            "inlet { type fixedValue; value "
            f"{_nonuniform_vector_field(inlet_values)}; }}"
        )
        pressure_inlet = (
            "inlet { type pressureInletOutletVelocity; phi phi; "
            "tangentialVelocity uniform "
            f"(0 {spec.crossflow_velocity_m_s:.17g} 0); value "
            f"{_nonuniform_vector_field(inlet_values)}; }}"
        )
        if fixed_inlet not in files["0/U"]:
            raise ValueError("baseline inlet velocity boundary was not found")
        files["0/U"] = files["0/U"].replace(fixed_inlet, pressure_inlet, 1)
    if u_solver_type == "PBiCGStab":
        smooth = (
            "U { solver smoothSolver; smoother symGaussSeidel; "
            f"tolerance {linear_solver_tolerance:.17g}; "
            "relTol 0; } UFinal { $U; relTol 0; }"
        )
        bicg = (
            "U { solver PBiCGStab; preconditioner DILU; "
            f"tolerance {linear_solver_tolerance:.17g}; "
            "relTol 0; } UFinal { $U; relTol 0; }"
        )
        if smooth not in files["system/fvSolution"]:
            raise ValueError("baseline U linear solver block was not found")
        files["system/fvSolution"] = files["system/fvSolution"].replace(
            smooth, bicg, 1
        )
    inlet_description = (
        {"U": "analytic fixedValue", "p": "fixedFluxPressure"}
        if inlet_contract == FIXED_VELOCITY_FLUX_PRESSURE
        else {
            "U": (
                "pressureInletOutletVelocity; analytic tangential velocity; "
                "normal zeroGradient"
            ),
            "p": "analytic fixedValue; inlet pressure datum",
        }
    )
    outlet_pressure = (
        "fixedValue 0; sole pressure datum"
        if inlet_contract == FIXED_VELOCITY_FLUX_PRESSURE
        else "fixedValue 0; downstream pressure trace"
    )
    files["boundary-implementation.json"] = json.dumps(
        {
            "schema": "flowlab.affine-crossflow-flux-pressure-boundaries.v1",
            "inletContract": inlet_contract,
            "inlet": inlet_description,
            "outlet": {
                "U": "pressureInletOutletVelocity",
                "p": outlet_pressure,
            },
            "yMin": {"U": "analytic fixedValue", "p": "fixedFluxPressure"},
            "yMax": {
                "U": "analytic fixedGradient",
                "p": "fixedFluxPressure",
            },
            "initialization": (
                "analytic cell and patch pressure values; analytic initial normal "
                "gradients supplied before constrainPressure"
            ),
            "uLinearSolver": u_solver_type,
            "linearSolverTolerance": linear_solver_tolerance,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    return files


def _boundary_vectors(
    path: Path,
    patches: tuple[str, ...] = ALL_PATCHES,
) -> dict[str, list[tuple[float, float, float]]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    boundary = text.find("boundaryField")
    if boundary < 0:
        raise ValueError(f"missing boundaryField in {path}")
    text = text[boundary:]
    result: dict[str, list[tuple[float, float, float]]] = {}
    for patch in patches:
        match = re.search(
            rf"\b{patch}\s*\{{.*?value\s+nonuniform\s+List<vector>\s+"
            rf"(?P<count>\d+)\s*\((?P<values>.*?)\)\s*;",
            text,
            re.S,
        )
        if match is None:
            raise ValueError(f"missing calculated face centres for patch {patch}")
        values = [
            tuple(float(component) for component in item)
            for item in re.findall(
                r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)",
                match.group("values"),
            )
        ]
        if len(values) != int(match.group("count")):
            raise ValueError(f"invalid face-centre count for patch {patch}")
        result[patch] = values
    return result


def _initialize_exact_pressure(
    case: Path,
    spec: AffineCrossflowMms,
    inlet_contract: str = FIXED_VELOCITY_FLUX_PRESSURE,
) -> None:
    if inlet_contract not in SUPPORTED_INLET_CONTRACTS:
        raise ValueError(f"unsupported inlet contract: {inlet_contract}")
    _init_exact_internal_fields(case, spec)
    centres = _read_cell_centres(case / "0/C")
    patch_centres = _boundary_vectors(case / "0/C", PRESSURE_PATCHES)
    internal = [spec.pressure(*point) for point in centres]

    def exact_patch(name: str) -> str:
        values = [spec.pressure(*point) for point in patch_centres[name]]
        if (
            name == "inlet"
            and inlet_contract == FIXED_PRESSURE_TANGENTIAL_VELOCITY
        ):
            return (
                f"{name} {{ type fixedValue; "
                f"value {_nonuniform_scalar_field(values)}; }}"
            )
        gradient = (
            spec.pressure_gradient_m2_s2_per_m if name == "inlet" else 0.0
        )
        return (
            f"{name} {{ type fixedFluxPressure; "
            f"value {_nonuniform_scalar_field(values)}; "
            f"gradient uniform {gradient:.17g}; }}"
        )

    pressure = _header("0", "p", "volScalarField") + (
        "dimensions [0 2 -2 0 0 0 0];\n"
        f"internalField {_nonuniform_scalar_field(internal)};\n"
        "boundaryField {\n"
        f" {exact_patch('inlet')}\n"
        " outlet { type fixedValue; value uniform 0; }\n"
        f" {exact_patch('yMin')}\n"
        f" {exact_patch('yMax')}\n"
        " zMin { type symmetryPlane; } zMax { type symmetryPlane; }\n"
        "}\n"
    )
    _write(case / "0/p", pressure)
    manifest = json.loads((case / "exact-init.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "pressureBoundaryInitialization": "analytic face values from 0/C",
            "pressureInitialNormalGradient": {
                "inlet": (
                    spec.pressure_gradient_m2_s2_per_m
                    if inlet_contract == FIXED_VELOCITY_FLUX_PRESSURE
                    else "derived from analytic fixedValue"
                ),
                "yMin": 0.0,
                "yMax": 0.0,
            },
            "inletContract": inlet_contract,
        }
    )
    _write(case / "exact-init.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _foam_list(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for index, line in enumerate(lines):
        if line.strip().isdigit():
            count = int(line.strip())
            cursor = index + 1
            while cursor < len(lines) and lines[cursor].strip() != "(":
                cursor += 1
            values = [line.strip() for line in lines[cursor + 1 : cursor + 1 + count]]
            if len(values) != count:
                raise ValueError(f"invalid OpenFOAM list in {path}")
            return values
    raise ValueError(f"missing OpenFOAM list in {path}")


def _mesh_geometry(case: Path) -> tuple[list[tuple[float, float, float]], list[list[int]]]:
    points = [
        tuple(float(value) for value in line.strip("()").split())
        for line in _foam_list(case / "constant/polyMesh/points")
    ]
    faces: list[list[int]] = []
    for line in _foam_list(case / "constant/polyMesh/faces"):
        match = re.fullmatch(r"\d+\((.*?)\)", line)
        if match is None:
            raise ValueError("unsupported polyMesh face record")
        faces.append([int(value) for value in match.group(1).split()])
    return points, faces


def _cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _face_geometry(
    points: list[tuple[float, float, float]], face: list[int]
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    vertices = [points[index] for index in face]
    centre = tuple(sum(point[i] for point in vertices) / len(vertices) for i in range(3))
    area = [0.0, 0.0, 0.0]
    for index, point in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        contribution = _cross(point, following)
        for component in range(3):
            area[component] += 0.5 * contribution[component]
    return centre, tuple(area)


def _expand(values: list[float], count: int) -> list[float]:
    if len(values) == count:
        return values
    if len(values) == 1:
        return values * count
    raise ValueError(f"surface field count {len(values)} does not match {count}")


def _face_compatibility(
    case: Path,
    spec: AffineCrossflowMms,
    *,
    time_name: str = "1",
) -> dict[str, Any]:
    fields = case / time_name
    predictor_internal, predictor_patches = _surface_scalars(fields / "phiHbyA")
    correction_internal, correction_patches = _surface_scalars(fields / "pCorrectionFlux")
    gradient_internal, gradient_patches = _surface_scalars(fields / "pressureNormalGradient")
    _, pressure_patches = _surface_scalars(fields / "p")
    points, faces = _mesh_geometry(case)
    internal_count = len(predictor_internal)
    if not (len(correction_internal) == len(gradient_internal) == internal_count):
        raise ValueError("instrumented internal surface fields have inconsistent counts")
    boundary_counts = _boundary_face_counts(case)
    if set(boundary_counts) != set(ALL_PATCHES):
        raise ValueError("polyMesh boundary does not contain the expected affine patches")

    def face_values(
        name: str, internal: list[float], patches: dict[str, list[float]]
    ) -> list[float]:
        return internal if name == "internal" else _expand(patches[name], boundary_counts[name])

    sections: dict[str, Any] = {}
    correction_errors: list[float] = []
    gradient_errors: list[float] = []
    boundary_pressure_errors: list[float] = []
    face_offset = internal_count
    for name in ("internal",) + ALL_PATCHES:
        count = internal_count if name == "internal" else boundary_counts[name]
        geometry = faces[:internal_count] if name == "internal" else faces[face_offset : face_offset + count]
        if name != "internal":
            face_offset += count
        predictor = face_values(name, predictor_internal, predictor_patches)
        correction = face_values(name, correction_internal, correction_patches)
        actual_gradient = face_values(name, gradient_internal, gradient_patches)
        required: list[float] = []
        exact_gradients: list[float] = []
        exact_fluxes: list[float] = []
        for face in geometry:
            centre, area = _face_geometry(points, face)
            velocity = spec.velocity(*centre)
            exact_flux = sum(left * right for left, right in zip(area, velocity))
            area_magnitude = math.sqrt(sum(value * value for value in area))
            unit_normal = tuple(value / area_magnitude for value in area)
            exact_gradient = -spec.pressure_gradient_m2_s2_per_m * unit_normal[0]
            exact_fluxes.append(exact_flux)
            exact_gradients.append(exact_gradient)
        required = [left - right for left, right in zip(predictor, exact_fluxes)]
        local_correction_errors = [left - right for left, right in zip(correction, required)]
        local_gradient_errors = [left - right for left, right in zip(actual_gradient, exact_gradients)]
        correction_errors.extend(local_correction_errors)
        gradient_errors.extend(local_gradient_errors)
        section = {
            "faceCount": count,
            "predictorFluxSum": sum(predictor),
            "exactVelocityFluxSum": sum(exact_fluxes),
            "requiredCorrectionFluxSum": sum(required),
            "actualCorrectionFluxSum": sum(correction),
            "maxAbsoluteCorrectionMismatch": max(
                (abs(value) for value in local_correction_errors), default=0.0
            ),
            "maxAbsoluteNormalPressureGradientError": max(
                (abs(value) for value in local_gradient_errors), default=0.0
            ),
        }
        if name != "internal" and name in pressure_patches:
            actual_pressure = _expand(pressure_patches[name], count)
            exact_pressure = [spec.pressure(*_face_geometry(points, face)[0]) for face in geometry]
            local_pressure_errors = [
                left - right for left, right in zip(actual_pressure, exact_pressure)
            ]
            boundary_pressure_errors.extend(local_pressure_errors)
            section["maxAbsoluteBoundaryPressureError"] = max(
                (abs(value) for value in local_pressure_errors), default=0.0
            )
        sections[name] = section
    return {
        "method": (
            "Every OpenFOAM internal and boundary face is reconstructed from polyMesh; "
            "analytic U and grad(p) are evaluated at its centre and projected through its "
            "oriented area vector."
        ),
        "faceCount": len(correction_errors),
        "maxAbsoluteCorrectionMismatch": max(
            (abs(value) for value in correction_errors), default=math.inf
        ),
        "maxAbsoluteNormalPressureGradientError": max(
            (abs(value) for value in gradient_errors), default=math.inf
        ),
        "maxAbsoluteBoundaryPressureError": max(
            (abs(value) for value in boundary_pressure_errors), default=math.inf
        ),
        "sections": sections,
    }


def _diagnostics(
    case: Path,
    artifacts: Path,
    spec: AffineCrossflowMms,
    *,
    time_name: str = "1",
    presolve_time_name: str = "1",
) -> dict[str, Any]:
    trace = trace_flux_audit(case, time_name=time_name)
    trace["coupledHistory"] = {
        "status": "not-applicable",
        "reason": (
            "the reusable history helper is defined for the earlier constant-velocity MMS; "
            "affine field errors are reported by this probe instead"
        ),
    }
    pressure = pressure_presolve_audit(
        case,
        time_name=presolve_time_name,
        log_name="../artifacts/foamRun.log",
    )
    pressure_residual = pressure["initializedPressureResidual"]["max"]
    pressure["conclusion"] = {
        "exactInitializedPressureSatisfiesLiveEquation": (
            pressure_residual <= PRESSURE_EQUATION_RESIDUAL_LIMIT
        ),
        "finding": (
            "The initialized pressure satisfies the live assembled pressure equation "
            "to the declared absolute residual gate."
            if pressure_residual <= PRESSURE_EQUATION_RESIDUAL_LIMIT
            else "The initialized pressure does not satisfy the live assembled pressure equation."
        ),
    }
    compatibility = _face_compatibility(case, spec, time_name=time_name)
    return {
        "trace": trace,
        "pressurePreSolve": pressure,
        "diagnosticTimes": {
            "finalFaceFields": time_name,
            "exactInitializedPressurePreSolve": presolve_time_name,
        },
        "faceCompatibility": compatibility,
        "instrumentation": {
            "library": os.environ.get("FLOWLAB_INSTRUMENTED_LIBRARY"),
            "librarySha256": (
                _sha(Path(os.environ["FLOWLAB_INSTRUMENTED_LIBRARY"]))
                if os.environ.get("FLOWLAB_INSTRUMENTED_LIBRARY")
                and Path(os.environ["FLOWLAB_INSTRUMENTED_LIBRARY"]).exists()
                else None
            ),
            "ignoredMarker": (
                "FLOWLAB_EXACT_PEQN_BOUNDARY from the retained constant-field auxiliary; "
                "only live FLOWLAB_PEQN_PRE_SOLVE and written live fields are audited"
            ),
        },
    }


def run_probe(
    output: Path,
    *,
    inlet_contract: str = FIXED_VELOCITY_FLUX_PRESSURE,
    u_solver_type: str = "smoothSolver",
    schema: str = SCHEMA,
    artifact_filename: str = "affine-flux-pressure-one-step.json",
    n: int = N,
    iterations: int = ITERATIONS,
    linear_solver_tolerance: float = 1.0e-10,
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    offline = preflight()
    if offline["status"] != "authorized":
        raise ValueError("offline affine MMS preflight did not authorize OpenFOAM")
    spec = AffineCrossflowMms()
    case = output / "case"
    artifacts = output / "artifacts"
    for name, content in _case_files(
        n,
        spec,
        inlet_contract=inlet_contract,
        u_solver_type=u_solver_type,
        iterations=iterations,
        linear_solver_tolerance=linear_solver_tolerance,
    ).items():
        _write(case / name, content)
    block = _run(["blockMesh"], case, artifacts / "blockMesh.log")
    check = (
        _run(["checkMesh", "-allGeometry", "-allTopology"], case, artifacts / "checkMesh.log")
        if block == 0
        else 127
    )
    centres = (
        _run(["foamPostProcess", "-func", "writeCellCentres", "-time", "0"], case, artifacts / "writeCellCentres.log")
        if check == 0
        else 127
    )
    if centres == 0:
        _initialize_exact_pressure(case, spec, inlet_contract)
    solver = (
        _run(["foamRun", "-solver", "incompressibleFluid"], case, artifacts / "foamRun.log")
        if centres == 0
        else 127
    )
    try:
        cell_centres = _read_cell_centres(case / "0/C")
        time_name = str(iterations)
        actual_u = _values(case / time_name / "U", True)
        actual_p = _values(case / time_name / "p", False)
        velocity_error = _l2(actual_u, [spec.velocity(*point) for point in cell_centres])
        pressure_error = _l2(actual_p, [(spec.pressure(*point),) for point in cell_centres])
        diagnostics = _diagnostics(
            case,
            artifacts,
            spec,
            time_name=time_name,
        )
    except (OSError, ValueError, StopIteration, ZeroDivisionError, KeyError):
        velocity_error = pressure_error = math.inf
        diagnostics = {}
    solver_log = (artifacts / "foamRun.log").read_text(encoding="utf-8", errors="replace")
    residuals = [float(value) for value in re.findall(r"Final residual = ([0-9.eE+-]+)", solver_log)]
    final_linear_residual = max(residuals[-4:]) if residuals else math.inf
    flux = _flux_balance(case)
    face = diagnostics.get("faceCompatibility", {})
    pressure_residual = (
        diagnostics.get("pressurePreSolve", {})
        .get("initializedPressureResidual", {})
        .get("max", math.inf)
    )
    trace_identity = diagnostics.get("trace", {}).get("fluxIdentity", {}).get("passed", False)
    checks = {
        "blockMesh": block == 0,
        "checkMesh": check == 0 and "Mesh OK" in (artifacts / "checkMesh.log").read_text(encoding="utf-8", errors="replace"),
        "solverCompleted": solver == 0,
        "instrumentedTracePresent": bool(diagnostics),
        "pressureCorrectionMatchesExactVelocityFluxFaceByFace": face.get("maxAbsoluteCorrectionMismatch", math.inf) <= COMPATIBILITY_LIMIT,
        "pressureNormalGradientMatchesAnalyticFaceByFace": face.get("maxAbsoluteNormalPressureGradientError", math.inf) <= COMPATIBILITY_LIMIT,
        "boundaryPressureMatchesAnalyticFaceByFace": face.get("maxAbsoluteBoundaryPressureError", math.inf) <= COMPATIBILITY_LIMIT,
        "pressureEquationExactStateResidual": pressure_residual <= PRESSURE_EQUATION_RESIDUAL_LIMIT,
        "pressureCorrectionIdentity": trace_identity,
        "velocityExactStateRetained": velocity_error <= FIELD_ERROR_LIMIT,
        "pressureExactStateRetained": pressure_error <= FIELD_ERROR_LIMIT,
        "massBalance": flux["relativeImbalance"] <= MASS_LIMIT,
        "finalLinearResidual": final_linear_residual <= LINEAR_RESIDUAL_LIMIT,
    }
    passed = all(checks.values())
    report = {
        "schema": schema,
        "status": "authorized" if passed else "blocked",
        "scientificStatus": "one-step-exact-state-and-face-compatibility-gate",
        "validated": False,
        "definition": _definition_manifest(spec, inlet_contract),
        "boundaryImplementation": json.loads((case / "boundary-implementation.json").read_text(encoding="utf-8")),
        "execution": {
            "mesh": f"{n}^3",
            "iterations": iterations,
            "solverExitCode": solver,
            "inletContract": inlet_contract,
            "uLinearSolver": u_solver_type,
            "linearSolverTolerance": linear_solver_tolerance,
        },
        "observation": {
            "velocityRelativeL2Error": velocity_error,
            "pressureRelativeL2Error": pressure_error,
            "mass": flux,
            "finalLinearResidual": final_linear_residual,
            "diagnostics": diagnostics,
        },
        "limits": {
            "fieldRelativeL2Error": FIELD_ERROR_LIMIT,
            "massRelativeImbalance": MASS_LIMIT,
            "finalLinearResidual": LINEAR_RESIDUAL_LIMIT,
            "faceCompatibilityAbsolute": COMPATIBILITY_LIMIT,
            "pressureEquationExactStateResidualMax": PRESSURE_EQUATION_RESIDUAL_LIMIT,
        },
        "checks": checks,
        "failedChecks": [name for name, value in checks.items() if not value],
        "nextStage": {
            "coarseValidation": "authorized" if passed else "blocked",
            "coarseValidationExecuted": False,
            "threeGridValidation": "blocked pending coarse validation",
            "threeGridValidationExecuted": False,
        },
        "artifacts": {
            "case": str(case),
            "solverLog": str(artifacts / "foamRun.log"),
            "initialPressure": {"path": str(case / "0/p"), "sha256": _sha(case / "0/p")},
        },
    }
    _write(
        artifacts / artifact_filename,
        json.dumps(report, indent=2, sort_keys=True) + "\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_probe(args.output.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "authorized" else 2


if __name__ == "__main__":
    raise SystemExit(main())
