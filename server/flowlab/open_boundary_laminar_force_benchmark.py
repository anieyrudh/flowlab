"""Independent plane-Poiseuille force validation for the open-boundary regime."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Tuple

from .cad_parabolic_smoke import (
    _nonuniform_scalar_field,
    _nonuniform_vector_field,
    _read_cell_centres,
)
from .open_boundary_affine_flux_pressure_probe import _boundary_vectors
from .open_boundary_mms_runner import (
    _flux_imbalance,
    _header,
    _l2,
    _run,
    _values,
    _write,
)
from .open_boundary_non_affine_mms import SCHEMA as MMS_SCHEMA


SCHEMA = "flowlab.open-boundary-laminar-force-benchmark.v1"
ARTIFACT = "laminar-force-benchmark.json"
LEVELS = (("coarse", 12), ("medium", 24), ("fine", 48))
PATCHES = ("inlet", "outlet", "yMin", "yMax", "zMin", "zMax")
INITIALIZED_PATCHES = ("inlet", "outlet", "yMin", "yMax")
ITERATIONS = 1000
LINEAR_SOLVER_TOLERANCE = 1.0e-12
MASS_LIMIT = 1.0e-8
LINEAR_RESIDUAL_LIMIT = 1.0e-10
AXIAL_NONLINEAR_RESIDUAL_LIMIT = 1.0e-6
PRESSURE_NONLINEAR_RESIDUAL_LIMIT = 1.0e-8
TRANSVERSE_VELOCITY_RELATIVE_LIMIT = 1.0e-5
FORCE_RECONCILIATION_ABSOLUTE_LIMIT = 1.0e-10
PRESSURE_FORCE_RELATIVE_LIMIT = 1.0e-8
COARSE_WALL_VISCOUS_RELATIVE_LIMIT = 0.06
FINE_WALL_VISCOUS_RELATIVE_LIMIT = 0.02
FINE_FACE_TRACTION_RELATIVE_LIMIT = 0.03
FINE_FIELD_RELATIVE_LIMIT = 0.02


Vector = Tuple[float, float, float]


@dataclass(frozen=True)
class PlanePoiseuille:
    pressure_drop_m2_s2: float = 0.02
    viscosity_m2_s: float = 0.01
    length_m: float = 1.0
    height_m: float = 1.0
    depth_m: float = 1.0

    @property
    def pressure_gradient_m_s2(self) -> float:
        return self.pressure_drop_m2_s2 / self.length_m

    @property
    def profile_coefficient(self) -> float:
        return self.pressure_gradient_m_s2 / (2.0 * self.viscosity_m2_s)

    @property
    def mean_velocity_m_s(self) -> float:
        return self.profile_coefficient * self.height_m**2 / 6.0

    @property
    def reynolds_number(self) -> float:
        return abs(self.mean_velocity_m_s) * self.height_m / self.viscosity_m2_s

    @property
    def signed_reynolds_number(self) -> float:
        return self.mean_velocity_m_s * self.height_m / self.viscosity_m2_s

    @property
    def analytic_open_pressure_force(self) -> Vector:
        return (
            -self.pressure_drop_m2_s2 * self.height_m * self.depth_m,
            0.0,
            0.0,
        )

    @property
    def analytic_wall_viscous_force(self) -> Vector:
        return (
            self.pressure_drop_m2_s2 * self.height_m * self.depth_m,
            0.0,
            0.0,
        )

    def velocity(self, x: float, y: float, z: float) -> Vector:
        del x, z
        return (self.profile_coefficient * y * (self.height_m - y), 0.0, 0.0)

    def pressure(self, x: float, y: float, z: float) -> float:
        del y, z
        return self.pressure_drop_m2_s2 * (1.0 - x / self.length_m)

    def analytic_traction(self, point: Vector, normal: Vector) -> Vector:
        _, y, _ = point
        du_dy = self.profile_coefficient * (self.height_m - 2.0 * y)
        shear = -self.viscosity_m2_s * du_dy
        return (normal[1] * shear, normal[0] * shear, 0.0)

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "flowlab.plane-poiseuille-definition.v1",
            "velocity": "(G/(2 nu) y(H-y), 0, 0)",
            "kinematicPressure": "G(1-x/L)",
            "parameters": asdict(self),
            "derived": {
                "meanVelocityMPerS": self.mean_velocity_m_s,
                "reynoldsNumberHeightBased": self.reynolds_number,
                "signedReynoldsNumberHeightBased": self.signed_reynolds_number,
                "pressureGradientMPerS2": self.pressure_gradient_m_s2,
                "analyticOpenPressureForce": list(self.analytic_open_pressure_force),
                "analyticWallViscousForce": list(self.analytic_wall_viscous_force),
            },
        }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorized_upstream(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != MMS_SCHEMA or report.get("status") != "accepted":
        raise ValueError("upstream non-affine MMS report is not accepted")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError("upstream non-affine MMS did not pass every check")
    if (
        report.get("nextStage", {}).get("independentLaminarPhysicalBenchmark")
        != "authorized"
    ):
        raise ValueError("upstream non-affine MMS does not authorize the benchmark")
    return report


def _mesh_shape(
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
) -> tuple[int, int, int]:
    if n <= 0:
        raise ValueError("cells per height must be positive")
    if axial_cell_aspect_ratio <= 0.0:
        raise ValueError("axial cell aspect ratio must be positive")
    transverse_size = spec.height_m / n
    nx = max(1, round(spec.length_m / (axial_cell_aspect_ratio * transverse_size)))
    nz = max(1, round(spec.depth_m / transverse_size))
    return nx, n, nz


def _case_files(
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
    iterations: int = ITERATIONS,
) -> dict[str, str]:
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    nx, ny, nz = _mesh_shape(n, spec, axial_cell_aspect_ratio)
    block = _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices ((0 0 0) ({spec.length_m:.17g} 0 0) ({spec.length_m:.17g} {spec.height_m:.17g} 0) (0 {spec.height_m:.17g} 0) (0 0 {spec.depth_m:.17g}) ({spec.length_m:.17g} 0 {spec.depth_m:.17g}) ({spec.length_m:.17g} {spec.height_m:.17g} {spec.depth_m:.17g}) (0 {spec.height_m:.17g} {spec.depth_m:.17g}));
blocks (hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)); edges ();
boundary (
 inlet {{ type patch; faces ((0 4 7 3)); }} outlet {{ type patch; faces ((1 2 6 5)); }}
 yMin {{ type wall; faces ((0 1 5 4)); }} yMax {{ type wall; faces ((3 7 6 2)); }}
 zMin {{ type symmetryPlane; faces ((0 3 2 1)); }} zMax {{ type symmetryPlane; faces ((4 5 6 7)); }}
); mergePatchPairs ();
"""
    u = _header("0", "U", "volVectorField") + """dimensions [0 1 -1 0 0 0 0];
internalField uniform (0 0 0);
boundaryField {
 inlet { type pressureInletOutletVelocity; phi phi; tangentialVelocity uniform (0 0 0); value uniform (0 0 0); }
 outlet { type pressureInletOutletVelocity; phi phi; tangentialVelocity uniform (0 0 0); value uniform (0 0 0); }
 yMin { type fixedValue; value uniform (0 0 0); }
 yMax { type fixedValue; value uniform (0 0 0); }
 zMin { type symmetryPlane; } zMax { type symmetryPlane; }
}
"""
    p = _header("0", "p", "volScalarField") + f"""dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField {{
 inlet {{ type fixedValue; value uniform {spec.pressure_drop_m2_s2:.17g}; }}
 outlet {{ type fixedValue; value uniform 0; }}
 yMin {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 yMax {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }}
}}
"""
    functions = f"""
 residuals {{ type residuals; libs ("libutilityFunctionObjects.so"); fields (U p); writeControl timeStep; writeInterval 1; }}
 forcesOpen {{ type forces; libs ("libforces.so"); patches (inlet outlet); CofR (0 0 0); rho rhoInf; rhoInf 1; writeControl timeStep; writeInterval {iterations}; }}
 forcesWalls {{ type forces; libs ("libforces.so"); patches (yMin yMax); CofR (0 0 0); rho rhoInf; rhoInf 1; writeControl timeStep; writeInterval {iterations}; }}
 inletFlux {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name inlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {iterations}; }}
 outletFlux {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name outlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {iterations}; }}
"""
    control = _header("system", "controlDict") + f"""application foamRun;
startFrom startTime; startTime 0; stopAt endTime; endTime {iterations}; deltaT 1;
writeControl timeStep; writeInterval {iterations}; writeFormat ascii; writePrecision 16;
runTimeModifiable false; functions {{ {functions} }}
"""
    schemes = _header("system", "fvSchemes") + """ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
"""
    solution = _header("system", "fvSolution") + f"""solvers {{
 p {{ solver GAMG; smoother GaussSeidel; tolerance {LINEAR_SOLVER_TOLERANCE:.17g}; relTol 0; }}
 pFinal {{ $p; relTol 0; }}
 U {{ solver PBiCGStab; preconditioner DILU; tolerance {LINEAR_SOLVER_TOLERANCE:.17g}; relTol 0; }}
 UFinal {{ $U; relTol 0; }}
}}
SIMPLE {{ nNonOrthogonalCorrectors 0; consistent yes; pRefCell 0; pRefValue 0; }}
relaxationFactors {{ equations {{ U 0.5; }} }}
"""
    return {
        "system/blockMeshDict": block,
        "0/U": u,
        "0/p": p,
        "constant/physicalProperties": _header("constant", "physicalProperties")
        + f"viscosityModel constant; nu [0 2 -1 0 0 0 0] {spec.viscosity_m2_s:.17g};\n",
        "constant/momentumTransport": _header("constant", "momentumTransport")
        + "simulationType laminar;\n",
        "system/controlDict": control,
        "system/fvSchemes": schemes,
        "system/fvSolution": solution,
        "benchmark-definition.json": json.dumps(spec.manifest(), indent=2, sort_keys=True) + "\n",
    }


