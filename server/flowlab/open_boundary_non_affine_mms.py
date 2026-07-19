"""Three-grid, boundary-compatible non-affine MMS for OpenFOAM 11.

The exact fields are

    U = (U0 + A sin(pi y) sin(pi z), 0, 0)
    p = G(1-x) + P sin(pi x) sin(pi y) sin(pi z)

They are divergence free, have zero normal velocity gradient on the pressure
inlet/outlet, and reduce to exact fixed traces on every Dirichlet boundary.
The spatially varying coded source closes the steady incompressible momentum
equation.  Unlike the affine regression, these fields exercise interpolation,
gradient, and diffusion truncation error and therefore support observed-order
and GCI evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Tuple

from .cad_parabolic_smoke import (
    _nonuniform_scalar_field,
    _nonuniform_vector_field,
    _read_cell_centres,
)
from .open_boundary_affine_grid_invariance import SCHEMA as AFFINE_SCHEMA
from .open_boundary_affine_flux_pressure_probe import _boundary_vectors
from .open_boundary_mms_runner import (
    _flux_imbalance,
    _header,
    _l2,
    _run,
    _values,
    _write,
)


SCHEMA = "flowlab.open-boundary-non-affine-mms.v1"
ARTIFACT = "non-affine-mms-report.json"
LEVELS = (("coarse", 12), ("medium", 24), ("fine", 48))
ITERATIONS = 1000
LINEAR_SOLVER_TOLERANCE = 1.0e-12
MASS_LIMIT = 1.0e-8
LINEAR_RESIDUAL_LIMIT = 1.0e-10
NONLINEAR_RESIDUAL_LIMIT = 1.0e-8
MINIMUM_OBSERVED_ORDER = 1.5
MAXIMUM_ORDER_SPREAD = 0.75
FINE_GCI_LIMIT = 0.01
SAFETY_FACTOR = 1.25
PATCHES = ("inlet", "outlet", "yMin", "yMax", "zMin", "zMax")


Vector = Tuple[float, float, float]


@dataclass(frozen=True)
class NonAffineMms:
    base_velocity_m_s: float = 1.0
    velocity_amplitude_m_s: float = 0.2
    pressure_drop_m2_s2: float = 0.02
    pressure_amplitude_m2_s2: float = 0.01
    viscosity_m2_s: float = 0.01
    domain: tuple[float, float, float] = (1.0, 1.0, 1.0)

    def velocity(self, x: float, y: float, z: float) -> Vector:
        del x
        return (
            self.base_velocity_m_s
            + self.velocity_amplitude_m_s
            * math.sin(math.pi * y)
            * math.sin(math.pi * z),
            0.0,
            0.0,
        )

    def pressure(self, x: float, y: float, z: float) -> float:
        return self.pressure_drop_m2_s2 * (1.0 - x) + (
            self.pressure_amplitude_m2_s2
            * math.sin(math.pi * x)
            * math.sin(math.pi * y)
            * math.sin(math.pi * z)
        )

    def pressure_gradient(self, x: float, y: float, z: float) -> Vector:
        amplitude = self.pressure_amplitude_m2_s2 * math.pi
        return (
            -self.pressure_drop_m2_s2
            + amplitude
            * math.cos(math.pi * x)
            * math.sin(math.pi * y)
            * math.sin(math.pi * z),
            amplitude
            * math.sin(math.pi * x)
            * math.cos(math.pi * y)
            * math.sin(math.pi * z),
            amplitude
            * math.sin(math.pi * x)
            * math.sin(math.pi * y)
            * math.cos(math.pi * z),
        )

    def momentum_source(self, x: float, y: float, z: float) -> Vector:
        gradient = self.pressure_gradient(x, y, z)
        diffusion = (
            2.0
            * self.viscosity_m2_s
            * self.velocity_amplitude_m_s
            * math.pi**2
            * math.sin(math.pi * y)
            * math.sin(math.pi * z)
        )
        return (gradient[0] + diffusion, gradient[1], gradient[2])

    def outward_pressure_gradient(
        self, patch: str, x: float, y: float, z: float
    ) -> float:
        gradient = self.pressure_gradient(x, y, z)
        normals: dict[str, Vector] = {
            "inlet": (-1.0, 0.0, 0.0),
            "outlet": (1.0, 0.0, 0.0),
            "yMin": (0.0, -1.0, 0.0),
            "yMax": (0.0, 1.0, 0.0),
            "zMin": (0.0, 0.0, -1.0),
            "zMax": (0.0, 0.0, 1.0),
        }
        normal = normals[patch]
        return sum(a * b for a, b in zip(gradient, normal))

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "flowlab.non-affine-open-boundary-mms-definition.v1",
            "strongForm": (
                "div(U tensor U) - div(nu dev2(T(grad(U)))) + grad(p) = S"
            ),
            "velocity": "(U0 + A sin(pi y) sin(pi z), 0, 0)",
            "kinematicPressure": (
                "G(1-x) + P sin(pi x) sin(pi y) sin(pi z)"
            ),
            "momentumSource": (
                "grad(p) + (2 nu A pi^2 sin(pi y) sin(pi z), 0, 0)"
            ),
            "parameters": asdict(self),
            "boundaryCompatibility": {
                "inlet/outlet": (
                    "fixed exact pressure traces; pressureInletOutletVelocity; "
                    "analytic dU/dx=0"
                ),
                "y/z": (
                    "exact fixed velocity traces; fixedFluxPressure initialized "
                    "with analytic values and outward-normal gradients"
                ),
            },
        }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorized_upstream(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("schema") != AFFINE_SCHEMA:
        raise ValueError("upstream report is not the affine grid-invariance gate")
    if report.get("status") != "accepted":
        raise ValueError("upstream affine grid-invariance gate is not accepted")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError("upstream affine report does not pass every check")
    if report.get("nextStage", {}).get("nonAffineMms") != "authorized":
        raise ValueError("upstream affine report does not authorize non-affine MMS")
    return report


def _source_dictionary(spec: NonAffineMms) -> str:
    nu = spec.viscosity_m2_s
    amplitude = spec.velocity_amplitude_m_s
    drop = spec.pressure_drop_m2_s2
    pressure = spec.pressure_amplitude_m2_s2
    return _header("constant", "fvModels") + f"""mmsSource
{{
 type coded;
 select all;
 field U;
 codeAddSup
 #{{
  const vectorField& c = mesh().C();
  const scalarField& v = mesh().V();
  vectorField& s = eqn.source();
  const scalar pi = constant::mathematical::pi;
  forAll(c, i)
  {{
   const scalar sx =
       2*{nu:.17g}*{amplitude:.17g}*sqr(pi)*sin(pi*c[i].y())*sin(pi*c[i].z())
     - {drop:.17g}
     + {pressure:.17g}*pi*cos(pi*c[i].x())*sin(pi*c[i].y())*sin(pi*c[i].z());
   const scalar sy =
       {pressure:.17g}*pi*sin(pi*c[i].x())*cos(pi*c[i].y())*sin(pi*c[i].z());
   const scalar sz =
       {pressure:.17g}*pi*sin(pi*c[i].x())*sin(pi*c[i].y())*cos(pi*c[i].z());
   // fvMatrix stores this coded explicit contribution with the opposite
   // algebraic sign from semiImplicitSource's user-facing explicit value.
   s[i] -= vector(sx, sy, sz)*v[i];
  }}
 #}};
}}
"""


def _case_files(n: int, spec: NonAffineMms) -> dict[str, str]:
    block = _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1));
blocks (hex (0 1 2 3 4 5 6 7) ({n} {n} {n}) simpleGrading (1 1 1));
edges ();
boundary (
 inlet {{ type patch; faces ((0 4 7 3)); }}
 outlet {{ type patch; faces ((1 2 6 5)); }}
 yMin {{ type patch; faces ((0 1 5 4)); }}
 yMax {{ type patch; faces ((3 7 6 2)); }}
 zMin {{ type patch; faces ((0 3 2 1)); }}
 zMax {{ type patch; faces ((4 5 6 7)); }}
);
mergePatchPairs ();
"""
    u = _header("0", "U", "volVectorField") + f"""dimensions [0 1 -1 0 0 0 0];
internalField uniform ({spec.base_velocity_m_s:.17g} 0 0);
boundaryField {{
 inlet {{ type pressureInletOutletVelocity; phi phi; tangentialVelocity uniform (0 0 0); value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
 outlet {{ type pressureInletOutletVelocity; phi phi; tangentialVelocity uniform (0 0 0); value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
 yMin {{ type fixedValue; value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
 yMax {{ type fixedValue; value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
 zMin {{ type fixedValue; value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
 zMax {{ type fixedValue; value uniform ({spec.base_velocity_m_s:.17g} 0 0); }}
}}
"""
    p = _header("0", "p", "volScalarField") + f"""dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField {{
 inlet {{ type fixedValue; value uniform {spec.pressure_drop_m2_s2:.17g}; }}
 outlet {{ type fixedValue; value uniform 0; }}
 yMin {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 yMax {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 zMin {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
 zMax {{ type fixedFluxPressure; value uniform 0; gradient uniform 0; }}
}}
"""
    control = _header("system", "controlDict") + f"""application foamRun;
startFrom startTime; startTime 0; stopAt endTime; endTime {ITERATIONS}; deltaT 1;
writeControl timeStep; writeInterval {ITERATIONS}; writeFormat ascii; writePrecision 16;
runTimeModifiable false;
functions {{
 residuals {{ type residuals; libs ("libutilityFunctionObjects.so"); fields (U p); writeControl timeStep; writeInterval 1; }}
 inletFlux {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name inlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {ITERATIONS}; }}
 outletFlux {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); regionType patch; name outlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {ITERATIONS}; }}
}}
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
relaxationFactors {{ equations {{ U 1; }} }}
"""
    return {
        "system/blockMeshDict": block,
        "0/U": u,
        "0/p": p,
        "constant/fvModels": _source_dictionary(spec),
        "constant/physicalProperties": _header(
            "constant", "physicalProperties"
        )
        + (
            "viscosityModel constant; "
            f"nu [0 2 -1 0 0 0 0] {spec.viscosity_m2_s:.17g};\n"
        ),
        "constant/momentumTransport": _header(
            "constant", "momentumTransport"
        )
        + "simulationType laminar;\n",
        "system/controlDict": control,
        "system/fvSchemes": schemes,
        "system/fvSolution": solution,
        "mms-definition.json": json.dumps(
            spec.manifest(), indent=2, sort_keys=True
        )
        + "\n",
    }


