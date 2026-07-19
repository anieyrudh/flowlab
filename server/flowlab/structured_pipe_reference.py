"""Run a topology-only structured O-grid Poiseuille reference.

This intentionally bypasses the frozen CAD surface and every prism/tet
interface while retaining the current open-boundary, exact-initialization, and
100-iteration numerical contract.  It is a diagnostic reference, never a CAD
validation result.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence

from .cad_parabolic_smoke import CadParabolicSmokeSpec, STRUCTURED_REFERENCE_GATE_SCHEMA, materialize_cad_parabolic_smoke_case
from .straight_pipe_runner import FULL_PIPE_OGRID_MESH_RECIPE, StraightPipeRunSpec, _full_pipe_ogrid_block_mesh_dict
from .volume_discretization_campaign import _assert_exact_iteration_limit, _exact_gate


SCHEMA = "flowlab.structured-pipe-reference.v1"
POLYMESH = ("points", "faces", "owner", "neighbour", "boundary")
DIAGNOSTIC_SCHEMA = "flowlab.structured-pipe-reference-diagnostics.v1"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reference_spec(cross_section_cells_per_quadrant: int = 16) -> StraightPipeRunSpec:
    """Use the CAD smoke physics with a three-level, all-hex O-grid family."""
    smoke = CadParabolicSmokeSpec()
    return StraightPipeRunSpec(
        length_m=smoke.length_m,
        radius_m=smoke.radius_m,
        density_kg_m3=smoke.density_kg_m3,
        dynamic_viscosity_pa_s=smoke.dynamic_viscosity_pa_s,
        volumetric_flow_rate_m3_s=smoke.volumetric_flow_rate_m3_s,
        mesh_sizes_m=(0.00125, 0.000625, 0.0003125),
        mesh_recipe=FULL_PIPE_OGRID_MESH_RECIPE,
        ogrid_azimuthal_cells_per_quadrant=cross_section_cells_per_quadrant,
        ogrid_core_cells_per_side=cross_section_cells_per_quadrant,
    )


def open_boundary_ogrid_dict(spec: StraightPipeRunSpec, mesh_size_m: float) -> str:
    """Convert the existing all-hex O-grid topology from cyclic to open ends."""
    text = _full_pipe_ogrid_block_mesh_dict(spec, mesh_size_m)
    text = text.replace("type cyclic;\n        neighbourPatch outlet;", "type patch;", 1)
    text = text.replace("type cyclic;\n        neighbourPatch inlet;", "type patch;", 1)
    if "type cyclic" in text or text.count("type patch;") != 2:
        raise RuntimeError("could not materialize open inlet/outlet patches for structured O-grid")
    return text


def _run(command: Sequence[str], log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    log.write_text(completed.stdout, encoding="utf-8")
    return completed.returncode


def _mesh_gate(mesh_dir: Path, check_log: Path) -> dict[str, Any]:
    poly_mesh = mesh_dir / "constant" / "polyMesh"
    boundary = (poly_mesh / "boundary").read_text(encoding="utf-8", errors="replace") if (poly_mesh / "boundary").is_file() else ""
    log = check_log.read_text(encoding="utf-8", errors="replace") if check_log.is_file() else ""
    mesh_ok = "Mesh OK" in log
    cells = re.search(r"\bcells:\s+(\d+)", log)
    artifacts = {f"polyMesh/{name}": {"path": str((poly_mesh / name).resolve()), "sha256": _sha(poly_mesh / name)} for name in POLYMESH if (poly_mesh / name).is_file()}
    accepted = mesh_ok and set(artifacts) == {f"polyMesh/{name}" for name in POLYMESH} and all(name in boundary for name in ("inlet", "outlet", "wall"))
    return {
        "schema": STRUCTURED_REFERENCE_GATE_SCHEMA,
        "accepted": accepted,
        "expected": {"patches": ["inlet", "outlet", "wall"]},
        "conversion": {"patchNames": ["inlet", "outlet", "wall"]},
        "checkMesh": {"meshOk": mesh_ok, "failedChecks": 0 if mesh_ok else 1, "issueCounts": {"smallDeterminantCells": 0, "lowInterpolationWeightFaces": 0, "lowVolumeRatioFaces": 0, "concaveCells": 0}, "counts": {"cells": None if cells is None else int(cells.group(1))}},
        "artifacts": artifacts,
        "topology": {"family": "five-block-full-pipe-O-grid", "cellTypes": ["hex"], "prismTetInterface": False, "wallRepresentation": "64 fixed blockMesh arc facets; controlled topology reference, not CAD-exact geometry"},
    }


def _component_residual_summary(log: Path, *, axis: str) -> dict[str, Any]:
    """Retain the component-wise SIMPLE momentum residual history.

    OpenFOAM's linear-solver residual is dimensionless and especially noisy
    for an analytically zero transverse component.  It is therefore retained
    alongside velocity magnitudes rather than treated as a velocity error.
    """
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    records: dict[str, list[dict[str, float | int]]] = {name: [] for name in ("Ux", "Uy", "Uz")}
    current_time: int | None = None
    for line in text.splitlines():
        time_match = re.match(r"Time = (\d+)s", line.strip())
        if time_match:
            current_time = int(time_match.group(1))
            continue
        match = re.search(r"Solving for (U[xyz]), Initial residual = ([0-9.eE+-]+), Final residual = ([0-9.eE+-]+), No Iterations (\d+)", line)
        if match:
            component = match.group(1)
            records[component].append({"time": -1 if current_time is None else current_time, "initial": float(match.group(2)), "final": float(match.group(3)), "iterations": int(match.group(4))})
    axial = "Ux" if axis == "x" else "Uz"
    transverse = [name for name in records if name != axial]
    def summary(items: list[dict[str, float | int]]) -> dict[str, Any]:
        if not items:
            return {"samples": 0, "last": None, "maxInitial": None, "maxFinal": None}
        return {"samples": len(items), "last": items[-1], "maxInitial": max(float(item["initial"]) for item in items), "maxFinal": max(float(item["final"]) for item in items)}
    return {"axialComponent": axial, "transverseComponents": transverse, "components": {name: summary(items) for name, items in records.items()}}


def _internal_vector_statistics(path: Path, *, axis: str) -> dict[str, Any]:
    """Compute physical transverse-velocity statistics from the final U field."""
    if not path.is_file():
        return {"available": False}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next((index for index, line in enumerate(lines) if re.match(r"internalField\s+nonuniform\s+List<vector>", line.strip())), None)
    if start is None or start + 2 >= len(lines):
        return {"available": False}
    try:
        count = int(lines[start + 1].strip())
    except ValueError:
        return {"available": False}
    first = start + 3 if lines[start + 2].strip() == "(" else None
    if first is None or first + count > len(lines):
        return {"available": False}
    axial_index = 0 if axis == "x" else 2
    axial_sum_sq = transverse_sum_sq = 0.0
    max_axial = max_transverse = 0.0
    for line in lines[first:first + count]:
        values = line.strip().strip("()").split()
        if len(values) != 3:
            return {"available": False}
        vector = tuple(float(value) for value in values)
        axial_value = abs(vector[axial_index])
        transverse_value = math.sqrt(sum(value * value for index, value in enumerate(vector) if index != axial_index))
        axial_sum_sq += axial_value * axial_value
        transverse_sum_sq += transverse_value * transverse_value
        max_axial = max(max_axial, axial_value)
        max_transverse = max(max_transverse, transverse_value)
    rms_axial = math.sqrt(axial_sum_sq / count)
    rms_transverse = math.sqrt(transverse_sum_sq / count)
    return {"available": True, "cellCount": count, "axial": {"maxMPerS": max_axial, "rmsMPerS": rms_axial}, "transverse": {"maxMPerS": max_transverse, "rmsMPerS": rms_transverse, "rmsToAxialRatio": None if rms_axial == 0 else rms_transverse / rms_axial}}


def _wall_shear_balance(case: Path, *, spec: CadParabolicSmokeSpec, pressure_drop: float | None) -> dict[str, Any]:
    """Compare the retained wall viscous force with rho*Delta-p*pi*R^2."""
    force_files = sorted((case / "postProcessing" / "wallForces").glob("**/force*.dat"))
    if not force_files or pressure_drop is None:
        return {"available": False, "forceFiles": [str(path) for path in force_files]}
    rows = [line for line in force_files[-1].read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    vectors = re.findall(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", rows[-1]) if rows else []
    if len(vectors) < 2:
        return {"available": False, "forceFiles": [str(path) for path in force_files], "parseError": "could not read pressure and viscous force vectors"}
    pressure_force = tuple(float(value) for value in vectors[0])
    viscous_force = tuple(float(value) for value in vectors[1])
    axial_index = 0 if spec.axis == "x" else 2
    driving_force = spec.density_kg_m3 * pressure_drop * math.pi * spec.radius_m**2
    viscous_axial = viscous_force[axial_index]
    return {
        "available": True,
        "forceFile": str(force_files[-1]),
        "pressureForceN": pressure_force,
        "viscousForceN": viscous_force,
        "axialPressureDrivingForceN": driving_force,
        "axialWallViscousForceN": viscous_axial,
        # The `forces` function object reports force exerted by the fluid on
        # the selected wall; its axial sign is therefore opposite the wall
        # traction in the fluid momentum balance.  Compare magnitudes here.
        "relativeAxialImbalance": None if driving_force == 0 else abs(abs(viscous_axial) - abs(driving_force)) / abs(driving_force),
    }


def _write_diagnostics(case: Path, *, spec: CadParabolicSmokeSpec, exact_gate: dict[str, Any], output: Path) -> dict[str, Any]:
    times = [(int(path.name), path) for path in case.iterdir() if path.is_dir() and path.name.isdigit()]
    latest = max(times, default=(None, None), key=lambda item: item[0] if item[0] is not None else -1)[1]
    diagnostics = {
        "schema": DIAGNOSTIC_SCHEMA,
        "axis": spec.axis,
        "linearMomentumResiduals": _component_residual_summary(case / "log.foamRun", axis=spec.axis),
        "physicalVelocity": _internal_vector_statistics(latest / "U", axis=spec.axis) if latest is not None else {"available": False},
        "wallShearBalance": _wall_shear_balance(case, spec=spec, pressure_drop=exact_gate.get("qoi", {}).get("pressureDrop")),
        "artifacts": {"solverLog": str(case / "log.foamRun"), "finalU": None if latest is None else str(latest / "U")},
    }
    output.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return diagnostics


def run_reference(output_dir: Path, *, mesh_size_m: float = 0.000625, iteration_limit: int = 100, cross_section_cells_per_quadrant: int = 32, non_orthogonal_correctors: int = 2, linear_relative_tolerance: float = 0.0, u_relaxation: float = 1.0) -> dict[str, Any]:
    """Build, screen, and solve one structured all-hex reference level."""
    if iteration_limit < 1:
        raise ValueError("iteration limit must be positive")
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("refusing to overwrite a non-empty structured reference output")
    spec = reference_spec(cross_section_cells_per_quadrant)
    if mesh_size_m not in spec.mesh_sizes_m:
        raise ValueError("mesh size must be one of the frozen structured reference levels")
    mesh_dir = output_dir / "mesh"
    mesh_dir.joinpath("system").mkdir(parents=True)
    mesh_dir.joinpath("system", "blockMeshDict").write_text(open_boundary_ogrid_dict(spec, mesh_size_m), encoding="utf-8")
    mesh_dir.joinpath("system", "controlDict").write_text(
        "FoamFile { version 2.0; format ascii; class dictionary; location \"system\"; object controlDict; }\n"
        "application blockMesh; startFrom startTime; startTime 0; stopAt endTime; endTime 0; deltaT 1; "
        "writeControl timeStep; writeInterval 1; writeFormat ascii; writePrecision 12; runTimeModifiable false;\n",
        encoding="utf-8",
    )
    block_status = _run(["blockMesh", "-case", str(mesh_dir)], output_dir / "artifacts" / "blockMesh.log")
    check_status = _run(["checkMesh", "-case", str(mesh_dir), "-allGeometry", "-allTopology"], output_dir / "artifacts" / "checkMesh.log") if block_status == 0 else 127
    gate = _mesh_gate(mesh_dir, output_dir / "artifacts" / "checkMesh.log")
    gate["commandStatus"] = {"blockMesh": block_status, "checkMesh": check_status}
    gate_path = output_dir / "artifacts" / "mesh-gate-report.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result: dict[str, Any] = {"schema": SCHEMA, "scientificStatus": "analysis-only", "spec": asdict(spec), "meshSizeM": mesh_size_m, "crossSectionCellsPerQuadrant": cross_section_cells_per_quadrant, "iterationLimit": iteration_limit, "meshGate": {"accepted": gate["accepted"], "path": str(gate_path)}, "exactInitGate": None}
    if not gate["accepted"]:
        result["status"] = "rejected_mesh_gate"
        (output_dir / "artifacts" / "reference-report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    case = output_dir / "case"
    smoke_spec = CadParabolicSmokeSpec(axis="x")
    materialize_cad_parabolic_smoke_case(
        case,
        source_poly_mesh=mesh_dir / "constant" / "polyMesh",
        immutable_gate_report=gate_path,
        spec=smoke_spec,
        iteration_limit=iteration_limit,
        diagnostics=True,
        non_orthogonal_correctors=non_orthogonal_correctors,
        linear_relative_tolerance=linear_relative_tolerance,
        u_relaxation=u_relaxation,
    )
    _assert_exact_iteration_limit(case / "system" / "controlDict", iteration_limit)
    statuses = {"writeCellCentres": _run(["foamPostProcess", "-case", str(case), "-func", "writeCellCentres", "-time", "0"], case / "log.writeCellCentres"), "foamRun": None}
    if statuses["writeCellCentres"] == 0:
        exact = subprocess.run([sys.executable, "-m", "server.flowlab.cad_parabolic_smoke", "--initialize-exact", str(case)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        (case / "exact-init.json").write_text(exact.stdout, encoding="utf-8")
        if exact.returncode == 0:
            statuses["foamRun"] = _run(["foamRun", "-case", str(case), "-solver", "incompressibleFluid"], case / "log.foamRun")
    exact_gate = _exact_gate({"smoke": case, "solverLog": case / "log.foamRun", "cellCentresLog": case / "log.writeCellCentres", "exactInit": case / "exact-init.json"}, solver_status=int(statuses["foamRun"] if statuses["foamRun"] is not None else 127), iteration_limit=iteration_limit)
    exact_path = output_dir / "artifacts" / "exact-init-gate-report.json"
    exact_path.write_text(json.dumps(exact_gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    diagnostics_path = output_dir / "artifacts" / "structured-diagnostics.json"
    diagnostics = _write_diagnostics(case, spec=smoke_spec, exact_gate=exact_gate, output=diagnostics_path)
    result.update({"nonOrthogonalCorrectors": non_orthogonal_correctors, "linearRelativeTolerance": linear_relative_tolerance, "uRelaxation": u_relaxation, "status": "accepted_exact_init_gate" if exact_gate["accepted"] else "rejected_exact_init_gate", "exactInitGate": {"accepted": exact_gate["accepted"], "path": str(exact_path), "qoi": exact_gate["qoi"]}, "diagnostics": {"path": str(diagnostics_path), "wallShearAvailable": diagnostics["wallShearBalance"]["available"]}, "commandStatus": statuses})
    (output_dir / "artifacts" / "reference-report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mesh-size-m", type=float, default=0.000625)
    parser.add_argument("--iteration-limit", type=int, default=100)
    parser.add_argument("--cross-section-cells-per-quadrant", type=int, default=32)
    parser.add_argument("--non-orthogonal-correctors", type=int, default=2)
    parser.add_argument("--linear-relative-tolerance", type=float, default=0.0)
    parser.add_argument("--u-relaxation", type=float, default=1.0)
    args = parser.parse_args()
    result = run_reference(args.output_dir, mesh_size_m=args.mesh_size_m, iteration_limit=args.iteration_limit, cross_section_cells_per_quadrant=args.cross_section_cells_per_quadrant, non_orthogonal_correctors=args.non_orthogonal_correctors, linear_relative_tolerance=args.linear_relative_tolerance, u_relaxation=args.u_relaxation)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "accepted_exact_init_gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
