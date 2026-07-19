from pathlib import Path

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint, tetrahedron_count


def test_cylinder_surface_master_is_deterministic_and_patch_partitioned(tmp_path: Path) -> None:
    first = tmp_path / "first.msh"
    second = tmp_path / "second.msh"
    kwargs = {
        "length_m": 0.05,
        "radius_m": 0.005,
        "circumferential_chords": 8,
        "axial_cells": 3,
    }

    write_cylinder_surface_master(first, **kwargs)
    write_cylinder_surface_master(second, **kwargs)

    assert first.read_bytes() == second.read_bytes()
    fingerprint = msh2_surface_fingerprint(first)
    assert fingerprint["surfaceTriangles"] == 64
    assert fingerprint["trianglesByPhysicalId"] == {"11": 8, "12": 8, "13": 48}
    assert len(fingerprint["surfaceSha256"]) == 64
    assert tetrahedron_count(first) == 0


def test_surface_fingerprint_ignores_triangle_orientation_but_not_patch_identity(tmp_path: Path) -> None:
    master = tmp_path / "master.msh"
    changed = tmp_path / "changed.msh"
    write_cylinder_surface_master(
        master,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=4,
        axial_cells=1,
    )
    rows = master.read_text(encoding="utf-8").splitlines()
    element_start = rows.index("$Elements") + 2
    original = rows[element_start].split()
    # Reverse a triangle's orientation: its geometric patch identity is the
    # same and must not change the immutable-boundary fingerprint.
    rows[element_start] = " ".join(original[:-3] + list(reversed(original[-3:])))
    changed.write_text("\n".join(rows) + "\n", encoding="utf-8")
    original_fingerprint = msh2_surface_fingerprint(master)
    reversed_fingerprint = msh2_surface_fingerprint(changed)
    assert original_fingerprint["surfaceSha256"] == reversed_fingerprint["surfaceSha256"]
    assert original_fingerprint["orientedSurfaceSha256"] != reversed_fingerprint["orientedSurfaceSha256"]

    patched_rows = changed.read_text(encoding="utf-8").splitlines()
    fields = patched_rows[element_start].split()
    fields[3] = "99"
    patched_rows[element_start] = " ".join(fields)
    changed.write_text("\n".join(patched_rows) + "\n", encoding="utf-8")
    assert msh2_surface_fingerprint(master)["surfaceSha256"] != msh2_surface_fingerprint(changed)["surfaceSha256"]
