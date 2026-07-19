"""Fail-closed evidence gates for experimental immutable-surface mesh runs.

These helpers are deliberately separate from the meshing implementation.  A
candidate builder supplies the paths it produced; this module establishes
whether its retained MSH2, conversion and OpenFOAM evidence are sufficient to
advance to the next *analysis-only* gate.  It never promotes a CFD mesh.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .gmsh_immutable_surface_probe import msh2_surface_fingerprint, tetrahedron_count


_NUMBER = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
_CHECKMESH_COUNT_RE = re.compile(r"^\s*(points|faces|internal faces|cells|boundary patches)\s*:\s*(\d+)\s*$", re.IGNORECASE)
_CHECKMESH_FAILED_RE = re.compile(r"\bFailed\s+(\d+)\s+mesh checks?\.", re.IGNORECASE)
_CHECKMESH_MIN_VOLUME_RE = re.compile(rf"\bMin volume\s*=\s*({_NUMBER})", re.IGNORECASE)
_CHECKMESH_NONORTH_RE = re.compile(rf"\bMesh non-orthogonality Max:\s*({_NUMBER})\s+average:\s*({_NUMBER})", re.IGNORECASE)
_CHECKMESH_SKEW_RE = re.compile(rf"\bMax skewness\s*=\s*({_NUMBER})", re.IGNORECASE)
_CHECKMESH_ASPECT_RE = re.compile(rf"\bMax aspect ratio:\?\s*({_NUMBER})|\bMax aspect ratio\s*=\s*({_NUMBER})", re.IGNORECASE)
_CHECKMESH_DETERMINANT_RE = re.compile(rf"\bCell determinant \(wellposedness\)\s*:\s*minimum:\s*({_NUMBER})\s+average:\s*({_NUMBER})", re.IGNORECASE)
_CHECKMESH_SEVERE_NONORTH_RE = re.compile(r"Number of severely non-orthogonal .*?faces:\s*(\d+)", re.IGNORECASE)
_CHECKMESH_SMALL_DETERMINANT_RE = re.compile(r"Cells with small determinant .*?number of cells:\s*(\d+)", re.IGNORECASE)
_CHECKMESH_LOW_WEIGHT_RE = re.compile(r"Faces with small interpolation weight .*?number of faces:\s*(\d+)", re.IGNORECASE)
_CHECKMESH_LOW_VOLUME_RATIO_RE = re.compile(r"Faces with small volume ratio .*?number of faces:\s*(\d+)", re.IGNORECASE)
_CHECKMESH_CONCAVE_RE = re.compile(r"Concave cells? .*?(?:number of cells|cells)\s*:?\s*(\d+)", re.IGNORECASE)
_GMSH_MAPPING_RE = re.compile(r"Mapping region\s+(\d+)\s+to Foam\s+(patch|cellZone)\s+(\d+)", re.IGNORECASE)
_GMSH_PATCH_NAME_RE = re.compile(r"Patch\s+(\d+)\s+gets name\s+([^\s]+)", re.IGNORECASE)
_GMSH_CELLZONE_NAME_RE = re.compile(r"Writing zone\s+(\d+)\s+to cellZone\s+([^\s]+)", re.IGNORECASE)


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _finite(value: str) -> float | None:
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def parse_checkmesh_log(lines: Iterable[str]) -> dict[str, Any]:
    """Parse the quality facts needed by the immutable-surface acceptance gate.

    The parser deliberately distinguishes a zero exit code from a valid mesh:
    OpenFOAM can emit ``Failed N mesh checks`` while still exiting zero.
    """

    rows = list(lines)
    text = "\n".join(rows)
    counts: dict[str, int] = {}
    metrics: dict[str, float] = {}
    issue_counts = {
        "highAspectRatioCells": 0,
        "severelyNonOrthogonalFaces": 0,
        "smallDeterminantCells": 0,
        "lowInterpolationWeightFaces": 0,
        "lowVolumeRatioFaces": 0,
        "concaveCells": 0,
    }
    for line in rows:
        count_match = _CHECKMESH_COUNT_RE.search(line)
        if count_match:
            counts[count_match.group(1).lower().replace(" ", "_")] = int(count_match.group(2))
    for key, pattern in (
        ("maxNonOrthogonality", _CHECKMESH_NONORTH_RE),
        ("maxSkewness", _CHECKMESH_SKEW_RE),
        ("minVolume", _CHECKMESH_MIN_VOLUME_RE),
    ):
        match = pattern.search(text)
        if match:
            value = _finite(match.group(1))
            if value is not None:
                metrics[key] = value
            if key == "maxNonOrthogonality":
                average = _finite(match.group(2))
                if average is not None:
                    metrics["averageNonOrthogonality"] = average
    aspect = _CHECKMESH_ASPECT_RE.search(text)
    if aspect:
        value = _finite(aspect.group(1) or aspect.group(2))
        if value is not None:
            metrics["maxAspectRatio"] = value
    determinant = _CHECKMESH_DETERMINANT_RE.search(text)
    if determinant:
        for key, value in (("minCellDeterminant", determinant.group(1)), ("averageCellDeterminant", determinant.group(2))):
            parsed = _finite(value)
            if parsed is not None:
                metrics[key] = parsed
    for key, pattern in (
        ("severelyNonOrthogonalFaces", _CHECKMESH_SEVERE_NONORTH_RE),
        ("smallDeterminantCells", _CHECKMESH_SMALL_DETERMINANT_RE),
        ("lowInterpolationWeightFaces", _CHECKMESH_LOW_WEIGHT_RE),
        ("lowVolumeRatioFaces", _CHECKMESH_LOW_VOLUME_RATIO_RE),
        ("concaveCells", _CHECKMESH_CONCAVE_RE),
    ):
        match = pattern.search(text)
        if match:
            issue_counts[key] = int(match.group(1))
    high_aspect = re.search(r"High aspect ratio cells found.*?number of cells\s*(\d+)", text, re.IGNORECASE)
    if high_aspect:
        issue_counts["highAspectRatioCells"] = int(high_aspect.group(1))
    failed = _CHECKMESH_FAILED_RE.search(text)
    failed_checks = int(failed.group(1)) if failed else None
    command_is_full = bool(re.search(r"^Exec\s*:\s*checkMesh\b.*\-allGeometry.*\-allTopology", text, re.MULTILINE))
    mesh_ok = bool(re.search(r"\bMesh OK\b", text, re.IGNORECASE))
    if mesh_ok and failed_checks is None:
        failed_checks = 0
    return {
        "commandIsFull": command_is_full,
        "completed": mesh_ok or failed_checks is not None,
        "meshOk": mesh_ok,
        "failedChecks": failed_checks,
        "counts": counts,
        "metrics": metrics,
        "issueCounts": issue_counts,
    }


def parse_gmsh_to_foam_log(lines: Iterable[str]) -> dict[str, Any]:
    """Return physical-region and patch mapping evidence from ``gmshToFoam``."""

    rows = list(lines)
    mappings: dict[str, dict[str, Any]] = {}
    patch_names: dict[int, str] = {}
    cellzone_names: dict[int, str] = {}
    for line in rows:
        mapping = _GMSH_MAPPING_RE.search(line)
        if mapping:
            region, target, foam_index = mapping.groups()
            mappings[region] = {"target": target, "foamIndex": int(foam_index), "name": None}
        patch = _GMSH_PATCH_NAME_RE.search(line)
        if patch:
            patch_names[int(patch.group(1))] = patch.group(2)
        cellzone = _GMSH_CELLZONE_NAME_RE.search(line)
        if cellzone:
            cellzone_names[int(cellzone.group(1))] = cellzone.group(2)
    for mapping in mappings.values():
        if mapping["target"] == "patch":
            mapping["name"] = patch_names.get(mapping["foamIndex"])
        elif mapping["target"] == "cellZone":
            mapping["name"] = cellzone_names.get(mapping["foamIndex"])
    return {
        "completed": any(line.strip() == "End" for line in rows),
        "regions": mappings,
        "patchNames": sorted(patch_names.values()),
    }


def _artifact(path: Path) -> dict[str, Any]:
    return {"path": str(path), "present": path.is_file(), "bytes": path.stat().st_size if path.is_file() else None}


def audit_immutable_surface_candidate(
    *,
    surface_msh: Path,
    volume_msh: Path,
    surface_report: Path,
    configuration: Path,
    gmsh_log: Path,
    gmsh_to_foam_log: Path,
    checkmesh_log: Path,
    poly_mesh_dir: Path,
    expected_patches: Mapping[str, int] = {"inlet": 11, "outlet": 12, "wall": 13},
    expected_volume_name: str = "fluid",
) -> dict[str, Any]:
    """Fail closed unless a candidate retained all evidence and meets mesh gates."""

    artifacts = {
        "surfaceMsh": _artifact(surface_msh),
        "volumeMsh": _artifact(volume_msh),
        "surfaceReport": _artifact(surface_report),
        "configuration": _artifact(configuration),
        "gmshLog": _artifact(gmsh_log),
        "gmshToFoamLog": _artifact(gmsh_to_foam_log),
        "checkMeshLog": _artifact(checkmesh_log),
    }
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        artifacts[f"polyMesh/{name}"] = _artifact(poly_mesh_dir / name)
    reasons = [f"missing or empty required artifact: {name}" for name, item in artifacts.items() if not item["present"] or not item["bytes"]]
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    if artifacts["surfaceMsh"]["present"] and artifacts["volumeMsh"]["present"]:
        before = msh2_surface_fingerprint(surface_msh)
        after = msh2_surface_fingerprint(volume_msh)
        if before != after:
            reasons.append("volume MSH2 changed the frozen surface fingerprint or physical patch partition")
        if tetrahedron_count(volume_msh) <= 0:
            reasons.append("volume MSH2 contains no tetrahedra")
    else:
        reasons.append("cannot verify frozen-surface fingerprint without both MSH2 files")
    report_data: dict[str, Any] | None = None
    if artifacts["surfaceReport"]["present"]:
        try:
            report_data = json.loads(surface_report.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            reasons.append("surface hash report is not valid JSON")
        else:
            surface_hash_equal = isinstance(report_data, dict) and (
                report_data.get("surfaceHashEqual") is True
                or report_data.get("outerSurfaceHashEqual") is True
            )
            if not surface_hash_equal:
                reasons.append("surface hash report does not record exact preservation")
            oriented_surface_hash_equal = isinstance(report_data, dict) and report_data.get("outerSurfaceOrientedHashEqual") is True
            if not oriented_surface_hash_equal:
                reasons.append("surface hash report does not record exact orientation preservation")
    conversion = parse_gmsh_to_foam_log(_read_lines(gmsh_to_foam_log)) if artifacts["gmshToFoamLog"]["present"] else {}
    if conversion and not conversion["completed"]:
        reasons.append("gmshToFoam log lacks completion marker")
    if conversion:
        expected_names = set(expected_patches)
        actual_names = set(conversion["patchNames"])
        if actual_names != expected_names:
            reasons.append("gmshToFoam patch names do not exactly match inlet/outlet/wall")
        for name, physical_id in expected_patches.items():
            mapping = conversion["regions"].get(str(physical_id))
            if not mapping or mapping.get("target") != "patch" or mapping.get("name") != name:
                reasons.append(f"gmshToFoam did not map physical surface {physical_id} to patch {name}")
        fluid = [item for item in conversion["regions"].values() if item.get("target") == "cellZone"]
        if len(fluid) != 1 or fluid[0].get("name") != expected_volume_name:
            reasons.append(f"gmshToFoam did not map exactly one cellZone named {expected_volume_name}")
    checkmesh = parse_checkmesh_log(_read_lines(checkmesh_log)) if artifacts["checkMeshLog"]["present"] else {}
    if checkmesh:
        if not checkmesh["commandIsFull"]:
            reasons.append("checkMesh log was not captured with -allGeometry -allTopology")
        if not checkmesh["completed"]:
            reasons.append("checkMesh log lacks a terminal Mesh OK or Failed N mesh checks marker")
        if checkmesh["failedChecks"] != 0:
            reasons.append("checkMesh did not report zero failed checks")
        for key in ("smallDeterminantCells", "lowInterpolationWeightFaces", "lowVolumeRatioFaces", "concaveCells"):
            if checkmesh["issueCounts"].get(key, 0) > 0:
                reasons.append(f"checkMesh reported {checkmesh['issueCounts'][key]} {key}")
        if checkmesh["metrics"].get("minVolume", 0.0) <= 0.0:
            reasons.append("checkMesh did not report a strictly positive minimum volume")
    return {
        "schema": "flowlab.immutable-surface-layered-gate.v1",
        "accepted": not reasons,
        "rejectionReasons": reasons,
        "artifacts": artifacts,
        "surface": {"before": before, "after": after, "report": report_data},
        "conversion": conversion,
        "checkMesh": checkmesh,
        "expected": {"patches": dict(expected_patches), "volumeName": expected_volume_name},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface-msh", type=Path, required=True)
    parser.add_argument("--volume-msh", type=Path, required=True)
    parser.add_argument("--surface-report", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--gmsh-log", type=Path, required=True)
    parser.add_argument("--gmsh-to-foam-log", type=Path, required=True)
    parser.add_argument("--checkmesh-log", type=Path, required=True)
    parser.add_argument("--poly-mesh", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit_immutable_surface_candidate(
        surface_msh=args.surface_msh,
        volume_msh=args.volume_msh,
        surface_report=args.surface_report,
        configuration=args.configuration,
        gmsh_log=args.gmsh_log,
        gmsh_to_foam_log=args.gmsh_to_foam_log,
        checkmesh_log=args.checkmesh_log,
        poly_mesh_dir=args.poly_mesh,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