def _initialize_exact(case: Path, spec: PlanePoiseuille) -> None:
    centres = _read_cell_centres(case / "0/C")
    patches = _boundary_vectors(case / "0/C", INITIALIZED_PATCHES)
    internal_u = [spec.velocity(*point) for point in centres]
    internal_p = [spec.pressure(*point) for point in centres]

    def u_patch(name: str) -> str:
        if name in ("zMin", "zMax"):
            return f"{name} {{ type symmetryPlane; }}"
        values = _nonuniform_vector_field(
            [spec.velocity(*point) for point in patches[name]]
        )
        if name in ("inlet", "outlet"):
            return (
                f"{name} {{ type pressureInletOutletVelocity; phi phi; "
                f"tangentialVelocity uniform (0 0 0); value {values}; }}"
            )
        return f"{name} {{ type fixedValue; value {values}; }}"

    def p_patch(name: str) -> str:
        if name in ("zMin", "zMax"):
            return f"{name} {{ type symmetryPlane; }}"
        values = _nonuniform_scalar_field(
            [spec.pressure(*point) for point in patches[name]]
        )
        if name in ("inlet", "outlet"):
            return f"{name} {{ type fixedValue; value {values}; }}"
        return (
            f"{name} {{ type fixedFluxPressure; value {values}; "
            "gradient uniform 0; }"
        )

    _write(
        case / "0/U",
        _header("0", "U", "volVectorField")
        + "dimensions [0 1 -1 0 0 0 0];\n"
        + f"internalField {_nonuniform_vector_field(internal_u)};\n"
        + "boundaryField {\n"
        + "\n".join(f" {u_patch(name)}" for name in PATCHES)
        + "\n}\n",
    )
    _write(
        case / "0/p",
        _header("0", "p", "volScalarField")
        + "dimensions [0 2 -2 0 0 0 0];\n"
        + f"internalField {_nonuniform_scalar_field(internal_p)};\n"
        + "boundaryField {\n"
        + "\n".join(f" {p_patch(name)}" for name in PATCHES)
        + "\n}\n",
    )


