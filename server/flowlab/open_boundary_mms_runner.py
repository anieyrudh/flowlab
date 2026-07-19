"""Run the fixed structured all-hex, forced open-boundary MMS stage."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any, Literal

from .cad_parabolic_smoke import _nonuniform_scalar_field, _nonuniform_vector_field, _read_cell_centres
from .open_boundary_campaign import (
    LINEAR_RESIDUAL_LIMIT,
    MASS_LIMIT,
    MmsDefinition,
    RefinementObservation,
    evaluate_mms_stage,
)

LEVELS = (("coarse", 12), ("medium", 24), ("fine", 48))
ITERATIONS = 100
OutletVelocityType = Literal["fixedValue", "pressureInletOutletVelocity", "zeroGradient"]
OUTLET_VELOCITY_TYPES: tuple[OutletVelocityType, ...] = (
    "fixedValue",
    "pressureInletOutletVelocity",
    "zeroGradient",
)
USolverType = Literal["smoothSolver", "PBiCGStab"]
U_SOLVER_TYPES: tuple[USolverType, ...] = ("smoothSolver", "PBiCGStab")
InletPressureType = Literal["fixedValue", "fixedFluxPressure", "fixedGradient"]
INLET_PRESSURE_TYPES: tuple[InletPressureType, ...] = (
    "fixedValue",
    "fixedFluxPressure",
    "fixedGradient",
)
COARSE_ADVANCEMENT_SCHEMA = "flowlab.open-boundary-mms-coarse-advancement-gate.v1"
INLET_PRESSURE_ADVANCEMENT_SCHEMA = "flowlab.open-boundary-mms-inlet-pressure-advancement-gate.v1"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _header(location: str, name: str, cls: str = "dictionary") -> str:
    return f"FoamFile {{ version 2.0; format ascii; class {cls}; location \"{location}\"; object {name}; }}\n"


def _case_files(
    n: int,
    spec: MmsDefinition,
    *,
    outlet_velocity_type: OutletVelocityType = "fixedValue",
    inlet_pressure_type: InletPressureType = "fixedValue",
    force_write_interval: int = ITERATIONS,
    u_equation_relaxation: float = 1.0,
    simple_consistent: bool = True,
    u_solver_type: USolverType = "smoothSolver",
) -> dict[str, str]:
    if outlet_velocity_type not in OUTLET_VELOCITY_TYPES:
        raise ValueError(f"unsupported outlet velocity type: {outlet_velocity_type}")
    if inlet_pressure_type not in INLET_PRESSURE_TYPES:
        raise ValueError(f"unsupported inlet pressure type: {inlet_pressure_type}")
    if force_write_interval < 1:
        raise ValueError("force write interval must be positive")
    if not 0.0 < u_equation_relaxation <= 1.0:
        raise ValueError("U equation relaxation must be in (0, 1]")
    if u_solver_type not in U_SOLVER_TYPES:
        raise ValueError(f"unsupported U solver type: {u_solver_type}")
    outlet_velocity = {
        "fixedValue": "outlet { type fixedValue; value uniform (1 0 0); }",
        "pressureInletOutletVelocity": "outlet { type pressureInletOutletVelocity; value uniform (1 0 0); }",
        "zeroGradient": "outlet { type zeroGradient; }",
    }[outlet_velocity_type]
    exact_inlet_pressure = f"{spec.pressure_gradient_m2_s2_per_m:.17g}"
    inlet_pressure = {
        "fixedValue": f"inlet {{ type fixedValue; value uniform {exact_inlet_pressure}; }}",
        "fixedFluxPressure": f"inlet {{ type fixedFluxPressure; value uniform {exact_inlet_pressure}; }}",
        "fixedGradient": f"inlet {{ type fixedGradient; gradient uniform {exact_inlet_pressure}; }}",
    }[inlet_pressure_type]
    u_solver = (
        "solver smoothSolver; smoother symGaussSeidel;"
        if u_solver_type == "smoothSolver"
        else "solver PBiCGStab; preconditioner DILU;"
    )
    block = _header("system", "blockMeshDict") + f"""convertToMeters 1;
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1));
blocks (hex (0 1 2 3 4 5 6 7) ({n} {n} {n}) simpleGrading (1 1 1)); edges ();
boundary (
 inlet {{ type patch; faces ((0 4 7 3)); }} outlet {{ type patch; faces ((1 2 6 5)); }}
 yMin {{ type symmetryPlane; faces ((0 1 5 4)); }} yMax {{ type symmetryPlane; faces ((3 7 6 2)); }}
 zMin {{ type symmetryPlane; faces ((0 3 2 1)); }} zMax {{ type symmetryPlane; faces ((4 5 6 7)); }} ); mergePatchPairs ();\n"""
    u = _header("0", "U", "volVectorField") + f"dimensions [0 1 -1 0 0 0 0]; internalField uniform (1 0 0);\nboundaryField {{ inlet {{ type fixedValue; value uniform (1 0 0); }} {outlet_velocity} yMin {{ type symmetryPlane; }} yMax {{ type symmetryPlane; }} zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }} }}\n"
    p = _header("0", "p", "volScalarField") + f"""dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;
