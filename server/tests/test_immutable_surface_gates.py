from __future__ import annotations

import json
from pathlib import Path

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint
from server.flowlab.immutable_surface_gates import (
    audit_immutable_surface_candidate,
    parse_checkmesh_log,
    parse_gmsh_to_foam_log,
)


def _checkmesh(*, failed: int = 0, low_weight: int = 0) -> str:
    issue = f" ***Faces with small interpolation weight (< 0.05) found, number of faces: {low_weight}\n" if low_weight else ""
    terminal = "Mesh OK." if failed == 0 else f"Failed {failed} mesh checks."
    return f"""Exec   : checkMesh -allGeometry -allTopology
    points: 10
    faces: 20
    internal faces: 8
    cells: 4
    boundary patches: 3
Min volume = 1e-12. Max volume = 2e-12. Total volume = 6e-12.
Mesh non-orthogonality Max: 12 average: 4
Max skewness = 0.3 OK.
Cell determinant (wellposedness) : minimum: 0.02 average: 0.4
{issue}{terminal}
"""


def _gmsh_to_foam() -> str:
    return """Mapping region 11 to Foam patch 0
Mapping region 12 to Foam patch 1
Mapping region 13 to Foam patch 2
Mapping region 1 to Foam cellZone 0
Patch 0 gets name inlet
Patch 1 gets name outlet
Patch 2 gets name wall
Writing zone 0 to cellZone fluid and cellSet
End
"""


def test_checkmesh_parser_retains_failure_category_counts() -> None:
    parsed = parse_checkmesh_log(_checkmesh(failed=1, low_weight=7).splitlines())
    assert parsed["commandIsFull"] is True
    assert parsed["completed"] is True
    assert parsed["failedChecks"] == 1
    assert parsed["counts"]["cells"] == 4
    assert parsed["metrics"]["minVolume"] == 1e-12
    assert parsed["issueCounts"]["lowInterpolationWeightFaces"] == 7


def test_gmsh_to_foam_parser_binds_physical_regions_to_names() -> None:
    parsed = parse_gmsh_to_foam_log(_gmsh_to_foam().splitlines())
    assert parsed["completed"] is True
    assert parsed["regions"]["11"] == {"target": "patch", "foamIndex": 0, "name": "inlet"}
    assert parsed["regions"]["1"] == {"target": "cellZone", "foamIndex": 0, "name": "fluid"}


def test_candidate_audit_requires_immutable_surface_and_all_quality_evidence(tmp_path: Path) -> None:
    surface = tmp_path / "surface.msh"
    volume = tmp_path / "volume.msh"
    write_cylinder_surface_master(surface, length_m=0.05, radius_m=0.005, circumferential_chords=4, axial_cells=1)
    # A tiny synthetic tetrahedron uses the same boundary rows only for the
    # parser-level test; the audit catches it as the wrong frozen surface if
    # a caller ever changes the master.
    volume.write_text(surface.read_text(encoding="utf-8").replace("$EndElements", "9 4 2 1 1 1 2 3 4\n$EndElements"), encoding="utf-8")
    report = tmp_path / "surface.json"
    fingerprint = msh2_surface_fingerprint(surface)
    report.write_text(
        json.dumps({"surfaceHashEqual": True, "outerSurfaceOrientedHashEqual": True, "before": fingerprint, "after": fingerprint}),
        encoding="utf-8",
    )
    configuration = tmp_path / "configuration.json"
    configuration.write_text("{}\n", encoding="utf-8")
    gmsh_log = tmp_path / "gmsh.log"
    gmsh_log.write_text("Info : meshed\n", encoding="utf-8")
    conversion = tmp_path / "gmshToFoam.log"
    conversion.write_text(_gmsh_to_foam(), encoding="utf-8")
    checkmesh = tmp_path / "checkMesh.log"
    checkmesh.write_text(_checkmesh(), encoding="utf-8")
    poly_mesh = tmp_path / "polyMesh"
    poly_mesh.mkdir()
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly_mesh / name).write_text("evidence\n", encoding="utf-8")

    audited = audit_immutable_surface_candidate(
        surface_msh=surface,
        volume_msh=volume,
        surface_report=report,
        configuration=configuration,
        gmsh_log=gmsh_log,
        gmsh_to_foam_log=conversion,
        checkmesh_log=checkmesh,
        poly_mesh_dir=poly_mesh,
    )
    assert audited["accepted"] is True
    assert audited["checkMesh"]["metrics"]["maxNonOrthogonality"] == 12.0
    assert audited["conversion"]["patchNames"] == ["inlet", "outlet", "wall"]


def test_candidate_audit_rejects_low_weight_even_if_checkmesh_reports_zero_failures(tmp_path: Path) -> None:
    surface = tmp_path / "surface.msh"
    write_cylinder_surface_master(surface, length_m=0.05, radius_m=0.005, circumferential_chords=4, axial_cells=1)
    volume = tmp_path / "volume.msh"
    volume.write_text(surface.read_text(encoding="utf-8").replace("$EndElements", "9 4 2 1 1 1 2 3 4\n$EndElements"), encoding="utf-8")
    report = tmp_path / "surface.json"
    report.write_text('{"surfaceHashEqual": true, "outerSurfaceOrientedHashEqual": true}\n', encoding="utf-8")
    for name, content in (("configuration.json", "{}\n"), ("gmsh.log", "Info\n"), ("gmshToFoam.log", _gmsh_to_foam()), ("checkMesh.log", _checkmesh(low_weight=2))):
        (tmp_path / name).write_text(content, encoding="utf-8")
    poly_mesh = tmp_path / "polyMesh"
    poly_mesh.mkdir()
    for name in ("points", "faces", "owner", "neighbour", "boundary"):
        (poly_mesh / name).write_text("evidence\n", encoding="utf-8")
    audited = audit_immutable_surface_candidate(
        surface_msh=surface,
        volume_msh=volume,
        surface_report=report,
        configuration=tmp_path / "configuration.json",
        gmsh_log=tmp_path / "gmsh.log",
        gmsh_to_foam_log=tmp_path / "gmshToFoam.log",
        checkmesh_log=tmp_path / "checkMesh.log",
        poly_mesh_dir=poly_mesh,
    )
    assert audited["accepted"] is False
    assert any("lowInterpolationWeightFaces" in reason for reason in audited["rejectionReasons"])