def _force_object(case: Path, name: str) -> dict[str, Vector]:
    paths = sorted((case / "postProcessing" / name).glob("**/force*.dat"))
    if not paths:
        raise ValueError(f"missing force output for {name}")
    rows = [
        line
        for line in paths[-1].read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    vectors = re.findall(
        r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)",
        rows[-1],
    )
    if len(vectors) < 2:
        raise ValueError(f"invalid force output for {name}")
    return {
        "pressure": tuple(float(value) for value in vectors[0]),
        "viscous": tuple(float(value) for value in vectors[1]),
    }


def _sum_vectors(values: Iterable[Vector]) -> Vector:
    rows = list(values)
    return tuple(sum(row[index] for row in rows) for index in range(3))  # type: ignore[return-value]


def _direct_audit(path: Path, spec: PlanePoiseuille) -> dict[str, Any]:
    groups = {
        "open": {"patches": {"inlet", "outlet"}},
        "walls": {"patches": {"yMin", "yMax"}},
        "all": {"patches": set(PATCHES)},
    }
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    wall_shear_scale = abs(spec.pressure_gradient_m_s2) * spec.height_m / 2.0
    pressure_scale = abs(spec.pressure_drop_m2_s2)
    max_traction_error = 0.0
    max_pressure_error = 0.0
    for row in rows:
        point = tuple(float(row[f"cf_{axis}"]) for axis in "xyz")
        normal = tuple(float(row[f"n_{axis}"]) for axis in "xyz")
        exact_traction = spec.analytic_traction(point, normal)
        actual_traction = tuple(float(row[f"traction_{axis}"]) for axis in "xyz")
        max_traction_error = max(
            max_traction_error,
            math.sqrt(sum((a - b) ** 2 for a, b in zip(actual_traction, exact_traction))),
        )
        max_pressure_error = max(
            max_pressure_error,
            abs(float(row["pressure"]) - spec.pressure(*point)),
        )
    result: dict[str, Any] = {
        "faceCount": len(rows),
        "maxFaceViscousTractionRelativeError": max_traction_error
        / max(wall_shear_scale, 1.0e-300),
        "maxFacePressureRelativeError": max_pressure_error
        / max(pressure_scale, 1.0e-300),
    }
    for name, definition in groups.items():
        selected = [row for row in rows if row["patch"] in definition["patches"]]
        result[name] = {
            "pressure": _sum_vectors(
                tuple(float(row[f"pressure_force_{axis}"]) for axis in "xyz")
                for row in selected
            ),
            "viscous": _sum_vectors(
                tuple(float(row[f"viscous_force_{axis}"]) for axis in "xyz")
                for row in selected
            ),
        }
    return result