boundaryField {{ {inlet_pressure} outlet {{ type fixedValue; value uniform 0; }} yMin {{ type symmetryPlane; }} yMax {{ type symmetryPlane; }} zMin {{ type symmetryPlane; }} zMax {{ type symmetryPlane; }} }}\n"""
    source = _header("constant", "fvModels") + f"""mmsSource {{ type semiImplicitSource; select all; volumeMode specific; sources {{ U {{ explicit ({-spec.pressure_gradient_m2_s2_per_m:.17g} 0 0); implicit 0; }} }} }}\n"""
    control = _header("system", "controlDict") + f"""application foamRun; startFrom startTime; startTime 0; stopAt endTime; endTime {ITERATIONS}; deltaT 1; writeControl timeStep; writeInterval {ITERATIONS}; writeFormat ascii; writePrecision 16; runTimeModifiable false;
functions {{ residuals {{ type residuals; libs (\"libutilityFunctionObjects.so\"); fields (U p); writeControl timeStep; writeInterval 1; }} forces {{ type forces; libs (\"libforces.so\"); patches (inlet outlet); CofR (0 0 0); rho rhoInf; rhoInf 1; writeControl timeStep; writeInterval {force_write_interval}; }} inletFlux {{ type surfaceFieldValue; libs (\"libfieldFunctionObjects.so\"); regionType patch; name inlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {ITERATIONS}; }} outletFlux {{ type surfaceFieldValue; libs (\"libfieldFunctionObjects.so\"); regionType patch; name outlet; operation sum; fields (phi); writeFields false; writeControl timeStep; writeInterval {ITERATIONS}; }} }}\n"""
    schemes = _header("system", "fvSchemes") + "ddtSchemes { default steadyState; } gradSchemes { default Gauss linear; } divSchemes { default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear; } laplacianSchemes { default Gauss linear corrected; } interpolationSchemes { default linear; } snGradSchemes { default corrected; }\n"
    solution = _header("system", "fvSolution") + f"solvers {{ p {{ solver GAMG; smoother GaussSeidel; tolerance 1e-10; relTol 0; }} pFinal {{ $p; relTol 0; }} U {{ {u_solver} tolerance 1e-10; relTol 0; }} UFinal {{ $U; relTol 0; }} }} SIMPLE {{ nNonOrthogonalCorrectors 0; consistent {'yes' if simple_consistent else 'no'}; pRefCell 0; pRefValue 0; residualControl {{ p 1e-8; U 1e-8; }} }} relaxationFactors {{ equations {{ U {u_equation_relaxation:.12g}; }} }}\n"
    implementation: dict[str, Any] = {
        "schema": "flowlab.open-boundary-mms-boundary-implementation.v1",
        "inletPressure": inlet_pressure_type,
        "inletVelocity": "fixedValue",
        "outletPressure": "fixedValue",
        "outletVelocity": outlet_velocity_type,
        "outletVelocityAnalyticFallback": "(1 0 0)",
        "sidePlanes": "symmetryPlane",
    }
    if inlet_pressure_type == "fixedGradient":
        implementation["inletPressureAnalyticNormalGradient"] = (
            spec.pressure_gradient_m2_s2_per_m
        )
    else:
        implementation["inletPressureAnalyticInitialValue"] = (
            spec.pressure_gradient_m2_s2_per_m
        )
    diagnostic_sampling = {
        "schema": "flowlab.open-boundary-mms-diagnostic-sampling.v1",
        "forcesWriteInterval": force_write_interval,
        "residualsWriteInterval": 1,
        "changesSolve": False,
    }
    solver_controls = {
        "schema": "flowlab.open-boundary-mms-solver-controls.v1",
        "uEquationRelaxation": u_equation_relaxation,
        "simpleConsistent": simple_consistent,
        "uSolverType": u_solver_type,
        "iterationLimit": ITERATIONS,
        "linearRelativeTolerance": 0,
        "linearAbsoluteTolerance": 1.0e-10,
    }
    return {"system/blockMeshDict": block, "0/U": u, "0/p": p, "constant/fvModels": source, "constant/physicalProperties": _header("constant", "physicalProperties") + f"viscosityModel constant; nu [0 2 -1 0 0 0 0] {spec.viscosity_m2_s:.17g};\n", "constant/momentumTransport": _header("constant", "momentumTransport") + "simulationType laminar;\n", "system/controlDict": control, "system/fvSchemes": schemes, "system/fvSolution": solution, "mms-definition.json": json.dumps(spec.manifest(), indent=2, sort_keys=True) + "\n", "boundary-implementation.json": json.dumps(implementation, indent=2, sort_keys=True) + "\n", "diagnostic-sampling.json": json.dumps(diagnostic_sampling, indent=2, sort_keys=True) + "\n", "solver-controls.json": json.dumps(solver_controls, indent=2, sort_keys=True) + "\n"}


def _run(cmd: list[str], cwd: Path, log: Path) -> int:
    completed = subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    _write(log, completed.stdout)
    return completed.returncode


def _init_exact(case: Path, spec: MmsDefinition) -> None:
    c = _read_cell_centres(case / "0/C")
    u = [spec.velocity(*point) for point in c]; p = [spec.pressure(*point) for point in c]
    _write(case / "0/U", (case / "0/U").read_text(encoding="utf-8").replace("internalField uniform (1 0 0);", "internalField " + _nonuniform_vector_field(u) + ";", 1))
    _write(case / "0/p", (case / "0/p").read_text(encoding="utf-8").replace("internalField uniform 0;", "internalField " + _nonuniform_scalar_field(p) + ";", 1))
    _write(case / "exact-init.json", json.dumps({"method": "writeCellCentres then analytic fields", "cellCount": len(c)}, indent=2) + "\n")


def _values(path: Path, vector: bool) -> list[tuple[float, ...]]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(); marker = "List<vector>" if vector else "List<scalar>"
    start = next(i for i, line in enumerate(lines) if marker in line); count = int(lines[start + 1]); return [tuple(float(x) for x in line.strip().strip("()").split()) for line in lines[start + 3:start + 3 + count]]


def _l2(actual: list[tuple[float, ...]], exact: list[tuple[float, ...]]) -> float:
    top = sum(sum((a-b)**2 for a,b in zip(left,right)) for left,right in zip(actual,exact)); bottom = sum(sum(b*b for b in right) for right in exact); return math.sqrt(top/bottom)


def _table_value(case: Path, name: str) -> float:
    paths = sorted((case / "postProcessing" / name).glob("**/surfaceFieldValue.dat"))
    if not paths: return math.inf
    for line in reversed(paths[-1].read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.strip() and not line.lstrip().startswith("#"):
            try: return float(line.split()[-1])
            except ValueError: continue
    return math.inf


def _flux_imbalance(case: Path) -> float:
    inlet, outlet = _table_value(case, "inletFlux"), _table_value(case, "outletFlux")
    return abs(inlet + outlet) / max(abs(inlet), 1e-30) if math.isfinite(inlet) and math.isfinite(outlet) else math.inf


def _traction_imbalance(case: Path, spec: MmsDefinition) -> float:
    paths = sorted((case / "postProcessing" / "forces").glob("**/force*.dat"))
    if not paths: return math.inf
    rows = [line for line in paths[-1].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    vectors = re.findall(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", rows[-1]) if rows else []
    if len(vectors) < 2: return math.inf
    force_x = sum(float(vector[0]) for vector in vectors[:2]); source_x = -spec.pressure_gradient_m2_s2_per_m
    # `forces` reports force exerted by the fluid on the selected patches;
    # for this balance it has the same sign as the integrated body source.
    return abs(force_x - source_x) / max(abs(source_x), 1e-30)


def _observe(
    root: Path,
    label: str,
    n: int,
    spec: MmsDefinition,
    *,
    outlet_velocity_type: OutletVelocityType = "fixedValue",
    inlet_pressure_type: InletPressureType = "fixedValue",
    force_write_interval: int = ITERATIONS,
    u_equation_relaxation: float = 1.0,
    simple_consistent: bool = True,
    u_solver_type: USolverType = "smoothSolver",
) -> RefinementObservation:
    case = root / label / "case"; artifacts = root / label / "artifacts"
    for name, content in _case_files(n, spec, outlet_velocity_type=outlet_velocity_type, inlet_pressure_type=inlet_pressure_type, force_write_interval=force_write_interval, u_equation_relaxation=u_equation_relaxation, simple_consistent=simple_consistent, u_solver_type=u_solver_type).items(): _write(case / name, content)
    block = _run(["blockMesh"], case, artifacts / "blockMesh.log"); check = _run(["checkMesh", "-allGeometry", "-allTopology"], case, artifacts / "checkMesh.log") if block == 0 else 127
    centres = _run(["foamPostProcess", "-func", "writeCellCentres", "-time", "0"], case, artifacts / "writeCellCentres.log") if check == 0 else 127
    if centres == 0: _init_exact(case, spec)
    solver = _run(["foamRun", "-solver", "incompressibleFluid"], case, artifacts / "foamRun.log") if centres == 0 else 127
    try:
        c = _read_cell_centres(case / "0/C"); au = _values(case / str(ITERATIONS) / "U", True); ap = _values(case / str(ITERATIONS) / "p", False); eu = [spec.velocity(*x) for x in c]; ep = [(spec.pressure(*x),) for x in c]; ue, pe = _l2(au, eu), _l2(ap, ep); ae = math.sqrt(sum((a[0]-b[0])**2 for a,b in zip(au,eu))/sum(b[0]**2 for b in eu)); te = math.sqrt(sum(a[1]**2+a[2]**2 for a in au)/sum(b[0]**2 for b in eu))
    except (OSError, ValueError, StopIteration, ZeroDivisionError): ue = pe = ae = te = math.inf
    residuals = [float(x) for x in re.findall(r"Final residual = ([0-9.eE+-]+)", (artifacts / "foamRun.log").read_text(encoding="utf-8", errors="replace"))]
    observation = RefinementObservation(label, 1/n, block == 0 and check == 0 and "Mesh OK" in (artifacts / "checkMesh.log").read_text(encoding="utf-8", errors="replace"), ue, pe, ae, te, _flux_imbalance(case), max(residuals[-4:]) if residuals else math.inf, _traction_imbalance(case, spec), {"case": str(case), "blockMesh": str(artifacts / "blockMesh.log"), "checkMesh": str(artifacts / "checkMesh.log"), "solver": str(artifacts / "foamRun.log"), "exactInit": str(case / "exact-init.json")})
    _write(artifacts / "observation.json", json.dumps(observation.__dict__, indent=2, sort_keys=True) + "\n"); return observation


def run(
    output: Path,
    *,
    outlet_velocity_type: OutletVelocityType = "fixedValue",
    inlet_pressure_type: InletPressureType = "fixedValue",
    force_write_interval: int = ITERATIONS,
    u_equation_relaxation: float = 1.0,
    simple_consistent: bool = True,
    u_solver_type: USolverType = "smoothSolver",
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()): raise ValueError(f"refusing to overwrite {output}")
    spec = MmsDefinition(); observations = [_observe(output, label, n, spec, outlet_velocity_type=outlet_velocity_type, inlet_pressure_type=inlet_pressure_type, force_write_interval=force_write_interval, u_equation_relaxation=u_equation_relaxation, simple_consistent=simple_consistent, u_solver_type=u_solver_type) for label, n in LEVELS]; report = evaluate_mms_stage(observations, definition=spec); report["boundaryImplementation"] = {"inletPressure": inlet_pressure_type, "outletPressure": "fixedValue", "outletVelocity": outlet_velocity_type}; report["diagnosticSampling"] = {"forcesWriteInterval": force_write_interval, "residualsWriteInterval": 1}; report["solverControls"] = {"uEquationRelaxation": u_equation_relaxation, "simpleConsistent": simple_consistent, "uSolverType": u_solver_type, "iterationLimit": ITERATIONS}; _write(output / "artifacts/mms-stage-report.json", json.dumps(report, indent=2, sort_keys=True) + "\n"); return report


def _coarse_advancement_report(
    observation: RefinementObservation,
    baseline: dict[str, Any],
    *,
    baseline_path: Path,
    outlet_velocity_type: OutletVelocityType,
) -> dict[str, Any]:
    checks = {
        "checkMesh": observation.check_mesh_passed,
        "massRelativeImbalance": observation.mass_relative_imbalance <= MASS_LIMIT,
        "boundaryTractionRelativeImbalance": observation.boundary_traction_relative_imbalance <= MASS_LIMIT,
        "finalLinearResidual": observation.final_linear_residual <= LINEAR_RESIDUAL_LIMIT,
        "velocityErrorImprovedVsControl": observation.velocity_l2_error < float(baseline["velocity_l2_error"]),
        "pressureErrorImprovedVsControl": observation.pressure_l2_error < float(baseline["pressure_l2_error"]),
    }
    passed = all(checks.values())
    return {
        "schema": COARSE_ADVANCEMENT_SCHEMA,
        "status": "advance" if passed else "blocked",
        "scientificStatus": "analysis-only",
        "validated": False,
        "scope": "coarse-grid diagnostic advancement gate; not a three-grid validation result",
        "singleBoundaryImplementationChange": {
            "field": "U",
            "patch": "outlet",
            "before": "fixedValue",
            "after": outlet_velocity_type,
            "outletPressureRetained": "fixedValue",
        },
        "frozenControls": [
            "mesh",
            "momentum source",
            "pressure boundary implementation",
            "solver tolerances",
            "iteration count",
            "numerical schemes",
            "exact initialization",
        ],
        "limits": {
            "massRelativeImbalance": MASS_LIMIT,
            "boundaryTractionRelativeImbalance": MASS_LIMIT,
            "finalLinearResidual": LINEAR_RESIDUAL_LIMIT,
            "fieldErrorComparison": "strictly lower than unchanged v10 coarse control",
        },
        "checks": checks,
        "failedChecks": [name for name, passed_check in checks.items() if not passed_check],
        "control": {
            "path": str(baseline_path),
            "velocityL2Error": float(baseline["velocity_l2_error"]),
            "pressureL2Error": float(baseline["pressure_l2_error"]),
        },
        "observation": observation.__dict__,
        "nextStage": {
            "forcedMmsThreeGrid": "authorized" if passed else "blocked",
            "openPipe": "blocked until forced MMS passes",
            "frozenSurface": "blocked until forced MMS and open pipe pass",
        },
    }


def _inlet_pressure_advancement_report(
    observation: RefinementObservation,
    baseline: dict[str, Any],
    *,
    baseline_path: Path,
    inlet_pressure_type: InletPressureType,
    exact_inlet_pressure: float,
) -> dict[str, Any]:
    checks = {
        "checkMesh": observation.check_mesh_passed,
        "massRelativeImbalance": observation.mass_relative_imbalance <= MASS_LIMIT,
        "boundaryTractionRelativeImbalance": observation.boundary_traction_relative_imbalance <= MASS_LIMIT,
        "finalLinearResidual": observation.final_linear_residual <= LINEAR_RESIDUAL_LIMIT,
        "velocityErrorImprovedVsControl": observation.velocity_l2_error < float(baseline["velocity_l2_error"]),
        "pressureErrorImprovedVsControl": observation.pressure_l2_error < float(baseline["pressure_l2_error"]),
    }
    passed = all(checks.values())
    return {
        "schema": INLET_PRESSURE_ADVANCEMENT_SCHEMA,
        "status": "advance" if passed else "blocked",
        "scientificStatus": "analysis-only",
        "validated": False,
        "scope": "coarse-grid inlet-pressure advancement gate; not a three-grid validation result",
        "singleBoundaryImplementationChange": {
            "field": "p",
            "patch": "inlet",
            "before": "fixedValue",
            "after": inlet_pressure_type,
            (
                "analyticNormalGradient"
                if inlet_pressure_type == "fixedGradient"
                else "analyticInitialValue"
            ): exact_inlet_pressure,
            "inletVelocityRetained": "fixedValue (1 0 0)",
            "outletPressureRetained": "fixedValue 0",
            "outletVelocityRetained": "pressureInletOutletVelocity",
        },
        "frozenControls": [
            "mesh",
            "momentum source",
            "inlet velocity",
            "outlet pressure",
            "outlet velocity",
            "solver tolerances",
            "iteration count",
            "numerical schemes",
            "exact initialization",
        ],
        "limits": {
            "massRelativeImbalance": MASS_LIMIT,
            "boundaryTractionRelativeImbalance": MASS_LIMIT,
            "finalLinearResidual": LINEAR_RESIDUAL_LIMIT,
            "fieldErrorComparison": "strictly lower than the retained v12 coarse control",
        },
        "checks": checks,
        "failedChecks": [name for name, passed_check in checks.items() if not passed_check],
        "control": {
            "path": str(baseline_path),
            "velocityL2Error": float(baseline["velocity_l2_error"]),
            "pressureL2Error": float(baseline["pressure_l2_error"]),
            "boundaryTractionRelativeImbalance": float(
                baseline["boundary_traction_relative_imbalance"]
            ),
        },
        "observation": observation.__dict__,
        "nextStage": {
            "forcedMmsThreeGrid": "authorized" if passed else "blocked",
            "coarseOnlyExecutionComplete": True,
            "threeGridExecuted": False,
            "openPipe": "blocked until forced MMS passes",
            "frozenSurface": "blocked until forced MMS and open pipe pass",
        },
    }


def run_coarse_advancement(
    output: Path,
    *,
    baseline_observation: Path,
    outlet_velocity_type: OutletVelocityType = "pressureInletOutletVelocity",
    inlet_pressure_type: InletPressureType = "fixedValue",
    force_write_interval: int = ITERATIONS,
    u_equation_relaxation: float = 1.0,
    simple_consistent: bool = True,
    u_solver_type: USolverType = "smoothSolver",
) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()): raise ValueError(f"refusing to overwrite {output}")
    baseline = json.loads(baseline_observation.read_text(encoding="utf-8"))
    if baseline.get("level") != "coarse":
        raise ValueError("baseline observation must be a coarse refinement")
    spec = MmsDefinition()
    observation = _observe(output, "coarse", 12, spec, outlet_velocity_type=outlet_velocity_type, inlet_pressure_type=inlet_pressure_type, force_write_interval=force_write_interval, u_equation_relaxation=u_equation_relaxation, simple_consistent=simple_consistent, u_solver_type=u_solver_type)
    if inlet_pressure_type == "fixedValue":
        report = _coarse_advancement_report(
            observation,
            baseline,
            baseline_path=baseline_observation,
            outlet_velocity_type=outlet_velocity_type,
        )
    else:
        report = _inlet_pressure_advancement_report(
            observation,
            baseline,
            baseline_path=baseline_observation,
            inlet_pressure_type=inlet_pressure_type,
            exact_inlet_pressure=spec.pressure_gradient_m2_s2_per_m,
        )
    report["diagnosticSampling"] = {
        "forcesWriteInterval": force_write_interval,
        "residualsWriteInterval": 1,
        "changesSolve": False,
    }
    report["solverControls"] = {
        "uEquationRelaxation": u_equation_relaxation,
        "simpleConsistent": simple_consistent,
        "uSolverType": u_solver_type,
        "iterationLimit": ITERATIONS,
    }
    _write(output / "artifacts/coarse-advancement-report.json", json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--outlet-velocity", choices=OUTLET_VELOCITY_TYPES, default="fixedValue")
    parser.add_argument("--inlet-pressure", choices=INLET_PRESSURE_TYPES, default="fixedValue")
    parser.add_argument("--coarse-only", action="store_true")
    parser.add_argument("--baseline-observation", type=Path)
    parser.add_argument("--force-write-interval", type=int, default=ITERATIONS)
    parser.add_argument("--u-equation-relaxation", type=float, default=1.0)
    parser.add_argument("--simple-consistent", choices=("yes", "no"), default="yes")
    parser.add_argument("--u-solver", choices=U_SOLVER_TYPES, default="smoothSolver")
    args = parser.parse_args()
    if args.coarse_only:
        if args.baseline_observation is None:
            parser.error("--coarse-only requires --baseline-observation")
        report = run_coarse_advancement(
            args.output.resolve(),
            baseline_observation=args.baseline_observation.resolve(),
            outlet_velocity_type=args.outlet_velocity,
            inlet_pressure_type=args.inlet_pressure,
            force_write_interval=args.force_write_interval,
            u_equation_relaxation=args.u_equation_relaxation,
            simple_consistent=args.simple_consistent == "yes",
            u_solver_type=args.u_solver,
        )
        success = report["status"] == "advance"
    else:
        if args.baseline_observation is not None:
            parser.error("--baseline-observation is only valid with --coarse-only")
        report = run(args.output.resolve(), outlet_velocity_type=args.outlet_velocity, inlet_pressure_type=args.inlet_pressure, force_write_interval=args.force_write_interval, u_equation_relaxation=args.u_equation_relaxation, simple_consistent=args.simple_consistent == "yes", u_solver_type=args.u_solver)
        success = report["status"] == "accepted"
    print(json.dumps(report, indent=2))
    return 0 if success else 2


if __name__ == "__main__": raise SystemExit(main())