def _initialize_exact(case: Path, spec: NonAffineMms) -> None:
    centres = _read_cell_centres(case / "0/C")
    patches = _boundary_vectors(case / "0/C", PATCHES)
    internal_u = [spec.velocity(*point) for point in centres]
    internal_p = [spec.pressure(*point) for point in centres]

    def u_patch(name: str) -> str:
        values = [spec.velocity(*point) for point in patches[name]]
        value = _nonuniform_vector_field(values)
        if name in ("inlet", "outlet"):
            return (
                f"{name} {{ type pressureInletOutletVelocity; phi phi; "
                f"tangentialVelocity uniform (0 0 0); value {value}; }}"
            )
        return f"{name} {{ type fixedValue; value {value}; }}"

    def p_patch(name: str) -> str:
        values = [spec.pressure(*point) for point in patches[name]]
        value = _nonuniform_scalar_field(values)
        if name in ("inlet", "outlet"):
            return f"{name} {{ type fixedValue; value {value}; }}"
        gradients = [
            spec.outward_pressure_gradient(name, *point)
            for point in patches[name]
        ]
        gradient = _nonuniform_scalar_field(gradients)
        return (
            f"{name} {{ type fixedFluxPressure; value {value}; "
            f"gradient {gradient}; }}"
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
    _write(
        case / "exact-init.json",
        json.dumps(
            {
                "method": "analytic cell and boundary values from writeCellCentres",
                "cellCount": len(centres),
                "pressureFluxGradients": "analytic outward-normal values",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _observe(root: Path, label: str, n: int, spec: NonAffineMms) -> dict[str, Any]:
    case = root / label / "case"
    artifacts = root / label / "artifacts"
    for name, content in _case_files(n, spec).items():
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
    centres_status = (
        _run(
            ["foamPostProcess", "-func", "writeCellCentres", "-time", "0"],
            case,
            artifacts / "writeCellCentres.log",
        )
        if check == 0
        else 127
    )
    if centres_status == 0:
        _initialize_exact(case, spec)
    solver = (
        _run(
            ["foamRun", "-solver", "incompressibleFluid"],
            case,
            artifacts / "foamRun.log",
        )
        if centres_status == 0
        else 127
    )
    try:
        centres = _read_cell_centres(case / "0/C")
        actual_u = _values(case / str(ITERATIONS) / "U", True)
        actual_p = _values(case / str(ITERATIONS) / "p", False)
        exact_u = [spec.velocity(*point) for point in centres]
        exact_p = [(spec.pressure(*point),) for point in centres]
        velocity_error = _l2(actual_u, exact_u)
        pressure_error = _l2(actual_p, exact_p)
    except (OSError, ValueError, StopIteration, ZeroDivisionError):
        velocity_error = pressure_error = math.inf
    solver_log = (artifacts / "foamRun.log").read_text(
        encoding="utf-8", errors="replace"
    )
    residuals = [
        float(value)
        for value in re.findall(r"Final residual = ([0-9.eE+-]+)", solver_log)
    ]
    initial_residuals = [
        float(value)
        for value in re.findall(r"Initial residual = ([0-9.eE+-]+)", solver_log)
    ]
    mesh_ok = block == 0 and check == 0 and "Mesh OK" in (
        artifacts / "checkMesh.log"
    ).read_text(encoding="utf-8", errors="replace")
    observation = {
        "level": label,
        "cellsPerAxis": n,
        "effectiveSpacing": 1.0 / n,
        "checkMeshPassed": mesh_ok,
        "solverExitCode": solver,
        "velocityRelativeL2Error": velocity_error,
        "pressureRelativeL2Error": pressure_error,
        "massRelativeImbalance": _flux_imbalance(case),
        "finalLinearResidual": max(residuals[-4:]) if residuals else math.inf,
        "finalNonlinearResidual": (
            max(initial_residuals[-4:]) if initial_residuals else math.inf
        ),
        "artifacts": {
            "case": str(case),
            "blockMesh": str(artifacts / "blockMesh.log"),
            "checkMesh": str(artifacts / "checkMesh.log"),
            "solver": str(artifacts / "foamRun.log"),
            "exactInit": str(case / "exact-init.json"),
        },
    }
    _write(
        artifacts / "observation.json",
        json.dumps(observation, indent=2, sort_keys=True) + "\n",
    )
    return observation


def _order(errors: list[float]) -> dict[str, float]:
    coarse, medium, fine = errors
    if not (
        all(math.isfinite(value) and value > 0.0 for value in errors)
        and coarse > medium > fine
    ):
        return {"coarseToMedium": math.nan, "mediumToFine": math.nan}
    return {
        "coarseToMedium": math.log(coarse / medium) / math.log(2.0),
        "mediumToFine": math.log(medium / fine) / math.log(2.0),
    }


def _gci(errors: list[float], observed_order: float) -> dict[str, float]:
    if not math.isfinite(observed_order) or observed_order <= 0.0:
        return {"medium": math.inf, "fine": math.inf, "asymptoticRatio": math.inf}
    denominator = 2.0**observed_order - 1.0
    medium = SAFETY_FACTOR * abs(errors[0] - errors[1]) / denominator
    fine = SAFETY_FACTOR * abs(errors[1] - errors[2]) / denominator
    ratio = medium / max((2.0**observed_order) * fine, 1.0e-300)
    return {"medium": medium, "fine": fine, "asymptoticRatio": ratio}


def _convergence(errors: list[float]) -> dict[str, Any]:
    orders = _order(errors)
    fine_order = orders["mediumToFine"]
    return {
        "relativeL2Errors": errors,
        "observedOrder": orders,
        "orderSpread": abs(orders["coarseToMedium"] - fine_order),
        "gciRelativeToAnalyticFieldNorm": _gci(errors, fine_order),
    }


def run(output: Path, affine_report: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    upstream = _authorized_upstream(affine_report)
    spec = NonAffineMms()
    observations = [
        _observe(output, label, n, spec) for label, n in LEVELS
    ]
    velocity = _convergence(
        [item["velocityRelativeL2Error"] for item in observations]
    )
    pressure = _convergence(
        [item["pressureRelativeL2Error"] for item in observations]
    )

    def convergence_checks(name: str, result: dict[str, Any]) -> dict[str, bool]:
        orders = result["observedOrder"]
        return {
            f"{name}ErrorsMonotonicallyDecrease": all(
                math.isfinite(value) and value > 0.0
                for value in result["relativeL2Errors"]
            )
            and result["relativeL2Errors"][0]
            > result["relativeL2Errors"][1]
            > result["relativeL2Errors"][2],
            f"{name}ObservedOrder": min(orders.values())
            >= MINIMUM_OBSERVED_ORDER,
            f"{name}OrderConsistency": result["orderSpread"]
            <= MAXIMUM_ORDER_SPREAD,
            f"{name}FineGci": result["gciRelativeToAnalyticFieldNorm"]["fine"]
            <= FINE_GCI_LIMIT,
        }

    checks: dict[str, bool] = {
        "affineGridInvarianceAccepted": True,
        "allMeshesPass": all(item["checkMeshPassed"] for item in observations),
        "allSolversComplete": all(
            item["solverExitCode"] == 0 for item in observations
        ),
        "allMassBalancesPass": all(
            item["massRelativeImbalance"] <= MASS_LIMIT
            for item in observations
        ),
        "allFinalLinearResidualsPass": all(
            item["finalLinearResidual"] <= LINEAR_RESIDUAL_LIMIT
            for item in observations
        ),
        "allFinalNonlinearResidualsPass": all(
            item["finalNonlinearResidual"] <= NONLINEAR_RESIDUAL_LIMIT
            for item in observations
        ),
    }
    checks.update(convergence_checks("velocity", velocity))
    checks.update(convergence_checks("pressure", pressure))
    passed = all(checks.values())
    report = {
        "schema": SCHEMA,
        "status": "accepted" if passed else "rejected",
        "scientificStatus": "formal-non-affine-three-grid-mms",
        "validated": False,
        "definition": spec.manifest(),
        "execution": {
            "levels": [n for _, n in LEVELS],
            "iterationsPerLevel": ITERATIONS,
            "solver": "OpenFOAM 11 incompressibleFluid/PBiCGStab/DILU",
            "linearSolverTolerance": LINEAR_SOLVER_TOLERANCE,
        },
        "observations": observations,
        "convergence": {"velocity": velocity, "pressure": pressure},
        "limits": {
            "massRelativeImbalance": MASS_LIMIT,
            "finalLinearResidual": LINEAR_RESIDUAL_LIMIT,
            "finalNonlinearResidual": NONLINEAR_RESIDUAL_LIMIT,
            "minimumObservedOrder": MINIMUM_OBSERVED_ORDER,
            "maximumOrderSpread": MAXIMUM_ORDER_SPREAD,
            "fineGciRelativeToAnalyticFieldNorm": FINE_GCI_LIMIT,
            "gciSafetyFactor": SAFETY_FACTOR,
        },
        "checks": checks,
        "failedChecks": [name for name, value in checks.items() if not value],
        "upstreamGate": {
            "path": str(affine_report),
            "sha256": _sha(affine_report),
            "schema": upstream["schema"],
            "status": upstream["status"],
            "allChecksPassed": True,
        },
        "nextStage": {
            "independentLaminarPhysicalBenchmark": (
                "authorized" if passed else "blocked"
            ),
            "executed": False,
        },
    }
    _write(
        output / "artifacts" / ARTIFACT,
        json.dumps(report, indent=2, sort_keys=True, allow_nan=True) + "\n",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--affine-report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.output.resolve(), args.affine_report.resolve())
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
