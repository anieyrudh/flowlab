"""Run the one-iteration OpenFOAM exact-state probe for affine MMS v2."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

from .cad_parabolic_smoke import (
    _nonuniform_scalar_field,
    _nonuniform_vector_field,
    _read_cell_centres,
)
from .open_boundary_mms_redesign import AffineCrossflowMms, preflight
from .open_boundary_mms_runner import _header, _l2, _run, _table_value, _values, _write


SCHEMA = "flowlab.open-boundary-affine-mms-one-iteration-probe.v1"
N = 12
ITERATIONS = 1
FIELD_ERROR_LIMIT = 1.0e-8
MASS_LIMIT = 1.0e-8
LINEAR_RESIDUAL_LIMIT = 1.0e-8


def _case_files(n: int, spec: AffineCrossflowMms) -> dict[str, str]:
    inlet_values = [
        spec.velocity(0.0, (j + 0.5) / n, (k + 0.5) / n)
        for k in range(n)
        for j in range(n)
    ]
    outlet_values = [
        spec.velocity(1.0, (j + 0.5) / n, (k + 0.5) / n)
        for k in range(n)
        for j in range(n)
    ]
    block = _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1));
blocks (hex (0 1 2 3 4 5 6 7) ({n} {n} {n}) simpleGrading (1 1 1)); edges ();
boundary (
 inlet {{ type patch; faces ((0 4 7 3)); }} outlet {{ type patch; faces ((1 2 6 5)); }}
 yMin {{ type patch; faces ((0 1 5 4)); }} yMax {{ type patch; faces ((3 7 6 2)); }}
 zMin {{ type symmetryPlane; faces ((0 3 2 1)); }} zMax {{ type symmetryPlane; faces ((4 5 6 7)); }} ); mergePatchPairs ();
"""
    u = _header("0", "U", "volVectorField") + f"""dimensions [0 1 -1 0 0 0 0];
internalField uniform ({spec.base_velocity_m_s:.17g} {spec.crossflow_velocity_m_s:.17g} 0);
boundaryField {{
 inlet {{ type fixedValue; value {_nonuniform_vector_field(inlet_values)}; }}
 outlet {{ type pressureInletOutletVelocity; value {_nonuniform_vector_field(outlet_values)}; }}
 yMin {{ type fixedValue; value uniform ({spec.base_velocity_m_s:.17g} {spec.crossflow_velocity_m_s:.17g} 0); }}
 yMax {{ type fixedGradient; gradient uniform ({spec.shear_rate_per_s:.17g} 0 0); }}
 zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }}
}}
"""
    p = _header("0", "p", "volScalarField") + f"""dimensions [0 2 -2 0 0 0 0];
internalField uniform 0;
boundaryField {{
 inlet {{ type fixedGradient; gradient uniform {spec.pressure_gradient_m2_s2_per_m:.17g}; }}
 outlet {{ type fixedValue; value uniform 0; }}
 yMin {{ type fixedGradient; gradient uniform 0; }}
 yMax {{ type fixedGradient; gradient uniform 0; }}
 zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }}
}}
"""
    source = spec.momentum_source()
    fv_models = _header("constant", "fvModels") + f"""mmsSource {{
 type semiImplicitSource; select all; volumeMode specific;
 sources {{ U {{ explicit ({source[0]:.17g} {source[1]:.17g} {source[2]:.17g}); implicit 0; }} }}
}}
"""
    functions = " ".join(
        f"{patch}Flux {{ type surfaceFieldValue; libs (\"libfieldFunctionObjects.so\"); regionType patch; name {patch}; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval 1; }}"
        for patch in ("inlet", "outlet", "yMin", "yMax")
    )
    control = _header("system", "controlDict") + f"""application foamRun;
startFrom startTime; startTime 0; stopAt endTime; endTime {ITERATIONS}; deltaT 1;
writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 16;
runTimeModifiable false;
functions {{
 residuals {{ type residuals; libs (\"libutilityFunctionObjects.so\"); fields (U p); writeControl timeStep; writeInterval 1; }}
 {functions}
}}
"""
    schemes = _header("system", "fvSchemes") + "ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } divSchemes { default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; }\n"
    solution = _header("system", "fvSolution") + "solvers { p { solver GAMG; smoother GaussSeidel; tolerance 1e-10; relTol 0; } pFinal { $p; relTol 0; } U { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-10; relTol 0; } UFinal { $U; relTol 0; } } SIMPLE { nNonOrthogonalCorrectors 0; consistent yes; residualControl { p 1e-8; U 1e-8; } } relaxationFactors { equations { U 1; } }\n"
    return {
        "system/blockMeshDict": block,
        "0/U": u,
        "0/p": p,
        "constant/fvModels": fv_models,
        "constant/physicalProperties": _header("constant", "physicalProperties")
        + f"viscosityModel constant; nu [0 2 -1 0 0 0 0] {spec.viscosity_m2_s:.17g};\n",
        "constant/momentumTransport": _header("constant", "momentumTransport")
        + "simulationType laminar;\n",
        "system/controlDict": control,
        "system/fvSchemes": schemes,
        "system/fvSolution": solution,
        "mms-definition.json": json.dumps(spec.manifest(), indent=2, sort_keys=True)
        + "\n",
    }


