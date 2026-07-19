"""Read-only operator audit for a retained forced-MMS campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .cad_parabolic_smoke import _read_cell_centres
from .open_boundary_mms_runner import _l2, _values

SCHEMA = "flowlab.open-boundary-operator-audit.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _gradient_error(path: Path, gradient: tuple[float, float, float]) -> tuple[int, float, tuple[float, float, float]]:
    values = _values(path, True)
    numerator = sum(sum((actual - exact) ** 2 for actual, exact in zip(value, gradient)) for value in values)
    denominator = len(values) * sum(component * component for component in gradient)
    mean = tuple(sum(value[index] for value in values) / len(values) for index in range(3))
    return len(values), math.sqrt(numerator / denominator), mean


def audit(run_root: Path) -> dict[str, Any]:
    report = json.loads((run_root / "artifacts/mms-stage-report.json").read_text(encoding="utf-8"))
    g = float(report["definition"]["parameters"]["pressure_gradient_m2_s2_per_m"])
    expected = (-g, 0.0, 0.0)
    levels: list[dict[str, Any]] = []
    for level in report["levels"]:
        name = str(level["level"])
        grad = run_root / name / "case/0/grad(p)"
        count, relative_error, mean = _gradient_error(grad, expected)
        levels.append({"level": name, "cellCount": count, "relativeL2Error": relative_error, "meanGradient": mean, "field": str(grad), "sha256": _sha(grad)})
    source = run_root / "coarse/case/constant/fvModels"
    source_text = source.read_text(encoding="utf-8")
    source_sign_ok = f"explicit ({-g:.17g} 0 0)" in source_text and "volumeMode specific" in source_text
    pressure_gradient_ok = all(item["relativeL2Error"] < 1.0e-12 for item in levels)
    traction_ok = all(float(level["boundary_traction_relative_imbalance"]) <= 1.0e-6 for level in report["levels"])
    return {
        "schema": SCHEMA,
        "run": str(run_root),
        "status": "audited",
        "continuousDerivation": {
            "solverAssembly": "L(U) = fvModels.source(U) - grad(p)",
            "exactFields": "U=(1,0,0), p=G*(1-x)",
            "gradP": list(expected),
            "requiredSource": list(expected),
        },
        "sourceVolumeAudit": {"passed": source_sign_ok, "evidence": "semiImplicitSource with volumeMode specific; OpenFOAM applies the explicit field as a per-volume source", "sourceFile": str(source), "sha256": _sha(source)},
        "pressureGradientAudit": {"passed": pressure_gradient_ok, "levels": levels},
        "tractionCrossCheck": {"passed": traction_ok, "relativeImbalances": [level["boundary_traction_relative_imbalance"] for level in report["levels"]]},
        "conclusion": {
            "cleared": ["momentum-source sign", "specific-source volume scaling", "Gauss-linear grad(p) on the exact initialized field"],
            "remainingSuspect": "coupled SIMPLE pressure-correction and face-flux reconstruction under simultaneous fixed inlet/outlet velocity and pressure values",
            "nextAction": "Instrument phiHbyA, pressure-correction flux, and continuity contribution face by face; do not change mesh, tolerances, or boundary conditions first.",
        },
    }


def _scalar_values(text: str) -> list[float]:
    return [value[0] for value in _values_from_text(text, False)]


def _values_from_text(text: str, vector: bool) -> list[tuple[float, ...]]:
    lines = text.splitlines()
    marker = "List<vector>" if vector else "List<scalar>"
    start = next(i for i, line in enumerate(lines) if marker in line)
    count = int(lines[start + 1])
    return [tuple(float(x) for x in line.strip().strip("()").split()) for line in lines[start + 3:start + 3 + count]]


def _brace_block(text: str, start: int) -> tuple[str, int]:
    if text[start] != "{":
        raise ValueError("expected opening brace")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise ValueError("unterminated OpenFOAM boundary block")


def _boundary_scalars(path: Path) -> dict[str, list[float]]:
    """Read scalar surface-field patch values without interpreting BC types."""
    text = path.read_text(encoding="utf-8", errors="replace")
    boundary = text.find("boundaryField")
    if boundary < 0:
        return {}
    cursor = text.find("{", boundary)
    body, _ = _brace_block(text, cursor)
    result: dict[str, list[float]] = {}
    offset = 0
    while offset < len(body):
        match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\{", body[offset:])
        if not match:
            break
        name = match.group(1)
        opening = offset + match.end() - 1
        patch, offset = _brace_block(body, opening)
        uniform = re.search(r"\bvalue\s+uniform\s+([-+0-9.eE]+)", patch)
        if uniform:
            result[name] = [float(uniform.group(1))]
            continue
        nonuniform = re.search(r"\bvalue\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)", patch, re.S)
        if nonuniform:
            values = [float(value) for value in nonuniform.group(2).split()]
            if len(values) != int(nonuniform.group(1)):
                raise ValueError(f"patch {name} has an invalid scalar count in {path}")
            result[name] = values
    return result


def _surface_scalars(path: Path) -> tuple[list[float], dict[str, list[float]]]:
    return _scalar_values(path.read_text(encoding="utf-8", errors="replace")), _boundary_scalars(path)


def _max_relative(values: list[float], scale_values: list[float]) -> tuple[float, float]:
    maximum = max((abs(value) for value in values), default=0.0)
    scale = max(max((abs(value) for value in scale_values), default=0.0), 1e-30)
    return maximum, maximum / scale


def _flux_identity(
    predictor: list[float], correction: list[float], final_flux: list[float]
) -> dict[str, Any]:
    if not (len(predictor) == len(correction) == len(final_flux)):
        raise ValueError("traced surface flux fields have inconsistent face counts")
    mismatch = [predictor[index] - correction[index] - final_flux[index] for index in range(len(final_flux))]
    maximum, relative = _max_relative(mismatch, predictor)
    return {
        "equation": "phi = phiHbyA - pCorrectionFlux",
        "maxAbsoluteMismatch": maximum,
        "maxRelativeMismatch": relative,
        "passed": maximum <= 1e-12 * max(max((abs(value) for value in predictor), default=0.0), 1e-30),
    }


def _velocity_flux_comparison(phi: list[float], u_flux: list[float]) -> dict[str, Any]:
    if len(phi) != len(u_flux):
        raise ValueError("phi and cell-centred U flux have inconsistent face counts")
    maximum, relative = _max_relative([left - right for left, right in zip(phi, u_flux)], phi)
    return {"equation": "phi - fvc::flux(U)", "maxAbsoluteMismatch": maximum, "maxRelativeMismatch": relative}


def _boundary_face_counts(case: Path) -> dict[str, int]:
    boundary = case / "constant/polyMesh/boundary"
    if not boundary.exists():
        return {}
    text = boundary.read_text(encoding="utf-8", errors="replace")
    return {name: int(count) for name, count in re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{[^{}]*?\bnFaces\s+(\d+);", text, re.M | re.S)}


def _patch_report(
    predictor: dict[str, list[float]], correction: dict[str, list[float]], final_flux: dict[str, list[float]], u_flux: dict[str, list[float]], face_counts: dict[str, int]
) -> dict[str, Any]:
    patches: dict[str, Any] = {}
    for patch in sorted(set(predictor) | set(correction) | set(final_flux) | set(u_flux)):
        if patch not in predictor or patch not in correction or patch not in final_flux or patch not in u_flux:
            raise ValueError(f"patch {patch} is missing from a traced surface field")
        count = face_counts.get(patch, max(len(predictor[patch]), len(correction[patch]), len(final_flux[patch]), len(u_flux[patch])))
        values = {
            "predictor": predictor[patch] * count if len(predictor[patch]) == 1 else predictor[patch],
            "correction": correction[patch] * count if len(correction[patch]) == 1 else correction[patch],
            "final": final_flux[patch] * count if len(final_flux[patch]) == 1 else final_flux[patch],
            "uFlux": u_flux[patch] * count if len(u_flux[patch]) == 1 else u_flux[patch],
        }
        if any(len(item) != count for item in values.values()):
            raise ValueError(f"patch {patch} has incompatible traced face counts")
        patches[patch] = {
            "faceCount": count,
            "predictorFluxSum": sum(values["predictor"]),
            "pressureCorrectionFluxSum": sum(values["correction"]),
            "finalFluxSum": sum(values["final"]),
            "pressureCorrectionIdentity": _flux_identity(values["predictor"], values["correction"], values["final"]),
            "cellCenteredVelocityFlux": _velocity_flux_comparison(values["final"], values["uFlux"]),
        }
    return patches


def _history(case: Path) -> dict[str, Any]:
    definition_path = case / "mms-definition.json"
    centres_path = case / "0/C"
    if not definition_path.exists() or not centres_path.exists():
        return {"status": "not-retained", "reason": "analytic definition or cell centres unavailable"}
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    g = float(definition["parameters"]["pressure_gradient_m2_s2_per_m"])
    centres = _read_cell_centres(centres_path)
    times = sorted((path for path in case.iterdir() if path.is_dir() and path.name.replace(".", "", 1).isdigit()), key=lambda path: float(path.name))
    rows: list[dict[str, Any]] = []
    required = ("U", "p", "phi", "phiHbyA", "pCorrectionFlux", "cellCenteredUFlux", "coupledMomentumResidual")
    for directory in times:
        if not all((directory / field).exists() for field in required):
            continue
        velocity = _values(directory / "U", True)
        pressure = _values(directory / "p", False)
        exact_velocity = [(1.0, 0.0, 0.0)] * len(centres)
        exact_pressure = [(g * (1.0 - point[0]),) for point in centres]
        phi, _ = _surface_scalars(directory / "phi")
        u_flux, _ = _surface_scalars(directory / "cellCenteredUFlux")
        predictor, _ = _surface_scalars(directory / "phiHbyA")
        correction, _ = _surface_scalars(directory / "pCorrectionFlux")
        residual = _values(directory / "coupledMomentumResidual", True)
        residual_norms = [math.sqrt(sum(component * component for component in value)) for value in residual]
        rows.append({
            "time": directory.name,
            "velocityRelativeL2Error": _l2(velocity, exact_velocity),
            "pressureRelativeL2Error": _l2(pressure, exact_pressure),
            "pressureCorrectionIdentity": _flux_identity(predictor, correction, phi),
            "cellCenteredVelocityFlux": _velocity_flux_comparison(phi, u_flux),
            "coupledMomentumResidual": {
                "maxNorm": max(residual_norms, default=0.0),
                "rmsNorm": math.sqrt(sum(value * value for value in residual_norms) / max(len(residual_norms), 1)),
            },
        })
    if not rows:
        return {"status": "not-retained", "reason": "required trace fields were not written at a common time"}
    def summary(name: str, nested: str | None = None) -> dict[str, float]:
        values = [float(row[name] if nested is None else row[name][nested]) for row in rows]
        plateau = values[-10:]
        return {"first": values[0], "last": values[-1], "lastTenMin": min(plateau), "lastTenMax": max(plateau)}
    return {
        "status": "audited",
        "stepCount": len(rows),
        "summary": {
            "velocityRelativeL2Error": summary("velocityRelativeL2Error"),
            "pressureRelativeL2Error": summary("pressureRelativeL2Error"),
            "coupledMomentumResidualRms": summary("coupledMomentumResidual", "rmsNorm"),
            "cellCenteredVelocityFluxRelativeMismatch": summary("cellCenteredVelocityFlux", "maxRelativeMismatch"),
        },
        "rows": rows,
    }


def trace_flux_audit(case: Path, *, time_name: str = "100") -> dict[str, Any]:
    """Audit the pressure-correction flux identity from the traced solver.

    ``correctPressure.C`` writes these otherwise local fields before and after
    the correction.  This verifier is read-only: it checks the exact identity
    phi = phiHbyA - pCorrectionFlux and summarizes the per-cell divergence
    field that is assembled face-by-face by OpenFOAM.
    """
    fields = case / time_name
    predictor, predictor_patches = _surface_scalars(fields / "phiHbyA")
    correction, correction_patches = _surface_scalars(fields / "pCorrectionFlux")
    final_flux, final_flux_patches = _surface_scalars(fields / "phi")
    continuity = _values(fields / "faceContinuityResidual", False)
    u_flux_path = fields / "cellCenteredUFlux"
    u_flux, u_flux_patches = _surface_scalars(u_flux_path) if u_flux_path.exists() else ([], {})
    continuity_values = [value[0] for value in continuity]
    result = {
        "schema": "flowlab.open-boundary-flux-trace-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "internalFaceCount": len(final_flux),
        "fluxIdentity": _flux_identity(predictor, correction, final_flux),
        "faceByFaceContinuity": {
            "field": str(fields / "faceContinuityResidual"),
            "cellCount": len(continuity_values),
            "maxAbsoluteDivergence": max((abs(value) for value in continuity_values), default=0.0),
            "rmsDivergence": math.sqrt(sum(value * value for value in continuity_values) / max(len(continuity_values), 1)),
        },
        "rawFields": {name: {"path": str(fields / name), "sha256": _sha(fields / name)} for name in ("phiHbyA", "pCorrectionFlux", "phi", "faceContinuityResidual")},
    }
    if u_flux_path.exists():
        result["cellCenteredVelocityFlux"] = _velocity_flux_comparison(final_flux, u_flux)
        result["boundaryPatches"] = _patch_report(predictor_patches, correction_patches, final_flux_patches, u_flux_patches, _boundary_face_counts(case))
        result["rawFields"]["cellCenteredUFlux"] = {"path": str(u_flux_path), "sha256": _sha(u_flux_path)}
        for name in ("phiMinusCellCenteredUFlux", "coupledMomentumResidual"):
            path = fields / name
            if path.exists():
                result["rawFields"][name] = {"path": str(path), "sha256": _sha(path)}
    result["coupledHistory"] = _history(case)
    return result


def _field_stats(values: list[float]) -> dict[str, float]:
    return {
        "sum": sum(values),
        "mean": sum(values) / max(len(values), 1),
        "min": min(values, default=0.0),
        "max": max(values, default=0.0),
    }


def _pressure_matrix_terms(log: Path, time_name: str, *, marker: str = "FLOWLAB_PEQN_BOUNDARY") -> dict[str, dict[str, float | int]]:
    if not log.exists():
        raise FileNotFoundError(f"missing pressure-matrix diagnostic log: {log}")
    result: dict[str, dict[str, float | int]] = {}
    pattern = re.compile(
        re.escape(marker) + r" time=(?P<time>\S+) patch=(?P<patch>\S+) faceCount=(?P<count>\d+) internalCoeffSum=(?P<internal>[-+0-9.eE]+) boundaryCoeffSum=(?P<boundary>[-+0-9.eE]+)"
    )
    for match in pattern.finditer(log.read_text(encoding="utf-8", errors="replace")):
        if match.group("time") == time_name:
            result[match.group("patch")] = {
                "faceCount": int(match.group("count")),
                "internalCoefficientSum": float(match.group("internal")),
                "boundaryCoefficientSum": float(match.group("boundary")),
            }
    if not result:
        raise ValueError(f"no {marker} records found for time {time_name} in {log}")
    return result


def boundary_coupling_audit(case: Path, *, time_name: str = "100", log_name: str = "log.boundaryTraceFoamRun") -> dict[str, Any]:
    """Audit fixed-pressure boundary terms against final flux and normal-gradient traces."""
    fields = case / time_name
    definition = json.loads((case / "mms-definition.json").read_text(encoding="utf-8"))
    gradient = float(definition["parameters"]["pressure_gradient_m2_s2_per_m"])
    required = ("p", "phi", "cellCenteredUFlux", "phiHbyA", "pCorrectionFlux", "pressureNormalGradient", "pressureGradientFlux", "requiredPressureCorrectionFlux")
    values = {name: _surface_scalars(fields / name)[1] for name in required}
    matrix = _pressure_matrix_terms(case / log_name, time_name)
    expected_normal_gradient = {"inlet": gradient, "outlet": -gradient}
    patches: dict[str, Any] = {}
    for patch in ("inlet", "outlet"):
        if patch not in matrix or any(patch not in values[name] for name in required):
            raise ValueError(f"missing inlet/outlet boundary trace for {patch}")
        actual_gradient = values["pressureNormalGradient"][patch]
        exact = expected_normal_gradient[patch]
        gradient_error = [value - exact for value in actual_gradient]
        compatibility = [left - right for left, right in zip(values["pCorrectionFlux"][patch], values["requiredPressureCorrectionFlux"][patch])]
        pressure_gradient_flux_identity = [left - right for left, right in zip(values["pCorrectionFlux"][patch], values["pressureGradientFlux"][patch])]
        patches[patch] = {
            "pressureMatrix": matrix[patch],
            "imposedKinematicPressure": _field_stats(values["p"][patch]),
            "normalPressureGradient": {
                "expected": exact,
                "actual": _field_stats(actual_gradient),
                "maxAbsoluteError": max((abs(value) for value in gradient_error), default=0.0),
            },
            "pressureGradientFlux": _field_stats(values["pressureGradientFlux"][patch]),
            "pressureGradientFluxIdentity": {
                "equation": "pCorrectionFlux = interpolate(rAtU)*snGrad(p)*magSf",
                "maxAbsoluteMismatch": max((abs(value) for value in pressure_gradient_flux_identity), default=0.0),
            },
            "predictorFlux": _field_stats(values["phiHbyA"][patch]),
            "pressureCorrectionFlux": _field_stats(values["pCorrectionFlux"][patch]),
            "requiredPressureCorrectionFluxForVelocity": _field_stats(values["requiredPressureCorrectionFlux"][patch]),
            "correctionCompatibility": {
                "equation": "pCorrectionFlux - (phiHbyA - fvc::flux(U)) = fvc::flux(U) - phi",
                "maxAbsoluteMismatch": max((abs(value) for value in compatibility), default=0.0),
            },
            "finalFlux": _field_stats(values["phi"][patch]),
            "imposedVelocityFlux": _field_stats(values["cellCenteredUFlux"][patch]),
        }
    return {
        "schema": "flowlab.open-boundary-boundary-coupling-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "pressureGradient_m2_s2_per_m": gradient,
        "patches": patches,
        "rawFields": {name: {"path": str(fields / name), "sha256": _sha(fields / name)} for name in required},
        "matrixLog": {"path": str(case / log_name), "sha256": _sha(case / log_name)},
    }


def exact_boundary_matrix_audit(case: Path, *, time_name: str = "1", log_name: str = "log.exactMatrixFoamRun") -> dict[str, Any]:
    """Compare the first actual pressure matrix with a separately assembled exact MMS matrix."""
    log = case / log_name
    actual = _pressure_matrix_terms(log, time_name)
    exact = _pressure_matrix_terms(log, time_name, marker="FLOWLAB_EXACT_PEQN_BOUNDARY")
    patches: dict[str, Any] = {}
    for patch in ("inlet", "outlet"):
        if patch not in actual or patch not in exact:
            raise ValueError(f"missing actual or exact pEqn boundary record for {patch}")
        terms: dict[str, Any] = {}
        for name in ("internalCoefficientSum", "boundaryCoefficientSum"):
            exact_value = float(exact[patch][name])
            actual_value = float(actual[patch][name])
            difference = actual_value - exact_value
            terms[name] = {
                "exact": exact_value,
                "actual": actual_value,
                "difference": difference,
                "relativeDifference": abs(difference) / max(abs(exact_value), 1e-30),
            }
        patches[patch] = {
            "faceCount": exact[patch]["faceCount"],
            "terms": terms,
        }
    return {
        "schema": "flowlab.open-boundary-exact-pressure-matrix-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "method": "A separate pEqn is assembled from exact initialized U=(1,0,0) and the untouched exact initial p before the first pressure correction; it is neither solved nor assigned to the runtime state.",
        "patches": patches,
        "matrixLog": {"path": str(log), "sha256": _sha(log)},
    }


_VECTOR_VALUE = r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)"


def _trace_vector(line: str, name: str) -> tuple[float, float, float]:
    match = re.search(rf"\b{re.escape(name)}={_VECTOR_VALUE}", line)
    if not match:
        raise ValueError(f"missing {name} in FLOWLAB_EXACT_UEQN record")
    return tuple(float(match.group(index)) for index in range(1, 4))


def _trace_scalar(line: str, name: str) -> float:
    match = re.search(rf"\b{re.escape(name)}=([-+0-9.eE]+)", line)
    if not match:
        raise ValueError(f"missing {name} in FLOWLAB_EXACT_UEQN record")
    return float(match.group(1))


def _norm(value: tuple[float, float, float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def exact_ueqn_audit(case: Path, *, time_name: str = "1", log_name: str = "log.exactUeqnFoamRun") -> dict[str, Any]:
    """Audit the exact-state momentum RHS decomposition without solving it.

    The diagnostic module reconstructs the MMS equation and copies the live
    predictor matrix before its first solve. It never assigns a field, changes
    a source, or changes a boundary condition.
    """
    log = case / log_name
    if not log.exists():
        raise FileNotFoundError(f"missing exact UEqn diagnostic log: {log}")
    records = [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("FLOWLAB_EXACT_UEQN ") and f" time={time_name} " in f" {line} "]
    if not records:
        raise ValueError(f"no FLOWLAB_EXACT_UEQN record found for time {time_name} in {log}")
    line = records[-1]
    model_source = _trace_vector(line, "modelSourceMatrixIntegral")
    equation_source = _trace_vector(line, "assembledEquationSourceIntegral")
    pressure_rhs = _trace_vector(line, "pressureGradientRhsIntegral")
    source_plus_pressure = _trace_vector(line, "sourcePlusPressureRhs")
    exact_matrix_residual = _trace_vector(line, "matrixResidualIntegral")
    exact_full_residual = _trace_vector(line, "fullResidualIntegral")
    exact_full_l2 = _trace_scalar(line, "fullResidualL2")
    exact_equality_residual = _trace_vector(line, "exactEqualityResidualIntegral")
    exact_equality_l2 = _trace_scalar(line, "exactEqualityResidualL2")
    actual_source = _trace_vector(line, "actualAssembledSourceIntegral")
    actual_matrix_residual = _trace_vector(line, "actualMatrixResidualIntegral")
    actual_equality_residual = _trace_vector(line, "actualEqualityResidualIntegral")
    actual_equality_l2 = _trace_scalar(line, "actualEqualityResidualL2")
    first_ux = re.search(r"smoothSolver:  Solving for Ux, Initial residual = ([-+0-9.eE]+), Final residual = ([-+0-9.eE]+), No Iterations (\d+)", log.read_text(encoding="utf-8", errors="replace"))
    operator_tolerance = 1e-12
    operator_passed = exact_equality_l2 <= operator_tolerance and actual_equality_l2 <= operator_tolerance
    source_pressure_passed = _norm(source_plus_pressure) <= operator_tolerance
    result: dict[str, Any] = {
        "schema": "flowlab.open-boundary-exact-ueqn-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "method": "The diagnostic-only module independently reconstructs the exact initialized U equation and copies the live UEqn before the first solve; it applies no field, source, or boundary-condition change.",
        "rhsConvention": {
            "modelMatrix": "fvModels().source(U)",
            "assembledEquation": "L(U) == fvModels().source(U) is represented by L(U) - fvModels().source(U)",
            "pressureCorrection": "UEqn == -fvc::grad(p) adds the pressure-gradient RHS to the assembled fvMatrix source",
        },
        "exactState": {
            "modelSourceMatrixIntegral": model_source,
            "assembledEquationSourceIntegral": equation_source,
            "pressureGradientRhsIntegral": pressure_rhs,
            "sourcePlusPressureRhs": source_plus_pressure,
            "sourcePressureBalance": {"norm": _norm(source_plus_pressure), "tolerance": operator_tolerance, "passed": source_pressure_passed},
            "matrixResidualIntegral": exact_matrix_residual,
            "fullResidualIntegral": exact_full_residual,
            "fullResidualL2": exact_full_l2,
            "equalityOperatorResidualIntegral": exact_equality_residual,
            "equalityOperatorResidualL2": exact_equality_l2,
        },
        "livePredictorBeforeSolve": {
            "assembledSourceIntegral": actual_source,
            "matrixResidualIntegral": actual_matrix_residual,
            "equalityOperatorResidualIntegral": actual_equality_residual,
            "equalityOperatorResidualL2": actual_equality_l2,
        },
        "operatorGate": {"tolerance": operator_tolerance, "passed": operator_passed},
        "rawLog": {"path": str(log), "sha256": _sha(log)},
        "conclusion": {
            "sourceOrBcChangeJustified": False,
            "cleared": "At the retained exact initial state, both the independently reconstructed and live UEqn equality-operator residuals are at round-off; the assembled MMS source and pressure-gradient RHS cancel.",
            "remainingQuestion": "The separately reported first Ux linear-solver residual must be interpreted through the solver's residual normalization/linear-system path; it is not evidence that the exact assembled UEqn has a source or pressure-gradient defect.",
            "nextAction": "Audit the Ux linear-system residual normalization and preconditioned solve path on this unchanged trace before changing a source or boundary-condition term.",
        },
    }
    if first_ux:
        result["firstReportedUxSolve"] = {"initialResidual": float(first_ux.group(1)), "finalResidual": float(first_ux.group(2)), "iterations": int(first_ux.group(3))}
    return result


def _dictionary_value_block(text: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\{{", text)
    if not match:
        raise ValueError(f"missing dictionary block {name}")
    return _brace_block(text, match.end() - 1)[0]


def _solver_controls(case: Path, field: str = "U") -> dict[str, Any]:
    text = (case / "system/fvSolution").read_text(encoding="utf-8", errors="replace")
    block = _dictionary_value_block(_dictionary_value_block(text, "solvers"), field)
    controls: dict[str, Any] = {}
    for name in ("solver", "smoother"):
        match = re.search(rf"\b{name}\s+(\S+);", block)
        if match:
            controls[name] = match.group(1)
    for name in ("tolerance", "relTol", "maxIter", "minIter", "nSweeps"):
        match = re.search(rf"\b{name}\s+([-+0-9.eE]+);", block)
        if match:
            controls[name] = int(match.group(1)) if name in {"maxIter", "minIter", "nSweeps"} else float(match.group(1))
    return controls


def ux_linear_path_audit(case: Path, *, time_name: str = "1", log_name: str = "log.uxNormalizationFoamRun") -> dict[str, Any]:
    """Explain smoothSolver's first-Ux residual using its actual denominator.

    This parser consumes only the diagnostic log. The denominator is emitted by
    the retained module's temporary ``lduMatrix::debug=2`` setting during the
    first solve; no solver controls or numerical inputs are changed.
    """
    log = case / log_name
    if not log.exists():
        raise FileNotFoundError(f"missing Ux normalization diagnostic log: {log}")
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    ux_index = next((index for index, line in enumerate(lines) if re.search(r"smoothSolver:\s+Solving for Ux,", line)), None)
    if ux_index is None:
        raise ValueError(f"no Ux smoothSolver record found in {log}")
    ux_match = re.search(r"Initial residual = ([-+0-9.eE]+), Final residual = ([-+0-9.eE]+), No Iterations (\d+)", lines[ux_index])
    if not ux_match:
        raise ValueError(f"invalid Ux smoothSolver record in {log}")
    normalization_match = next((re.search(r"Normalisation factor = ([-+0-9.eE]+)", lines[index]) for index in range(ux_index - 1, -1, -1) if "Normalisation factor" in lines[index]), None)
    if normalization_match is None:
        raise ValueError(f"no preceding Ux normalization factor found in {log}")
    path_line = next((line for line in lines if line.startswith("FLOWLAB_UX_SOLVER_PATH ") and f" time={time_name} " in f" {line} "), None)
    if path_line is None:
        raise ValueError(f"no FLOWLAB_UX_SOLVER_PATH record found for time {time_name} in {log}")
    path_match = re.search(r"solver=(\S+) initialResidual=" + _VECTOR_VALUE + r" finalResidual=" + _VECTOR_VALUE + r" iterations=\((\d+)\s+(\d+)\s+(\d+)\) lduMatrixDebugDuringSolve=(\d+)", path_line)
    if not path_match:
        raise ValueError(f"invalid FLOWLAB_UX_SOLVER_PATH record in {log}")
    normalization = float(normalization_match.group(1))
    initial = float(ux_match.group(1))
    final = float(ux_match.group(2))
    controls = _solver_controls(case)
    raw_initial = normalization * initial
    raw_final = normalization * final
    max_iterations = int(controls.get("maxIter", 1000))
    return {
        "schema": "flowlab.open-boundary-ux-linear-path-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "method": "OpenFOAM smoothSolver defines the reported residual as sum(|source - Apsi|) / normFactor. The diagnostic-only module enables lduMatrix debug for the first solve solely to retain normFactor and returned solver performance.",
        "solverControls": controls,
        "firstUx": {
            "normalizationFactor": normalization,
            "reportedInitialResidual": initial,
            "reportedFinalResidual": final,
            "rawInitialResidualL1": raw_initial,
            "rawFinalResidualL1": raw_final,
            "iterations": int(ux_match.group(3)),
            "hitConfiguredOrDefaultMaxIterations": int(ux_match.group(3)) >= max_iterations,
        },
        "returnedPath": {
            "solver": path_match.group(1),
            "initialResidual": tuple(float(path_match.group(index)) for index in range(2, 5)),
            "finalResidual": tuple(float(path_match.group(index)) for index in range(5, 8)),
            "iterations": tuple(int(path_match.group(index)) for index in range(8, 11)),
            "lduMatrixDebugDuringFirstSolve": int(path_match.group(11)),
            "interpretation": "smoothSolver uses the configured symGaussSeidel smoother as its iterative path; this configuration has no separately declared Krylov preconditioner.",
        },
        "interpretation": {
            "normalizationArtifact": normalization < 1e-12 and raw_initial < 1e-12,
            "finding": "The first Ux normalized residual is near one because both its numerator and normalization factor are round-off-scale, not because the assembled exact-state Ux equation has an O(1) raw residual.",
            "notCleared": "This explains the 1000 first-step smoother iterations but does not validate the open-boundary pressure/velocity formulation or clear its rejected QoI gates.",
            "nextAction": "Keep source and BC terms unchanged. Treat raw and normalized linear residuals separately in acceptance reporting, then continue the dedicated open-boundary formulation campaign.",
        },
        "rawLog": {"path": str(log), "sha256": _sha(log)},
    }


def _marker_values(line: str) -> dict[str, str]:
    return {name: value for name, value in re.findall(r"\b([A-Za-z][A-Za-z0-9]*)=([^\s]+)", line)}


def pressure_presolve_audit(case: Path, *, time_name: str = "1", log_name: str = "log.pEqnPreSolveFoamRun") -> dict[str, Any]:
    """Audit the live pEqn after assembly and before its first solve."""
    log = case / log_name
    if not log.exists():
        raise FileNotFoundError(f"missing pEqn pre-solve diagnostic log: {log}")
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    record = next((line for line in lines if line.startswith("FLOWLAB_PEQN_PRE_SOLVE ") and f" time={time_name} " in f" {line} "), None)
    if record is None:
        raise ValueError(f"no FLOWLAB_PEQN_PRE_SOLVE record found for time {time_name} in {log}")
    values = _marker_values(record)
    required = {
        "referenceCell", "referenceApplied", "referenceValue", "referencePressureBefore", "referencePressureAfter",
        "noReferenceDiagonal", "noReferenceSource", "referenceDiagonal", "referenceSource",
        "predictorDivergenceIntegral", "predictorDivergenceL2", "predictorDivergenceMax",
        "exactPressureResidualIntegral", "exactPressureResidualL1", "exactPressureResidualL2", "exactPressureResidualMax",
        "directSourceSum", "requiredCorrectionInternalFluxSum",
    }
    missing = required - set(values)
    if missing:
        raise ValueError(f"incomplete FLOWLAB_PEQN_PRE_SOLVE record: missing {sorted(missing)}")
    patches: dict[str, Any] = {}
    for line in lines:
        if not line.startswith("FLOWLAB_PEQN_PRE_SOLVE_BOUNDARY ") or f" time={time_name} " not in f" {line} ":
            continue
        patch = _marker_values(line)
        name = patch.get("patch")
        if name is None:
            raise ValueError("pre-solve pressure boundary record is missing its patch name")
        patches[name] = {
            "faceCount": int(patch["faceCount"]),
            "predictorFluxSum": float(patch["predictorFluxSum"]),
            "requiredCorrectionFluxSum": float(patch["requiredCorrectionFluxSum"]),
            "internalCoefficientSum": float(patch["internalCoeffSum"]),
            "boundaryRhsSum": float(patch["boundaryRhsSum"]),
        }
    if not patches:
        raise ValueError("no FLOWLAB_PEQN_PRE_SOLVE_BOUNDARY records found")
    correction_abs_total = sum(abs(float(patch["requiredCorrectionFluxSum"])) for patch in patches.values())
    for patch in patches.values():
        patch["requiredCorrectionAbsoluteShare"] = abs(float(patch["requiredCorrectionFluxSum"])) / max(correction_abs_total, 1e-30)
    dominant_patch = max(patches, key=lambda name: abs(float(patches[name]["requiredCorrectionFluxSum"])))
    reference_applied = values["referenceApplied"].lower() == "true"
    return {
        "schema": "flowlab.open-boundary-pressure-presolve-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "method": "The live pEqn is assembled after constrainPressure and before pEqn.solve(). Its residual is evaluated against the retained initialized pressure; no additional equation is solved and no source or boundary condition is changed.",
        "reference": {
            "applied": reference_applied,
            "cell": int(values["referenceCell"]),
            "value": float(values["referenceValue"]),
            "pressureBefore": float(values["referencePressureBefore"]),
            "pressureAfter": float(values["referencePressureAfter"]),
            "noReferenceDiagonal": float(values["noReferenceDiagonal"]),
            "noReferenceSource": float(values["noReferenceSource"]),
            "diagonal": float(values["referenceDiagonal"]),
            "source": float(values["referenceSource"]),
        },
        "predictorFluxDivergence": {
            "integral": float(values["predictorDivergenceIntegral"]),
            "l2": float(values["predictorDivergenceL2"]),
            "max": float(values["predictorDivergenceMax"]),
            "directPressureEquationSourceSum": float(values["directSourceSum"]),
        },
        "initializedPressureResidual": {
            "integral": float(values["exactPressureResidualIntegral"]),
            "l1": float(values["exactPressureResidualL1"]),
            "l2": float(values["exactPressureResidualL2"]),
            "max": float(values["exactPressureResidualMax"]),
        },
        "requiredCorrection": {
            "internalFluxSum": float(values["requiredCorrectionInternalFluxSum"]),
            "dominantPatch": dominant_patch,
            "dominantPatchAbsoluteShare": patches[dominant_patch]["requiredCorrectionAbsoluteShare"],
        },
        "boundaryPatches": patches,
        "conclusion": {
            "referenceCellCauseExcluded": not reference_applied,
            "finding": "The exact initialized pressure does not satisfy the live assembled pressure equation, and the predictor-flux imbalance is localized by the required pressure correction rather than by a pressure reference-cell constraint.",
            "nextAction": "Audit the outlet constrainPressure/updateCoeffs path and the construction of outlet phiHbyA against the imposed outlet velocity flux. Do not alter source, mesh, tolerance, or a boundary-condition term before that audit identifies the incompatible contribution.",
        },
        "rawLog": {"path": str(log), "sha256": _sha(log)},
    }


def outlet_constrain_pressure_audit(
    case: Path,
    *,
    time_name: str = "1",
    log_name: str = "log.outletConstrainPressureFoamRun",
) -> dict[str, Any]:
    """Audit whether constrainPressure can reconcile the outlet predictor flux."""
    log = case / log_name
    if not log.exists():
        raise FileNotFoundError(f"missing outlet constrainPressure diagnostic log: {log}")
    records: dict[str, dict[str, str]] = {}
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("FLOWLAB_CONSTRAIN_PRESSURE ") or f" time={time_name} " not in f" {line} ":
            continue
        values = _marker_values(line)
        if values.get("patch") != "outlet":
            continue
        phase = values.get("phase")
        if phase not in {"before", "after"}:
            raise ValueError("outlet constrainPressure record has an invalid phase")
        records[phase] = values
    if set(records) != {"before", "after"}:
        raise ValueError("outlet constrainPressure audit requires before and after records")

    required = {
        "pressureType", "velocityType", "pressureFixesValue", "velocityFixesValue",
        "fixedFluxPressure", "pressureUpdated", "predictorFluxSum", "velocityFluxSum",
        "requiredGradientMean", "actualGradientMean",
    }
    for phase, values in records.items():
        missing = required - set(values)
        if missing:
            raise ValueError(f"incomplete {phase} outlet constrainPressure record: missing {sorted(missing)}")

    def truth(value: str) -> bool:
        return value.lower() in {"1", "true", "yes", "on"}

    def decoded(values: dict[str, str]) -> dict[str, Any]:
        return {
            "pressureType": values["pressureType"],
            "velocityType": values["velocityType"],
            "pressureFixesValue": truth(values["pressureFixesValue"]),
            "velocityFixesValue": truth(values["velocityFixesValue"]),
            "fixedFluxPressureEligible": truth(values["fixedFluxPressure"]),
            "pressureUpdated": truth(values["pressureUpdated"]),
            "predictorFluxSum": float(values["predictorFluxSum"]),
            "imposedVelocityFluxSum": float(values["velocityFluxSum"]),
            "requiredPressureNormalGradientMean": float(values["requiredGradientMean"]),
            "actualPressureNormalGradientMean": float(values["actualGradientMean"]),
        }

    before, after = decoded(records["before"]), decoded(records["after"])
    predictor_flux_mismatch = before["predictorFluxSum"] - before["imposedVelocityFluxSum"]
    gradient_change = after["actualPressureNormalGradientMean"] - before["actualPressureNormalGradientMean"]
    simultaneous_fixed_values = before["pressureFixesValue"] and before["velocityFixesValue"]
    constrain_pressure_noop = (
        not before["fixedFluxPressureEligible"]
        and not after["fixedFluxPressureEligible"]
        and abs(gradient_change) <= 1.0e-15
    )
    incompatible_contribution_identified = simultaneous_fixed_values and constrain_pressure_noop and abs(predictor_flux_mismatch) > 1.0e-12
    return {
        "schema": "flowlab.open-boundary-outlet-constrain-pressure-audit.v1",
        "status": "audited",
        "case": str(case),
        "time": time_name,
        "method": (
            "The unchanged first-step outlet is recorded immediately before and after the live "
            "OpenFOAM constrainPressure call. No equation, field, source, tolerance, mesh, or boundary "
            "condition is changed by this audit."
        ),
        "before": before,
        "after": after,
        "comparison": {
            "predictorMinusImposedVelocityFlux": predictor_flux_mismatch,
            "actualPressureGradientChange": gradient_change,
            "simultaneousFixedPressureAndVelocity": simultaneous_fixed_values,
            "constrainPressureWasNoOp": constrain_pressure_noop,
            "incompatibleContributionIdentified": incompatible_contribution_identified,
        },
        "conclusion": {
            "finding": (
                "The v10 outlet fixes both pressure and velocity. Its pressure patch is not "
                "fixedFluxPressure, so OpenFOAM constrainPressure cannot update its normal gradient "
                "to reconcile phiHbyA with the imposed outlet velocity flux."
            ),
            "singleChangeJustified": incompatible_contribution_identified,
            "candidateChange": (
                "Change only outlet U from fixedValue to pressureInletOutletVelocity while retaining "
                "the fixed outlet pressure, then rerun the coarse exact-init gate."
            ),
            "notAuthorized": (
                "Do not change the source, pressure boundary, mesh, schemes, tolerances, or iteration count."
            ),
        },
        "rawLog": {"path": str(log), "sha256": _sha(log)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--trace-case", type=Path)
    parser.add_argument("--boundary-trace-case", type=Path)
    parser.add_argument("--exact-matrix-trace-case", type=Path)
    parser.add_argument("--exact-ueqn-trace-case", type=Path)
    parser.add_argument("--ux-linear-path-trace-case", type=Path)
    parser.add_argument("--pressure-presolve-trace-case", type=Path)
    parser.add_argument("--outlet-constrain-pressure-trace-case", type=Path)
    args = parser.parse_args()
    if sum(bool(value) for value in (args.run_root, args.trace_case, args.boundary_trace_case, args.exact_matrix_trace_case, args.exact_ueqn_trace_case, args.ux_linear_path_trace_case, args.pressure_presolve_trace_case, args.outlet_constrain_pressure_trace_case)) != 1:
        parser.error("provide exactly one audit target")
    if args.run_root:
        data, output = audit(args.run_root.resolve()), args.run_root / "artifacts/operator-audit.json"
    elif args.trace_case:
        data, output = trace_flux_audit(args.trace_case.resolve()), args.trace_case / "trace-flux-audit.json"
    elif args.boundary_trace_case:
        data, output = boundary_coupling_audit(args.boundary_trace_case.resolve()), args.boundary_trace_case / "boundary-coupling-audit.json"
    elif args.exact_matrix_trace_case:
        data, output = exact_boundary_matrix_audit(args.exact_matrix_trace_case.resolve()), args.exact_matrix_trace_case / "exact-pressure-matrix-audit.json"
    elif args.exact_ueqn_trace_case:
        data, output = exact_ueqn_audit(args.exact_ueqn_trace_case.resolve()), args.exact_ueqn_trace_case / "exact-ueqn-audit.json"
    elif args.ux_linear_path_trace_case:
        data, output = ux_linear_path_audit(args.ux_linear_path_trace_case.resolve()), args.ux_linear_path_trace_case / "ux-linear-path-audit.json"
    elif args.pressure_presolve_trace_case:
        data, output = pressure_presolve_audit(args.pressure_presolve_trace_case.resolve()), args.pressure_presolve_trace_case / "pressure-presolve-audit.json"
    else:
        data, output = outlet_constrain_pressure_audit(args.outlet_constrain_pressure_trace_case.resolve()), args.outlet_constrain_pressure_trace_case / "outlet-constrain-pressure-audit.json"
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
