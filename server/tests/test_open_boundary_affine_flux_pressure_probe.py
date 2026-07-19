from __future__ import annotations

from pathlib import Path

from server.flowlab.open_boundary_affine_flux_pressure_probe import (
    _boundary_vectors,
    _case_files,
    _face_geometry,
)
from server.flowlab.open_boundary_mms_redesign import AffineCrossflowMms


def test_flux_pressure_probe_changes_only_pressure_boundary_formulation() -> None:
    files = _case_files(12, AffineCrossflowMms())

    assert "inlet { type fixedFluxPressure; value uniform 0.001; gradient uniform 0.001; }" in files["0/p"]
    assert "outlet { type fixedValue; value uniform 0; }" in files["0/p"]
    assert "yMin { type fixedFluxPressure; value uniform 0; gradient uniform 0; }" in files["0/p"]
    assert "yMax { type fixedFluxPressure; value uniform 0; gradient uniform 0; }" in files["0/p"]
    assert "inlet { type fixedValue;" in files["0/U"]
    assert "outlet { type pressureInletOutletVelocity;" in files["0/U"]
    assert "endTime 1" in files["system/controlDict"]


def test_boundary_vector_parser_retains_patch_face_order(tmp_path: Path) -> None:
    patches = "\n".join(
        f"{name} {{ value nonuniform List<vector> 1 (({index} 0 0)); }}"
        for index, name in enumerate(("inlet", "outlet", "yMin", "yMax", "zMin", "zMax"))
    )
    path = tmp_path / "C"
    path.write_text(f"boundaryField {{ {patches} }}\n", encoding="utf-8")

    result = _boundary_vectors(path)

    assert result["inlet"] == [(0.0, 0.0, 0.0)]
    assert result["zMax"] == [(5.0, 0.0, 0.0)]


def test_face_geometry_preserves_oriented_area() -> None:
    points = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]

    centre, area = _face_geometry(points, [0, 1, 2, 3])

    assert centre == (0.5, 0.5, 0.0)
    assert area == (0.0, 0.0, 1.0)