def _vector_error(left: Vector, right: Vector) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _observe(
    root: Path,
    label: str,
    n: int,
    spec: PlanePoiseuille,
    axial_cell_aspect_ratio: float = 1.0,
    iterations: int = ITERATIONS,
) -> dict[str, Any]:
    case = root / label / "case"
    artifacts = root / label / "artifacts"
    mesh_shape = _mesh_shape(n, spec, axial_cell_aspect_ratio)
    for name, content in _case_files(
        n, spec, axial_cell_aspect_ratio, iterations
    ).items():
        _write(case / name, content)
    block = _run(["blockMesh"], case, artifacts / "blockMesh.log")
    check = _run(["checkMesh", "-allGeometry", "-allTopology"], case, artifacts / "checkMesh.log") if block == 0 else 127
    centres = _run(["foamPostProcess", "-func", "writeCellCentres", "-time", "0"], case, artifacts / "writeCellCentres.log") if check == 0 else 127
    if centres == 0:
        _initialize_exact(case, spec)
    solver = _run(["foamRun", "-solver", "incompressibleFluid"], case, artifacts / "foamRun.log") if centres == 0 else 127
    csv_path = artifacts / "face-traction.csv"
    utility = _run(["flowlabPatchTractionAudit", "-time", str(iterations), "-allFlowPatches", "-output", str(csv_path)], case, artifacts / "face-traction.log") if solver == 0 else 127
    try:
        cell_centres = _read_cell_centres(case / "0/C")
        actual_u = _values(case / str(iterations) / "U", True)
        actual_p = _values(case / str(iterations) / "p", False)
        exact_u = [spec.velocity(*point) for point in cell_centres]
        velocity_error = _l2(actual_u, exact_u)
        pressure_error = _l2(actual_p, [(spec.pressure(*point),) for point in cell_centres])
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
    solver_log = (artifacts / "foamRun.log").read_text(encoding="utf-8", errors="replace")
    final = [float(value) for value in re.findall(r"Final residual = ([0-9.eE+-]+)", solver_log)]
    def last_initial(field: str) -> float:
        values = re.findall(
            rf"Solving for {field}, Initial residual = ([0-9.eE+-]+)",
            solver_log,
        )
        return float(values[-1]) if values else math.inf
    analytic_open_pressure = spec.analytic_open_pressure_force
    analytic_wall_viscous = spec.analytic_wall_viscous_force
    scale = max(abs(analytic_open_pressure[0]), 1.0e-300)
    metrics = {
        "openForceObjectVsDirectAbsolute": max(
            (_vector_error(force_open[k], direct["open"][k]) for k in ("pressure", "viscous")),
            default=math.inf,
        ) if direct and force_open else math.inf,
        "wallForceObjectVsDirectAbsolute": max(
            (_vector_error(force_walls[k], direct["walls"][k]) for k in ("pressure", "viscous")),
            default=math.inf,
        ) if direct and force_walls else math.inf,
        "openPressureForceRelativeError": _vector_error(direct["open"]["pressure"], analytic_open_pressure) / scale if direct else math.inf,
        "wallViscousForceRelativeError": _vector_error(direct["walls"]["viscous"], analytic_wall_viscous) / scale if direct else math.inf,
        "openIntegratedViscousRelativeMagnitude": math.sqrt(sum(value * value for value in direct["open"]["viscous"])) / scale if direct else math.inf,
        "totalMomentumRelativeImbalance": _vector_error(
            _sum_vectors((direct["open"]["pressure"], direct["walls"]["viscous"])),
            (0.0, 0.0, 0.0),
        ) / scale if direct else math.inf,
    }
    observation = {
        "level": label,
        "cellsPerHeight": n,
        "cellsPerAxis": n if mesh_shape == (n, n, n) else None,
        "meshShape": list(mesh_shape),
        "axialCellAspectRatio": axial_cell_aspect_ratio,
        "effectiveSpacing": spec.height_m / n,
        "iterations": iterations,
        "checkMeshPassed": block == 0 and check == 0 and "Mesh OK" in (artifacts / "checkMesh.log").read_text(encoding="utf-8", errors="replace"),
        "solverExitCode": solver,
        "directAuditExitCode": utility,
        "velocityRelativeL2Error": velocity_error,
        "transverseVelocityRelativeL2Error": transverse_velocity_error,
        "pressureRelativeL2Error": pressure_error,
        "massRelativeImbalance": _flux_imbalance(case),
        "finalLinearResidual": max(final[-4:]) if final else math.inf,
        "finalAxialInitialResidual": last_initial("Ux"),
        "finalPressureInitialResidual": last_initial("p"),
        "forceComparison": metrics,
        "faceComparison": {
            "maxViscousTractionRelativeError": direct.get("maxFaceViscousTractionRelativeError", math.inf),
            "maxPressureRelativeError": direct.get("maxFacePressureRelativeError", math.inf),
        },
        "openFoamForces": {"open": force_open, "walls": force_walls},
        "directFaceIntegration": direct,
        "analytic": {
            "openPressureForce": analytic_open_pressure,
            "wallViscousForce": analytic_wall_viscous,
            "openIntegratedViscousForce": (0.0, 0.0, 0.0),
        },
        "artifacts": {"case": str(case), "solver": str(artifacts / "foamRun.log"), "faceCsv": str(csv_path), "faceAuditLog": str(artifacts / "face-traction.log")},
    }
    _write(artifacts / "observation.json", json.dumps(observation, indent=2, sort_keys=True) + "\n")
    return observation


