"""Materialize, but never execute, a gated CAD parabolic-inlet smoke case."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Any

SCHEMA = "flowlab.cad-parabolic-smoke.v1"
GATE_SCHEMA = "flowlab.immutable-surface-layered-gate.v1"
STRUCTURED_REFERENCE_GATE_SCHEMA = "flowlab.structured-pipe-reference-mesh-gate.v1"
POLYMESH = ("points", "faces", "owner", "neighbour", "boundary")


class CadParabolicSmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CadParabolicSmokeSpec:
    length_m: float = 0.05
    radius_m: float = 0.005
    density_kg_m3: float = 1000.0
    dynamic_viscosity_pa_s: float = 0.001
    volumetric_flow_rate_m3_s: float = 1.0e-5
    axis: str = "z"

    def __post_init__(self) -> None:
        numeric = (self.length_m, self.radius_m, self.density_kg_m3, self.dynamic_viscosity_pa_s, self.volumetric_flow_rate_m3_s)
        if any(not math.isfinite(v) or v <= 0 for v in numeric):
            raise CadParabolicSmokeError("all CAD smoke SI inputs must be finite and positive")
        if self.axis not in ("x", "z"):
            raise CadParabolicSmokeError("CAD smoke axis must be x or z")

    @property
    def centerline_velocity_m_per_s(self) -> float:
        return 2 * self.volumetric_flow_rate_m3_s / (math.pi * self.radius_m**2)

    def analytic_flow_m3_per_s(self) -> float:
        return self.centerline_velocity_m_per_s * math.pi * self.radius_m**2 / 2


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_accepted_immutable_mesh(*, source_poly_mesh: Path, immutable_gate_report: Path) -> dict[str, Any]:
    """Fail closed unless this exact imported mesh has accepted retained evidence."""
    source_poly_mesh = source_poly_mesh.resolve()
    try:
        report = json.loads(immutable_gate_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CadParabolicSmokeError("could not read immutable-surface gate report") from exc
    schema = report.get("schema")
    if schema not in (GATE_SCHEMA, STRUCTURED_REFERENCE_GATE_SCHEMA) or report.get("accepted") is not True:
        raise CadParabolicSmokeError("CAD smoke is blocked: mesh gate report is not accepted")
    if schema == GATE_SCHEMA and report.get("expected", {}).get("patches") != {"inlet": 11, "outlet": 12, "wall": 13}:
        raise CadParabolicSmokeError("immutable gate does not bind inlet/outlet/wall")
    if schema == STRUCTURED_REFERENCE_GATE_SCHEMA and report.get("expected", {}).get("patches") != ["inlet", "outlet", "wall"]:
        raise CadParabolicSmokeError("structured-reference gate does not bind inlet/outlet/wall")
    check = report.get("checkMesh", {})
    counts = check.get("issueCounts", {}) if isinstance(check, dict) else {}
    bad = ("smallDeterminantCells", "lowInterpolationWeightFaces", "lowVolumeRatioFaces", "concaveCells")
    if check.get("meshOk") is not True or check.get("failedChecks") != 0 or any(counts.get(k, 0) != 0 for k in bad):
        raise CadParabolicSmokeError("CAD smoke is blocked: mesh quality is not clean")
    if set(report.get("conversion", {}).get("patchNames", [])) != {"inlet", "outlet", "wall"}:
        raise CadParabolicSmokeError("mesh gate conversion lacks exact inlet/outlet/wall patches")
    artifacts = report.get("artifacts", {})
    records: dict[str, str] = {}
    for name in POLYMESH:
        path = source_poly_mesh / name
        recorded = artifacts.get(f"polyMesh/{name}", {}).get("path")
        if not path.is_file() or path.stat().st_size == 0:
            raise CadParabolicSmokeError(f"source polyMesh is missing {name}")
        if not isinstance(recorded, str) or Path(recorded).resolve() != path.resolve():
            raise CadParabolicSmokeError(f"immutable evidence does not bind supplied polyMesh/{name}")
        records[name] = _sha(path)
    boundary = (source_poly_mesh / "boundary").read_text(encoding="utf-8", errors="replace")
    if any(name not in boundary for name in ("inlet", "outlet", "wall")):
        raise CadParabolicSmokeError("source polyMesh boundary lacks inlet/outlet/wall")
    return {"gateReport": str(immutable_gate_report.resolve()), "gateReportSha256": _sha(immutable_gate_report), "sourcePolyMesh": str(source_poly_mesh), "polyMeshFiles": records}


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def _dict_header(location: str, object_name: str) -> str:
    return f'FoamFile {{ version 2.0; format ascii; class dictionary; location "{location}"; object {object_name}; }}\n'


def _u(spec: CadParabolicSmokeSpec, *, fully_developed_outlet: bool = False) -> str:
    radial_expression = "sqr(centres[facei].x())+sqr(centres[facei].y())" if spec.axis == "z" else "sqr(centres[facei].y())+sqr(centres[facei].z())"
    component = "z" if spec.axis == "z" else "x"
    uniform = f"(0 0 {spec.volumetric_flow_rate_m3_s / (math.pi * spec.radius_m**2):.17g})" if spec.axis == "z" else f"({spec.volumetric_flow_rate_m3_s / (math.pi * spec.radius_m**2):.17g} 0 0)"
    outlet = '''// Couple the pressure-corrected outlet flux back into U. A zeroGradient U
// here leaves adjustPhi's outlet flux disconnected from the velocity field.
outlet { type pressureInletOutletVelocity; value uniform (0 0 0); }'''
    if fully_developed_outlet:
        outlet = f'''// The structured analytical reference applies the same fully-developed
// velocity profile at both open ends.  This removes a finite-length outlet
// momentum-flux defect from a test of the interior discretization.
outlet {{ type codedFixedValue; value uniform (0 0 0); name fullyDevelopedPipeOutlet; code #{{
const scalar radius = {spec.radius_m:.17g}; const scalar targetFlow = {spec.volumetric_flow_rate_m3_s:.17g};
const vectorField& centres = patch().Cf(); const vectorField& areas = patch().Sf();
scalarField shape(patch().size(), 0.0); scalar weightedArea = 0.0;
forAll(shape, facei) {{ const scalar r2={radial_expression}; shape[facei]=max(scalar(0), scalar(1)-r2/sqr(radius)); weightedArea += mag(areas[facei])*shape[facei]; }}
vectorField profile(patch().size(), vector::zero); const scalar profileScale=targetFlow/weightedArea;
forAll(profile, facei) {{ profile[facei].{component}()=profileScale*shape[facei]; }}
operator==(profile); #}}; }}'''
    return f'''FoamFile {{ version 2.0; format ascii; class volVectorField; location "0"; object U; }}
dimensions [0 1 -1 0 0 0 0];
// A mean-flow initialization avoids an artificial start-up pressure pulse in
// this steady SIMPLE smoke while preserving the prescribed inlet profile.
internalField uniform {uniform};
boundaryField
{{
inlet {{ type codedFixedValue; value uniform (0 0 0); name fullyDevelopedPipeInlet; code #{{
const scalar radius = {spec.radius_m:.17g}; const scalar targetFlow = {spec.volumetric_flow_rate_m3_s:.17g};
const vectorField& centres = patch().Cf(); const vectorField& areas = patch().Sf();
scalarField shape(patch().size(), 0.0); scalar weightedArea = 0.0;
forAll(shape, facei) {{ const scalar r2={radial_expression}; shape[facei]=max(scalar(0), scalar(1)-r2/sqr(radius)); weightedArea += mag(areas[facei])*shape[facei]; }}
vectorField profile(patch().size(), vector::zero); const scalar profileScale=targetFlow/weightedArea;
forAll(profile, facei) {{ profile[facei].{component}()=profileScale*shape[facei]; }}
operator==(profile); #}}; }}
{outlet}
wall {{ type noSlip; }}
}}
'''


def _p() -> str:
    return '''FoamFile { version 2.0; format ascii; class volScalarField; location "0"; object p; }
// incompressibleFluid solves kinematic pressure; density is used only when
// reporting the physical pressure-drop QoI. This is paired with
// pressureInletOutletVelocity at the open outlet, which keeps U and the
// pressure-corrected flux coupled.
dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;
boundaryField { inlet { type zeroGradient; } outlet { type fixedValue; value uniform 0; } wall { type zeroGradient; } }
'''


def _control(end_time: int = 2000, *, diagnostics: bool = False) -> str:
    if end_time < 1:
        raise CadParabolicSmokeError("exact-init smoke end time must be positive")
    functions = "\n".join(f'''{name} {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so"); writeControl timeStep; writeInterval 1; writeFields false; select patch; patch {patch}; operation {operation}; fields ({field}); }}''' for name, patch, operation, field in (("inletPressure", "inlet", "areaAverage", "p"), ("outletPressure", "outlet", "areaAverage", "p"), ("inletFlux", "inlet", "sum", "phi"), ("outletFlux", "outlet", "sum", "phi")))
    if diagnostics:
        # These objects run inside foamRun so the solver-owned momentum-
        # transport model is present.  Running wallShearStress through a bare
        # post-processor does not create that model in Foundation-v11.
        functions += '''
transverseMomentumResiduals { type residuals; libs ("libutilityFunctionObjects.so"); writeControl timeStep; writeInterval 1; fields (U); }
wallShearStress { type wallShearStress; libs ("libfieldFunctionObjects.so"); writeControl timeStep; writeInterval 100; patches (wall); }
wallForces { type forces; libs ("libforces.so"); writeControl timeStep; writeInterval 100; patches (wall); rho rhoInf; rhoInf 1000; CofR (0 0 0); p p; U U; }
'''
    return f'''FoamFile {{ version 2.0; format ascii; class dictionary; location "system"; object controlDict; }}
application foamRun; solver incompressibleFluid; startFrom startTime; startTime 0; stopAt endTime; endTime {end_time}; deltaT 1; writeControl timeStep; writeInterval 100;
functions {{ {functions} }}
'''


def _read_cell_centres(path: Path) -> list[tuple[float, float, float]]:
    """Read the internal C field emitted by OpenFOAM writeCellCentres."""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip().startswith("internalField"):
                break
        else:
            raise CadParabolicSmokeError("cell-centre field lacks internalField")
        for line in stream:
            value = line.strip()
            if value:
                break
        else:
            raise CadParabolicSmokeError("cell-centre field lacks element count")
        try:
            count = int(value)
        except ValueError as exc:
            raise CadParabolicSmokeError("cell-centre field has non-integer element count") from exc
        for line in stream:
            if line.strip() == "(":
                break
        else:
            raise CadParabolicSmokeError("cell-centre field lacks opening list delimiter")
        centres: list[tuple[float, float, float]] = []
        for _ in range(count):
            values = stream.readline().strip().strip("()").split()
            if len(values) != 3:
                raise CadParabolicSmokeError("cell-centre field has malformed vector entry")
            try:
                centres.append((float(values[0]), float(values[1]), float(values[2])))
            except ValueError as exc:
                raise CadParabolicSmokeError("cell-centre field has non-numeric vector entry") from exc
    return centres


def _nonuniform_vector_field(values: list[tuple[float, float, float]]) -> str:
    return "nonuniform List<vector>\n" + str(len(values)) + "\n(\n" + "\n".join(f"({x:.17g} {y:.17g} {z:.17g})" for x, y, z in values) + "\n)"


def _nonuniform_scalar_field(values: list[float]) -> str:
    return "nonuniform List<scalar>\n" + str(len(values)) + "\n(\n" + "\n".join(f"{value:.17g}" for value in values) + "\n)"


def initialize_exact_parabolic_fields(case_dir: Path, spec: CadParabolicSmokeSpec | None = None) -> dict[str, Any]:
    """Replace uniform initial fields using OpenFOAM-written cell centres.

    This is an explicit diagnostic preparation step.  It does not run a solver
    and never edits constant/polyMesh.
    """
    case_dir = case_dir.resolve()
    manifest_path = case_dir / "cad-smoke-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CadParabolicSmokeError("exact initialization requires a materialized smoke manifest") from exc
    spec = spec or CadParabolicSmokeSpec(**manifest["spec"])
    centres = _read_cell_centres(case_dir / "0" / "C")
    if not centres:
        raise CadParabolicSmokeError("exact initialization requires at least one cell centre")
    radius2 = spec.radius_m**2
    centreline = spec.centerline_velocity_m_per_s
    delta_p = 8 * (spec.dynamic_viscosity_pa_s / spec.density_kg_m3) * spec.length_m * spec.volumetric_flow_rate_m3_s / (math.pi * spec.radius_m**4)
    if spec.axis == "z":
        velocity = [(0.0, 0.0, centreline * max(0.0, 1.0 - (x*x + y*y)/radius2)) for x, y, _ in centres]
        pressure = [delta_p * (1.0 - z/spec.length_m) for _, _, z in centres]
        velocity_description = "U_z = 2Q/(pi R^2) * max(0, 1-(x^2+y^2)/R^2)"
        pressure_description = "p = 8 nu L Q/(pi R^4) * (1-z/L)"
    else:
        velocity = [(centreline * max(0.0, 1.0 - (y*y + z*z)/radius2), 0.0, 0.0) for _, y, z in centres]
        pressure = [delta_p * (1.0 - x/spec.length_m) for x, _, _ in centres]
        velocity_description = "U_x = 2Q/(pi R^2) * max(0, 1-(y^2+z^2)/R^2)"
        pressure_description = "p = 8 nu L Q/(pi R^4) * (1-x/L)"
    u_path = case_dir / "0" / "U"
    p_path = case_dir / "0" / "p"
    u_text = u_path.read_text(encoding="utf-8")
    p_text = p_path.read_text(encoding="utf-8")
    u_text = u_text.replace(
        "internalField uniform " + (f"(0 0 {spec.volumetric_flow_rate_m3_s / (math.pi * spec.radius_m**2):.17g})" if spec.axis == "z" else f"({spec.volumetric_flow_rate_m3_s / (math.pi * spec.radius_m**2):.17g} 0 0)") + ";",
        "internalField " + _nonuniform_vector_field(velocity) + ";",
        1,
    )
    p_text = p_text.replace("internalField uniform 0;", "internalField " + _nonuniform_scalar_field(pressure) + ";", 1)
    if "internalField uniform" in u_text or "internalField uniform 0;" in p_text:
        raise CadParabolicSmokeError("exact initialization could not replace the materialized internal fields")
    _write(u_path, u_text)
    _write(p_path, p_text)
    manifest["exactInitialization"] = {
        "method": "OpenFOAM writeCellCentres followed by analytical fields",
        "cellCount": len(centres),
        "velocity": velocity_description,
        "kinematicPressure": pressure_description,
        "expectedKinematicPressureDropM2PerS2": delta_p,
        "cellCentresSha256": _sha(case_dir / "0" / "C"),
    }
    _write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest["exactInitialization"]


def materialize_cad_parabolic_smoke_case(output_dir: Path, *, source_poly_mesh: Path, immutable_gate_report: Path, spec: CadParabolicSmokeSpec | None = None, iteration_limit: int = 2000, diagnostics: bool = False, non_orthogonal_correctors: int = 2, linear_relative_tolerance: float = 0.1, u_relaxation: float = 0.9, fully_developed_outlet: bool = False) -> dict[str, Any]:
    """Write a smoke case only; solver launch requires explicit external execution."""
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise CadParabolicSmokeError("refusing to overwrite non-empty output directory")
    binding = validate_accepted_immutable_mesh(source_poly_mesh=source_poly_mesh, immutable_gate_report=immutable_gate_report)
    spec = spec or CadParabolicSmokeSpec()
    if non_orthogonal_correctors < 0:
        raise CadParabolicSmokeError("non-orthogonal corrector count cannot be negative")
    if not math.isfinite(linear_relative_tolerance) or not 0 <= linear_relative_tolerance < 1:
        raise CadParabolicSmokeError("linear relative tolerance must be finite and in [0, 1)")
    if not math.isfinite(u_relaxation) or not 0 < u_relaxation <= 1:
        raise CadParabolicSmokeError("U relaxation must be finite and in (0, 1]")
    shutil.copytree(source_poly_mesh.resolve(), output_dir / "constant" / "polyMesh")
    _write(output_dir / "0" / "U", _u(spec, fully_developed_outlet=fully_developed_outlet)); _write(output_dir / "0" / "p", _p())
    _write(output_dir / "constant" / "physicalProperties", _dict_header("constant", "physicalProperties") + f"viscosityModel constant;\nnu [0 2 -1 0 0 0 0] {(spec.dynamic_viscosity_pa_s/spec.density_kg_m3):.17g};\n")
    _write(output_dir / "constant" / "momentumTransport", _dict_header("constant", "momentumTransport") + "simulationType laminar;\n")
    _write(output_dir / "system" / "controlDict", _control(iteration_limit, diagnostics=diagnostics))
    _write(
        output_dir / "system" / "fvSchemes",
        _dict_header("system", "fvSchemes")
        + "ddtSchemes { default steadyState; }\n"
        "// Keep the verification problem on the Foundation-v11 tutorial's\n"
        "// linear operators. Limiting/upwinding a fully-developed analytical\n"
        "// laminar state distorted its pressure drop on the frozen mesh.\n"
        "gradSchemes { default Gauss linear; }\n"
        "divSchemes { default none; div(phi,U) Gauss linear; div((nuEff*dev2(T(grad(U))))) Gauss linear; }\n"
        "laplacianSchemes { default Gauss linear corrected; }\n"
        "interpolationSchemes { default linear; }\n"
        "snGradSchemes { default corrected; }\n",
    )
    _write(
        output_dir / "system" / "fvSolution",
        _dict_header("system", "fvSolution")
        + "solvers { "
        f"p {{ solver GAMG; smoother GaussSeidel; tolerance 1e-8; relTol {linear_relative_tolerance:.17g}; }} "
        "pFinal { $p; relTol 0; } "
        f"U {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol {linear_relative_tolerance:.17g}; }} "
        "UFinal { $U; relTol 0; } }\n"
        "// incompressibleFluid selects a steady SIMPLE loop for steadyState.\n"
        "// Use its consistent (SIMPLEC-style) pressure correction and relax\n"
        "// only the momentum equation. Field-relaxing p here is an\n"
        "// inconsistent second pressure update and caused r3-r7 oscillations.\n"
        f"SIMPLE {{ nNonOrthogonalCorrectors {non_orthogonal_correctors}; consistent yes; pRefCell 0; pRefValue 0; "
        "residualControl { p 1e-8; U 1e-8; } }\n"
        f"relaxationFactors {{ equations {{ U {u_relaxation:.17g}; }} }}\n",
    )
    _write(output_dir / "run_smoke.sh", "#!/usr/bin/env bash\nsource /opt/openfoam11/etc/bashrc\nset -euo pipefail\ncheckMesh -allGeometry -allTopology > log.checkMesh 2>&1\ngrep -q 'Mesh OK' log.checkMesh\nfoamRun -solver incompressibleFluid > log.foamRun 2>&1\ngrep -q End log.foamRun\nfoamToVTK -ascii -latestTime > log.foamToVTK 2>&1\n", True)
    analytic_q = spec.analytic_flow_m3_per_s()
    manifest = {"schema": SCHEMA, "status": "materialized-not-run", "scientificStatus": "analysis-only", "validated": False, "execution": {"solverStarted": False, "requiresExplicitManualExecution": "bash run_smoke.sh", "iterationLimit": iteration_limit, "nonOrthogonalCorrectors": non_orthogonal_correctors, "linearRelativeTolerance": linear_relative_tolerance, "uRelaxation": u_relaxation}, "immutableMeshEvidence": binding, "spec": asdict(spec), "parabolicInlet": {"axis": spec.axis, "centerlineVelocityMPerS": spec.centerline_velocity_m_per_s, "analyticVolumetricFlowRateM3PerS": analytic_q, "declaredVolumetricFlowRateM3PerS": spec.volumetric_flow_rate_m3_s, "relativeIntegralError": abs(analytic_q-spec.volumetric_flow_rate_m3_s)/spec.volumetric_flow_rate_m3_s, "outletVelocity": "same fully-developed parabolic profile" if fully_developed_outlet else "pressureInletOutletVelocity"}, "diagnostics": {"enabled": diagnostics, "contract": ["transverseMomentumResiduals", "wallShearStress", "wallForces"] if diagnostics else []}, "qoiExtraction": {"pressureDrop": "areaAverage(p,inlet) - areaAverage(p,outlet)", "massFlow": "surfaceFieldValue sum(phi) on inlet and outlet"}}
    _write(output_dir / "cad-smoke-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path); parser.add_argument("--source-poly-mesh", type=Path); parser.add_argument("--immutable-gate-report", type=Path); parser.add_argument("--materialize-only", action="store_true"); parser.add_argument("--initialize-exact", type=Path)
    args = parser.parse_args()
    if args.initialize_exact:
        if any(value is not None for value in (args.output_dir, args.source_poly_mesh, args.immutable_gate_report)) or args.materialize_only:
            parser.error("--initialize-exact cannot be combined with materialization arguments")
        print(json.dumps(initialize_exact_parabolic_fields(args.initialize_exact), indent=2, sort_keys=True))
        return 0
    if not args.materialize_only or not all((args.output_dir, args.source_poly_mesh, args.immutable_gate_report)):
        parser.error("--materialize-only with --output-dir, --source-poly-mesh, and --immutable-gate-report is required")
    print(json.dumps(materialize_cad_parabolic_smoke_case(args.output_dir, source_poly_mesh=args.source_poly_mesh, immutable_gate_report=args.immutable_gate_report), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