def _init_exact(case: Path, spec: AffineCrossflowMms) -> None:
    centres = _read_cell_centres(case / "0/C")
    velocity = [spec.velocity(*point) for point in centres]
    pressure = [spec.pressure(*point) for point in centres]
    _write(
        case / "0/U",
        (case / "0/U")
        .read_text(encoding="utf-8")
        .replace(
            f"internalField uniform ({spec.base_velocity_m_s:.17g} {spec.crossflow_velocity_m_s:.17g} 0);",
            "internalField " + _nonuniform_vector_field(velocity) + ";",
            1,
        ),
    )
    _write(
        case / "0/p",
        (case / "0/p")
        .read_text(encoding="utf-8")
        .replace(
            "internalField uniform 0;",
            "internalField " + _nonuniform_scalar_field(pressure) + ";",
            1,
        ),
    )
    _write(
        case / "exact-init.json",
        json.dumps(
            {
                "schema": "flowlab.affine-crossflow-exact-init.v1",
                "method": "OpenFOAM writeCellCentres followed by analytic internal fields",
                "cellCount": len(centres),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _flux_balance(case: Path) -> dict[str, Any]:
    fluxes = {
        patch: _table_value(case, f"{patch}Flux")
        for patch in ("inlet", "outlet", "yMin", "yMax")
    }
    net = sum(fluxes.values())
    inflow = sum(-value for value in fluxes.values() if value < 0.0)
    return {
        "patches": fluxes,
        "net": net,
        "relativeImbalance": abs(net) / max(inflow, 1.0e-30),
    }


def run_probe(output: Path) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"refusing to overwrite {output}")
    offline = preflight()
    if offline["status"] != "authorized":
        raise ValueError("offline affine MMS preflight did not authorize OpenFOAM")
    spec = AffineCrossflowMms()
    case = output / "case"
    artifacts = output / "artifacts"
    for name, content in _case_files(N, spec).items():
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
        _init_exact(case, spec)
    solver = (
        _run(
            ["foamRun", "-solver", "incompressibleFluid"],
            case,
            artifacts / "foamRun.log",
        )
        if centres == 0
        else 127
    )
    try:
        cell_centres = _read_cell_centres(case / "0/C")
        actual_u = _values(case / "1/U", True)
        actual_p = _values(case / "1/p", False)
        exact_u = [spec.velocity(*point) for point in cell_centres]
        exact_p = [(spec.pressure(*point),) for point in cell_centres]
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
    final_linear_residual = max(residuals[-4:]) if residuals else math.inf
    flux = _flux_balance(case)
    checks = {
        "blockMesh": block == 0,
        "checkMesh": check == 0
        and "Mesh OK" in (artifacts / "checkMesh.log").read_text(
            encoding="utf-8", errors="replace"
        ),
        "solverCompleted": solver == 0,
        "velocityExactStateRetained": velocity_error <= FIELD_ERROR_LIMIT,
        "pressureExactStateRetained": pressure_error <= FIELD_ERROR_LIMIT,
        "massBalance": flux["relativeImbalance"] <= MASS_LIMIT,
        "finalLinearResidual": final_linear_residual <= LINEAR_RESIDUAL_LIMIT,
    }
    passed = all(checks.values())
    report = {
        "schema": SCHEMA,
        "status": "authorized" if passed else "blocked",
        "scientificStatus": "one-iteration-exact-state-probe",
        "validated": False,
        "definition": spec.manifest(),
        "offlinePreflight": {
            "schema": offline["schema"],
            "status": offline["status"],
            "checks": offline["checks"],
        },
        "execution": {
            "mesh": f"{N}^3",
            "iterations": ITERATIONS,
            "solverExitCode": solver,
        },
        "observation": {
            "velocityRelativeL2Error": velocity_error,
            "pressureRelativeL2Error": pressure_error,
            "mass": flux,
            "finalLinearResidual": final_linear_residual,
        },
        "limits": {
            "fieldRelativeL2Error": FIELD_ERROR_LIMIT,
            "massRelativeImbalance": MASS_LIMIT,
            "finalLinearResidual": LINEAR_RESIDUAL_LIMIT,
        },
        "checks": checks,
        "failedChecks": [name for name, value in checks.items() if not value],
        "nextStage": {
            "coarseValidation": "authorized" if passed else "blocked",
            "threeGridValidation": "blocked pending coarse validation",
            "coarseValidationExecuted": False,
        },
        "artifacts": {
            "case": str(case),
            "blockMeshLog": str(artifacts / "blockMesh.log"),
            "checkMeshLog": str(artifacts / "checkMesh.log"),
            "solverLog": str(artifacts / "foamRun.log"),
        },
    }
    _write(
        artifacts / "affine-mms-one-iteration-probe.json",
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
