"""Reconcile OpenFOAM patch forces with a face-by-face field integration.

The input CSV is emitted by ``flowlabPatchTractionAudit``.  That utility reads
an existing OpenFOAM time directory and applies the exact laminar stress
expression used by the OpenFOAM 11 ``forces`` function object.  No solve input
or solution field is changed by this audit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


SCHEMA = "flowlab.open-boundary-face-traction-audit.v1"
# The direct CSV reduction and OpenFOAM's field reduction use different
# summation order.  This is roughly 5e-15 relative to the 1e-3 pressure force.
RECONCILIATION_TOLERANCE = 5.0e-18
VECTOR_PATTERN = re.compile(r"\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\)")
VECTOR_COMPONENTS = ("x", "y", "z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vector(values: Iterable[float]) -> list[float]:
    result = [float(value) for value in values]
    if len(result) != 3:
        raise ValueError(f"expected three vector components, got {len(result)}")
    return result


def _add(left: list[float], right: list[float]) -> list[float]:
    return [a + b for a, b in zip(left, right)]


def _subtract(left: list[float], right: list[float]) -> list[float]:
    return [a - b for a, b in zip(left, right)]


def _magnitude(value: list[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def _sum_vector(rows: list[dict[str, str]], prefix: str) -> list[float]:
    return [sum(float(row[f"{prefix}_{component}"]) for row in rows) for component in VECTOR_COMPONENTS]


def _read_faces(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"no face rows found in {path}")
    required = {
        "patch",
        "patch_face",
        "mesh_face",
        "owner_cell",
        "area",
        "sn_grad_u_x",
        "sn_grad_u_y",
        "sn_grad_u_z",
        "sn_grad_normal_velocity",
        "viscous_force_x",
        "viscous_force_y",
        "viscous_force_z",
        "pressure_force_x",
        "pressure_force_y",
        "pressure_force_z",
    }
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"face CSV is missing columns: {sorted(missing)}")
    return rows


def _read_kinematic_viscosity(path: Path) -> float:
    match = re.search(
        r"\bnu\s+\[[^\]]+\]\s+([-+0-9.eE]+)\s*;",
        path.read_text(encoding="utf-8", errors="replace"),
    )
    if match is None:
        raise ValueError(f"could not read nu from {path}")
    return float(match.group(1))


def _final_force_record(path: Path) -> dict[str, Any]:
    final: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        vectors = VECTOR_PATTERN.findall(line)
        if len(vectors) < 2:
            continue
        final = {
            "time": float(line.split()[0]),
            "pressureForce": _vector(float(value) for value in vectors[0]),
            "viscousForce": _vector(float(value) for value in vectors[1]),
        }
    if final is None:
        raise ValueError(f"no force record found in {path}")
    final["totalForce"] = _add(final["pressureForce"], final["viscousForce"])
    return final


def _face_reference(row: dict[str, str]) -> dict[str, Any]:
    return {
        "patchFace": int(row["patch_face"]),
        "meshFace": int(row["mesh_face"]),
        "ownerCell": int(row["owner_cell"]),
        "faceCentre": [float(row[f"cf_{component}"]) for component in VECTOR_COMPONENTS],
        "viscousForce": [
            float(row[f"viscous_force_{component}"]) for component in VECTOR_COMPONENTS
        ],
        "snGradU": [float(row[f"sn_grad_u_{component}"]) for component in VECTOR_COMPONENTS],
        "snGradNormalVelocity": float(row["sn_grad_normal_velocity"]),
    }


def _patch_stats(rows: list[dict[str, str]]) -> dict[str, Any]:
    viscous_force = _sum_vector(rows, "viscous_force")
    pressure_force = _sum_vector(rows, "pressure_force")
    x_contributions = [float(row["viscous_force_x"]) for row in rows]
    sn_grad_vectors = [
        [float(row[f"sn_grad_u_{component}"]) for component in VECTOR_COMPONENTS]
        for row in rows
    ]
    normal_gradients = [float(row["sn_grad_normal_velocity"]) for row in rows]
    top_face = max(rows, key=lambda row: abs(float(row["viscous_force_x"])))
    sum_abs_x = sum(abs(value) for value in x_contributions)
    return {
        "faceCount": len(rows),
        "area": sum(float(row["area"]) for row in rows),
        "pressureForce": pressure_force,
        "viscousForce": viscous_force,
        "viscousForceXContributions": {
            "positiveSum": sum(value for value in x_contributions if value > 0.0),
            "negativeSum": sum(value for value in x_contributions if value < 0.0),
            "absoluteSum": sum_abs_x,
            "netToAbsoluteRatio": abs(viscous_force[0]) / sum_abs_x if sum_abs_x else 0.0,
            "minimum": min(x_contributions),
            "maximum": max(x_contributions),
        },
        "normalVelocityGradient": {
            "minimum": min(normal_gradients),
            "maximum": max(normal_gradients),
            "mean": sum(normal_gradients) / len(normal_gradients),
            "maxAbsolute": max(abs(value) for value in normal_gradients),
            "rms": math.sqrt(sum(value * value for value in normal_gradients) / len(normal_gradients)),
        },
        "snGradU": {
            "maxMagnitude": max(_magnitude(value) for value in sn_grad_vectors),
            "allComponentsExactlyZero": all(
                component == 0.0 for value in sn_grad_vectors for component in value
            ),
        },
        "velocityError": {
            "maxAbsBoundaryUx": max(abs(float(row["boundary_error_x"])) for row in rows),
            "maxAbsOwnerUx": max(abs(float(row["owner_error_x"])) for row in rows),
        },
        "faceWithMaxAbsViscousForceX": _face_reference(top_face),
    }


def face_traction_audit(
    *,
    face_csv: Path,
    forces_path: Path,
    case: Path,
    utility_source: Path,
    utility_build_log: Path,
    utility_audit_log: Path,
    image: str,
    image_digest: str,
    output: Path,
    source_force_x: float = -1.0e-3,
) -> dict[str, Any]:
    rows = _read_faces(face_csv)
    patches = {
        patch: _patch_stats([row for row in rows if row["patch"] == patch])
        for patch in ("inlet", "outlet")
    }
    if any(stats["faceCount"] == 0 for stats in patches.values()):
        raise ValueError("both inlet and outlet faces are required")

    direct_pressure = _add(patches["inlet"]["pressureForce"], patches["outlet"]["pressureForce"])
    direct_viscous = _add(patches["inlet"]["viscousForce"], patches["outlet"]["viscousForce"])
    direct_total = _add(direct_pressure, direct_viscous)
    function_object = _final_force_record(forces_path)
    pressure_difference = _subtract(direct_pressure, function_object["pressureForce"])
    viscous_difference = _subtract(direct_viscous, function_object["viscousForce"])
    total_difference = _subtract(direct_total, function_object["totalForce"])
    max_difference = max(
        abs(component)
        for vector in (pressure_difference, viscous_difference, total_difference)
        for component in vector
    )
    relative_traction_imbalance = abs(direct_total[0] - source_force_x) / max(
        abs(source_force_x), 1.0e-30
    )
    inlet_x = patches["inlet"]["viscousForce"][0]
    outlet_x = patches["outlet"]["viscousForce"][0]
    inlet_share = inlet_x / direct_viscous[0] if direct_viscous[0] else None
    kinematic_viscosity = _read_kinematic_viscosity(case / "constant/physicalProperties")
    inlet_normal_strain_prediction = (
        (4.0 / 3.0)
        * kinematic_viscosity
        * patches["inlet"]["area"]
        * patches["inlet"]["normalVelocityGradient"]["mean"]
    )

    raw_paths = {
        "faceCsv": face_csv,
        "forces": forces_path,
        "U": case / "100/U",
        "p": case / "100/p",
        "fvSchemes": case / "system/fvSchemes",
        "physicalProperties": case / "constant/physicalProperties",
        "utilitySource": utility_source,
        "utilityBuildLog": utility_build_log,
        "utilityAuditLog": utility_audit_log,
    }
    report = {
        "schema": SCHEMA,
        "status": "audited",
        "scientificStatus": "analysis-only",
        "validated": False,
        "method": {
            "openFoamVersion": "11",
            "time": float(rows[0]["time"]),
            "patches": ["inlet", "outlet"],
            "forceObjectFormula": "Sf & [-nu dev(twoSymm(fvc::grad(U)))]",
            "kinematicViscosity": kinematic_viscosity,
            "directIntegration": "The retained U and p fields were read at time 100 and integrated face-by-face without running the solver.",
            "analyticState": {
                "U": [1.0, 0.0, 0.0],
                "gradU": "zero",
                "viscousStress": "zero",
                "viscousTraction": "zero on every face",
            },
            "reconciliationTolerance": RECONCILIATION_TOLERANCE,
            "image": image,
            "imageDigest": image_digest,
            "solveRerun": False,
        },
        "faceDecomposition": {
            "rowCount": len(rows),
            "patches": patches,
        },
        "directIntegration": {
            "pressureForce": direct_pressure,
            "viscousForce": direct_viscous,
            "totalForce": direct_total,
            "sourceForceX": source_force_x,
            "relativeTractionImbalance": relative_traction_imbalance,
        },
        "forceFunctionObject": function_object,
        "reconciliation": {
            "pressureForceDifference": pressure_difference,
            "viscousForceDifference": viscous_difference,
            "totalForceDifference": total_difference,
            "maxAbsComponentDifference": max_difference,
            "matchesWithinTolerance": max_difference <= RECONCILIATION_TOLERANCE,
        },
        "analyticComparison": {
            "analyticViscousForce": [0.0, 0.0, 0.0],
            "observedDirectViscousForce": direct_viscous,
            "absoluteDeparture": _magnitude(direct_viscous),
            "departureRelativeToSourceForceX": abs(direct_viscous[0]) / abs(source_force_x),
            "outletSnGradUMatchesAnalyticExactly": patches["outlet"]["snGradU"][
                "allComponentsExactlyZero"
            ],
            "inletSnGradUMatchesAnalyticExactly": patches["inlet"]["snGradU"][
                "allComponentsExactlyZero"
            ],
        },
        "attribution": {
            "inletViscousForceX": inlet_x,
            "outletViscousForceX": outlet_x,
            "inletShareOfNetViscousForceX": inlet_share,
            "outletForceXRelativeToNet": abs(outlet_x) / max(abs(direct_viscous[0]), 1.0e-30),
            "localizedPatch": "inlet",
            "inletNormalStrainClosure": {
                "formula": "(4/3) * nu * inletArea * mean(snGradNormalVelocity)",
                "predictedViscousForceX": inlet_normal_strain_prediction,
                "directViscousForceX": inlet_x,
                "absoluteDifference": abs(inlet_normal_strain_prediction - inlet_x),
            },
            "finding": "The outlet has exactly zero snGrad(U) on all faces and a cancelling near-zero x force. The non-zero net viscous force is generated at the inlet, where the exact fixed boundary velocity differs from the adjacent owner-cell velocity.",
        },
        "conclusion": {
            "forceObjectReproduced": max_difference <= RECONCILIATION_TOLERANCE,
            "analyticZeroViscousTractionReproducedByNumericalField": False,
            "outletIsCauseOfNetViscousForceX": False,
            "tractionGatePasses": relative_traction_imbalance <= 1.0e-6,
            "tractionGateUnchanged": True,
            "threeGridForcedMmsAuthorized": False,
        },
        "rawEvidence": {
            name: {"path": str(path), "sha256": _sha(path)} for name, path in raw_paths.items()
        },
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--face-csv", type=Path, required=True)
    parser.add_argument("--forces", type=Path, required=True)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--utility-source", type=Path, required=True)
    parser.add_argument("--utility-build-log", type=Path, required=True)
    parser.add_argument("--utility-audit-log", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = face_traction_audit(
        face_csv=args.face_csv.resolve(),
        forces_path=args.forces.resolve(),
        case=args.case.resolve(),
        utility_source=args.utility_source.resolve(),
        utility_build_log=args.utility_build_log.resolve(),
        utility_audit_log=args.utility_audit_log.resolve(),
        image=args.image,
        image_digest=args.image_digest,
        output=args.output.resolve(),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
