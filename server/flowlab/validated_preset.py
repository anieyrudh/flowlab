"""Fail-closed runnable case for FlowLab's accepted open-boundary regime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .adapters import EVIDENCE_CAPABILITY_PATH, add_case_manifest
from .open_boundary_laminar_force_benchmark import (
    COARSE_WALL_VISCOUS_RELATIVE_LIMIT,
    FORCE_RECONCILIATION_ABSOLUTE_LIMIT,
    ITERATIONS,
    MASS_LIMIT,
    PlanePoiseuille,
    PRESSURE_FORCE_RELATIVE_LIMIT,
    _case_files,
)
from .schemas import EvidenceCapability, SolverCase
from .validated_benchmark import OPEN_BOUNDARY_BENCHMARK_ID, validated_benchmark_registry


PRESET_SCHEMA = "flowlab.validated_open_boundary_preset.v1"
PRESET_MANIFEST_PATH = "evidence/validated-open-boundary-preset.json"
VALIDATED_RESULT_PATH = "postProcessing/validated-benchmark-summary.json"
CELLS_PER_AXIS = 12
_ROOT = Path(__file__).resolve().parents[2]
_UTILITY_ROOT = _ROOT / "benchmarks/tools/flowlabPatchTractionAudit"


_INITIALIZE_EXACT = r'''#!/usr/bin/env python3
from pathlib import Path
import re

G = 0.02
NU = 0.01
PATCHES = ("inlet", "outlet", "yMin", "yMax", "zMin", "zMax")
FLOW_PATCHES = ("inlet", "outlet", "yMin", "yMax")

def read_internal(path):
    stream = path.open(encoding="utf-8")
    for line in stream:
        if line.strip().startswith("internalField"):
            break
    count = int(next(line.strip() for line in stream if line.strip()))
    for line in stream:
        if line.strip() == "(":
            break
    values = []
    for _ in range(count):
        values.append(tuple(float(value) for value in stream.readline().strip().strip("()").split()))
    return values

def read_patches(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    text = text[text.index("boundaryField"):]
    result = {}
    for patch in FLOW_PATCHES:
        match = re.search(rf"\b{patch}\s*\{{.*?value\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\)\s*;", text, re.S)
        if match is None:
            raise RuntimeError(f"missing calculated face centres for {patch}")
        values = [tuple(float(v) for v in row) for row in re.findall(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", match.group(2))]
        if len(values) != int(match.group(1)):
            raise RuntimeError(f"invalid face-centre count for {patch}")
        result[patch] = values
    return result

def vector_field(values):
    rows = "\n".join(f"({x:.17g} {y:.17g} {z:.17g})" for x, y, z in values)
    return f"nonuniform List<vector>\n{len(values)}\n(\n{rows}\n)"

def scalar_field(values):
    rows = "\n".join(f"{value:.17g}" for value in values)
    return f"nonuniform List<scalar>\n{len(values)}\n(\n{rows}\n)"

def velocity(point):
    y = point[1]
    return (G / (2.0 * NU) * y * (1.0 - y), 0.0, 0.0)

def pressure(point):
    return G * (1.0 - point[0])

def header(location, name, cls):
    return f"""FoamFile
{{
    version 2.0;
    format ascii;
    class {cls};
    location "{location}";
    object {name};
}}
"""

root = Path(".")
centres = read_internal(root / "0/C")
faces = read_patches(root / "0/C")
u_patches = []
p_patches = []
for patch in PATCHES:
    if patch in ("zMin", "zMax"):
        u_patches.append(f" {patch} {{ type symmetryPlane; }}")
        p_patches.append(f" {patch} {{ type symmetryPlane; }}")
        continue
    u_values = vector_field([velocity(point) for point in faces[patch]])
    if patch in ("inlet", "outlet"):
        u_patches.append(f" {patch} {{ type pressureInletOutletVelocity; phi phi; tangentialVelocity uniform (0 0 0); value {u_values}; }}")
        p_values = scalar_field([pressure(point) for point in faces[patch]])
        p_patches.append(f" {patch} {{ type fixedValue; value {p_values}; }}")
    else:
        u_patches.append(f" {patch} {{ type fixedValue; value {u_values}; }}")
        p_values = scalar_field([pressure(point) for point in faces[patch]])
        p_patches.append(f" {patch} {{ type fixedFluxPressure; value {p_values}; gradient uniform 0; }}")

(root / "0/U").write_text(
    header("0", "U", "volVectorField")
    + "dimensions [0 1 -1 0 0 0 0];\n"
    + f"internalField {vector_field([velocity(point) for point in centres])};\n"
    + "boundaryField {\n" + "\n".join(u_patches) + "\n}\n",
    encoding="utf-8",
)
(root / "0/p").write_text(
    header("0", "p", "volScalarField")
    + "dimensions [0 2 -2 0 0 0 0];\n"
    + f"internalField {scalar_field([pressure(point) for point in centres])};\n"
    + "boundaryField {\n" + "\n".join(p_patches) + "\n}\n",
    encoding="utf-8",
)
print("FLOWLAB_VALIDATED_PRESET exact analytic U and p initialization complete")
'''


_SUMMARIZE = r'''#!/usr/bin/env python3
from pathlib import Path
import csv
import json
import math
import re
import sys

G = 0.02
MASS_LIMIT = 1.0e-8
FORCE_ABS_LIMIT = 1.0e-10
PRESSURE_REL_LIMIT = 1.0e-8
WALL_VISCOUS_REL_LIMIT = 0.06
OPEN_VISCOUS_REL_LIMIT = 1.0e-8
FACE_TRACTION_REL_LIMIT = 0.03

def norm(value):
    return math.sqrt(sum(component * component for component in value))

def difference(left, right):
    return norm(tuple(a - b for a, b in zip(left, right)))

def add(values):
    values = list(values)
    return [sum(row[index] for row in values) for index in range(3)]

def force(path):
    rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    vectors = re.findall(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", rows[-1])
    if len(vectors) < 2:
        raise RuntimeError(f"invalid force output: {path}")
    return {"pressure": [float(v) for v in vectors[0]], "viscous": [float(v) for v in vectors[1]]}

def latest_scalar(path):
    rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return float(rows[-1].split()[-1])

root = Path(".")
csv_path = root / "postProcessing/directFaceIntegration/1000/face-traction.csv"
with csv_path.open(newline="", encoding="utf-8") as stream:
    rows = list(csv.DictReader(stream))

groups = {"open": {"inlet", "outlet"}, "walls": {"yMin", "yMax"}, "all": {"inlet", "outlet", "yMin", "yMax", "zMin", "zMax"}}
direct = {"faceCount": len(rows)}
max_traction_error = 0.0
for row in rows:
    y = float(row["cf_y"])
    normal = [float(row[f"n_{axis}"]) for axis in "xyz"]
    du_dy = G / (2.0 * 0.01) * (1.0 - 2.0 * y)
    shear = -0.01 * du_dy
    analytic_traction = [normal[1] * shear, normal[0] * shear, 0.0]
    actual = [float(row[f"traction_{axis}"]) for axis in "xyz"]
    max_traction_error = max(max_traction_error, difference(actual, analytic_traction))
for name, patches in groups.items():
    selected = [row for row in rows if row["patch"] in patches]
    direct[name] = {
        "pressure": add([float(row[f"pressure_force_{axis}"]) for axis in "xyz"] for row in selected),
        "viscous": add([float(row[f"viscous_force_{axis}"]) for axis in "xyz"] for row in selected),
    }
direct["maxFaceViscousTractionRelativeError"] = max_traction_error / (G / 2.0)

forces = {
    "open": force(sorted((root / "postProcessing/forcesOpen").glob("**/forces.dat"))[-1]),
    "walls": force(sorted((root / "postProcessing/forcesWalls").glob("**/forces.dat"))[-1]),
}
inlet_flux = latest_scalar(sorted((root / "postProcessing/inletFlux").glob("**/surfaceFieldValue.dat"))[-1])
outlet_flux = latest_scalar(sorted((root / "postProcessing/outletFlux").glob("**/surfaceFieldValue.dat"))[-1])
mass_imbalance = abs(inlet_flux + outlet_flux) / max(abs(inlet_flux), abs(outlet_flux), 1.0e-30)
analytic = {
    "openPressureForce": [-G, 0.0, 0.0],
    "wallViscousForce": [G, 0.0, 0.0],
    "openViscousForce": [0.0, 0.0, 0.0],
}
errors = {
    "openFoamVsDirectAbsolute": max(difference(forces["open"][key], direct["open"][key]) for key in ("pressure", "viscous")),
    "wallOpenFoamVsDirectAbsolute": max(difference(forces["walls"][key], direct["walls"][key]) for key in ("pressure", "viscous")),
    "analyticPressureForceRelative": difference(direct["open"]["pressure"], analytic["openPressureForce"]) / G,
    "analyticWallViscousRelative": difference(direct["walls"]["viscous"], analytic["wallViscousForce"]) / G,
    "analyticOpenViscousRelative": difference(direct["open"]["viscous"], analytic["openViscousForce"]) / G,
    "faceViscousTractionRelative": direct["maxFaceViscousTractionRelativeError"],
    "massRelativeImbalance": mass_imbalance,
}
checks = {
    "openFoamForcesMatchDirectIntegration": max(errors["openFoamVsDirectAbsolute"], errors["wallOpenFoamVsDirectAbsolute"]) <= FORCE_ABS_LIMIT,
    "analyticPressureForceMatches": errors["analyticPressureForceRelative"] <= PRESSURE_REL_LIMIT,
    "analyticWallViscousTractionMatches": errors["analyticWallViscousRelative"] <= WALL_VISCOUS_REL_LIMIT,
    "analyticZeroOpenViscousTractionMatches": errors["analyticOpenViscousRelative"] <= OPEN_VISCOUS_REL_LIMIT,
    "faceViscousTractionMatches": errors["faceViscousTractionRelative"] <= FACE_TRACTION_REL_LIMIT,
    "massBalancePasses": errors["massRelativeImbalance"] <= MASS_LIMIT,
}
passed = all(checks.values())
report = {
    "schema": "flowlab.validated_open_boundary_run.v1",
    "benchmarkId": "laminar-open-boundary-all-hex-v1",
    "status": "accepted" if passed else "rejected",
    "allChecksPassed": passed,
    "cellsPerAxis": 12,
    "scope": "Bounded plane-Poiseuille reproduction only; not a general or production CFD claim.",
    "checks": checks,
    "errors": errors,
    "openFoamForces": forces,
    "directFaceIntegration": direct,
    "analytic": analytic,
    "flux": {"inlet": inlet_flux, "outlet": outlet_flux},
    "artifacts": {"faceDecomposition": str(csv_path), "fields": "VTK/*"},
}
output = root / "postProcessing/validated-benchmark-summary.json"
output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("FLOWLAB_VALIDATED_PRESET " + ("all runtime gates passed" if passed else "runtime gates failed"))
print(json.dumps({"checks": checks, "errors": errors}, sort_keys=True))
sys.exit(0 if passed else 2)
'''


def _allrun() -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail
echo "FlowLab validated preset: immutable coarse {CELLS_PER_AXIS}^3 plane-Poiseuille case"
blockMesh
checkMesh -allGeometry -allTopology
foamPostProcess -func writeCellCentres -time 0
python3 initialize_exact.py
foamRun -solver incompressibleFluid
mkdir -p postProcessing/directFaceIntegration/{ITERATIONS}
wmake tools/flowlabPatchTractionAudit
flowlabPatchTractionAudit -time {ITERATIONS} -allFlowPatches -output postProcessing/directFaceIntegration/{ITERATIONS}/face-traction.csv
foamToVTK -ascii -latestTime
python3 summarize_validated_run.py
'''


def _utility_files() -> dict[str, str]:
    paths = {
        "tools/flowlabPatchTractionAudit/flowlabPatchTractionAudit.C": _UTILITY_ROOT / "flowlabPatchTractionAudit.C",
        "tools/flowlabPatchTractionAudit/Make/files": _UTILITY_ROOT / "Make/files",
        "tools/flowlabPatchTractionAudit/Make/options": _UTILITY_ROOT / "Make/options",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("Validated traction utility source is unavailable: " + ", ".join(missing))
    return {target: source.read_text(encoding="utf-8") for target, source in paths.items()}


def _accepted_registry_entry() -> dict[str, Any]:
    registry = validated_benchmark_registry()
    entry = next((item for item in registry["benchmarks"] if item["id"] == OPEN_BOUNDARY_BENCHMARK_ID), None)
    if not isinstance(entry, dict) or entry.get("promotionBlocked") is not False:
        raise ValueError("The bounded open-boundary evidence does not authorize a runnable preset")
    return entry


def _preset_manifest(entry: dict[str, Any]) -> dict[str, Any]:
    spec = PlanePoiseuille()
    return {
        "schema": PRESET_SCHEMA,
        "benchmarkId": OPEN_BOUNDARY_BENCHMARK_ID,
        "eligibility": "exact-immutable-match-only",
        "cellsPerAxis": CELLS_PER_AXIS,
        "solver": "OpenFOAM 11 incompressibleFluid",
        "boundaryContract": {
            "velocity": "pressureInletOutletVelocity on inlet/outlet with exact analytic initialization",
            "pressure": "exact fixedValue inlet/outlet kinematic pressure; fixedFluxPressure walls initialized at exact pressure",
        },
        "definition": spec.manifest(),
        "runtimeChecks": [
            "checkMesh",
            "OpenFOAM forces versus direct face integration",
            "analytic pressure force",
            "analytic wall viscous traction",
            "analytic zero integrated open-boundary viscous traction",
            "mass balance",
        ],
        "immutableEvidence": entry["evidence"],
        "limits": entry["limits"],
    }


def _validated_capability(entry: dict[str, Any]) -> EvidenceCapability:
    return EvidenceCapability(
        status="validated-benchmark",
        promotionBlocked=False,
        blockingReasons=[],
        validationPath=["This case is eligible only while every generated file exactly matches the immutable preset contract."],
        allowedClaims=["Reproduction of the bounded validated plane-Poiseuille preset within its stated envelope."],
        prohibitedClaims=["general open-boundary validation", "production-ready", "production CFD", "validated for arbitrary geometry"],
        evidenceId=OPEN_BOUNDARY_BENCHMARK_ID,
        immutableEvidence=entry["evidence"],
    )


def build_validated_open_boundary_case() -> SolverCase:
    """Mint the sole product case eligible for the bounded validated label."""
    entry = _accepted_registry_entry()
    spec = PlanePoiseuille()
    files = {
        **_case_files(CELLS_PER_AXIS, spec),
        **_utility_files(),
        "Allrun": _allrun(),
        "initialize_exact.py": _INITIALIZE_EXACT,
        "summarize_validated_run.py": _SUMMARIZE,
        PRESET_MANIFEST_PATH: json.dumps(_preset_manifest(entry), indent=2, sort_keys=True) + "\n",
    }
    capability = _validated_capability(entry)
    files[EVIDENCE_CAPABILITY_PATH] = json.dumps(capability.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    case = SolverCase(
        projectName="Validated laminar open-boundary preset",
        solver="openfoam",
        advancedMode="incompressible-navier-stokes",
        status="generated",
        files=files,
        runCommand=["bash", "Allrun"],
        provenance=[
            "Minted by the dedicated validated-preset endpoint; generic FlowLab case generation remains experimental.",
            "Exact coarse 12^3 member of the accepted 12/24/48 all-hex refinement family.",
            "Exact analytic initialization, force function objects, direct face integration, and analytic force checks execute on every run.",
            "Scope is bounded plane-Poiseuille reproduction only and excludes production-CFD claims.",
        ],
        evidenceCapability=capability,
    )
    return add_case_manifest(case)


def is_validated_open_boundary_case(case: SolverCase) -> bool:
    return (
        case.evidenceCapability.status == "validated-benchmark"
        and case.evidenceCapability.evidenceId == OPEN_BOUNDARY_BENCHMARK_ID
        and PRESET_MANIFEST_PATH in case.files
    )


def validate_validated_open_boundary_case(case: SolverCase) -> list[str]:
    """Reject any mutation before a case can retain validated eligibility."""
    if not is_validated_open_boundary_case(case):
        return ["Case is not eligible for the validated open-boundary preset."]
    expected = build_validated_open_boundary_case()
    issues: list[str] = []
    if case.projectName != expected.projectName or case.solver != expected.solver or case.advancedMode != expected.advancedMode:
        issues.append("Validated preset identity was modified.")
    if case.runCommand != expected.runCommand or case.status != expected.status:
        issues.append("Validated preset execution contract was modified.")
    if case.evidenceCapability != expected.evidenceCapability:
        issues.append("Validated preset evidence capability was modified.")
    issues.extend(immutable_preset_file_issues(case.files, expected.files))
    return issues


def immutable_preset_file_issues(
    actual: dict[str, str], expected: dict[str, str]
) -> list[str]:
    """Return exact-file contract failures without requiring promotion state."""
    issues: list[str] = []
    if set(actual) != set(expected):
        issues.append("Validated preset file set does not exactly match the immutable contract.")
    for path in sorted(set(actual) & set(expected)):
        if actual[path] != expected[path]:
            issues.append(
                f"Validated preset file `{path}` does not match the immutable contract."
            )
    return issues


def read_validated_open_boundary_result(case_dir: Path) -> dict[str, Any] | None:
    path = case_dir / VALIDATED_RESULT_PATH
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict) or result.get("schema") != "flowlab.validated_open_boundary_run.v1":
        return None
    return result


def validated_result_error(result: dict[str, Any] | None) -> str | None:
    if result is None:
        return "Validated preset completed without its required structured force and field gate report."
    checks = result.get("checks")
    if result.get("status") != "accepted" or result.get("allChecksPassed") is not True:
        failed = [name for name, passed in checks.items() if not passed] if isinstance(checks, dict) else []
        return "Validated preset runtime gates failed" + (": " + ", ".join(failed) if failed else ".")
    return None


def preset_contract_sha256() -> str:
    case = build_validated_open_boundary_case()
    manifest = case.files[PRESET_MANIFEST_PATH].encode("utf-8")
    return hashlib.sha256(manifest).hexdigest()