def run(output: Path, mms_report: Path, utility_source: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    upstream = _authorized_upstream(mms_report)
    compile_status = _run(["wmake"], utility_source, output / "artifacts" / "wmake.log")
    spec = PlanePoiseuille()
    observations = [_observe(output, label, n, spec) for label, n in LEVELS] if compile_status == 0 else []
    fine = observations[-1] if observations else {}
    checks = {
        "nonAffineMmsAccepted": True,
        "tractionUtilityCompiled": compile_status == 0,
        "allMeshesPass": bool(observations) and all(item["checkMeshPassed"] for item in observations),
        "allSolversComplete": bool(observations) and all(item["solverExitCode"] == 0 for item in observations),
        "allDirectAuditsComplete": bool(observations) and all(item["directAuditExitCode"] == 0 for item in observations),
        "allMassBalancesPass": bool(observations) and all(item["massRelativeImbalance"] <= MASS_LIMIT for item in observations),
        "allLinearResidualsPass": bool(observations) and all(item["finalLinearResidual"] <= LINEAR_RESIDUAL_LIMIT for item in observations),
        "allAxialResidualsPass": bool(observations) and all(item["finalAxialInitialResidual"] <= AXIAL_NONLINEAR_RESIDUAL_LIMIT for item in observations),
        "allPressureResidualsPass": bool(observations) and all(item["finalPressureInitialResidual"] <= PRESSURE_NONLINEAR_RESIDUAL_LIMIT for item in observations),
        "allTransverseVelocityErrorsPass": bool(observations) and all(item["transverseVelocityRelativeL2Error"] <= TRANSVERSE_VELOCITY_RELATIVE_LIMIT for item in observations),
        "openFoamForcesMatchDirectIntegration": bool(observations) and all(max(item["forceComparison"]["openForceObjectVsDirectAbsolute"], item["forceComparison"]["wallForceObjectVsDirectAbsolute"]) <= FORCE_RECONCILIATION_ABSOLUTE_LIMIT for item in observations),
        "analyticPressureForceMatches": bool(observations) and all(item["forceComparison"]["openPressureForceRelativeError"] <= PRESSURE_FORCE_RELATIVE_LIMIT for item in observations),
        "coarseThroughFineAnalyticWallForce": bool(observations) and all(item["forceComparison"]["wallViscousForceRelativeError"] <= COARSE_WALL_VISCOUS_RELATIVE_LIMIT for item in observations),
        "fineAnalyticWallForce": bool(fine) and fine["forceComparison"]["wallViscousForceRelativeError"] <= FINE_WALL_VISCOUS_RELATIVE_LIMIT,
        "fineFaceViscousTraction": bool(fine) and fine["faceComparison"]["maxViscousTractionRelativeError"] <= FINE_FACE_TRACTION_RELATIVE_LIMIT,
        "fineFieldsMatchAnalytic": bool(fine) and max(fine["velocityRelativeL2Error"], fine["pressureRelativeL2Error"]) <= FINE_FIELD_RELATIVE_LIMIT,
        "fineMomentumBalance": bool(fine) and fine["forceComparison"]["totalMomentumRelativeImbalance"] <= FINE_WALL_VISCOUS_RELATIVE_LIMIT,
    }
    passed = all(checks.values())
    report = {
        "schema": SCHEMA,
        "status": "accepted" if passed else "rejected",
        "scientificStatus": "independent-analytic-laminar-force-validation",
        "validated": passed,
        "definition": spec.manifest(),
        "execution": {"levels": [n for _, n in LEVELS], "iterationsPerLevel": ITERATIONS, "solver": "OpenFOAM 11 incompressibleFluid", "directIntegration": "flowlabPatchTractionAudit"},
        "observations": observations,
        "limits": {
            "massRelativeImbalance": MASS_LIMIT,
            "linearResidual": LINEAR_RESIDUAL_LIMIT,
            "axialInitialResidual": AXIAL_NONLINEAR_RESIDUAL_LIMIT,
            "pressureInitialResidual": PRESSURE_NONLINEAR_RESIDUAL_LIMIT,
            "transverseVelocityRelativeL2": TRANSVERSE_VELOCITY_RELATIVE_LIMIT,
            "forceObjectVsDirectAbsolute": FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
            "analyticPressureForceRelative": PRESSURE_FORCE_RELATIVE_LIMIT,
            "coarseWallViscousForceRelative": COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
            "fineWallViscousForceRelative": FINE_WALL_VISCOUS_RELATIVE_LIMIT,
            "fineFaceViscousTractionRelative": FINE_FACE_TRACTION_RELATIVE_LIMIT,
        },
        "checks": checks,
        "failedChecks": [name for name, value in checks.items() if not value],
        "upstreamGate": {"path": str(mms_report), "sha256": _sha(mms_report), "schema": upstream["schema"], "status": upstream["status"], "allChecksPassed": True},
        "nextStage": {"desktopValidatedRegimePromotion": "authorized" if passed else "blocked", "executed": False},
        "artifacts": {"utilitySource": str(utility_source / "flowlabPatchTractionAudit.C"), "utilitySourceSha256": _sha(utility_source / "flowlabPatchTractionAudit.C"), "compileLog": str(output / "artifacts" / "wmake.log")},
    }
    _write(output / "artifacts" / ARTIFACT, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mms-report", type=Path, required=True)
    parser.add_argument("--utility-source", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output.resolve(), args.mms_report.resolve(), args.utility_source.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
