from __future__ import annotations

import csv
from pathlib import Path

import pytest

from server.flowlab.open_boundary_face_traction_audit import (
    _final_force_record,
    _patch_stats,
    _read_kinematic_viscosity,
)


def test_final_force_record_reads_pressure_and_viscous_vectors(tmp_path: Path) -> None:
    path = tmp_path / "forces.dat"
    path.write_text(
        "# Time forces\n"
        "100 (((-1e-3 0 0) (-1.25e-9 2e-18 3e-18)) ((0 0 0) (0 0 0)))\n",
        encoding="utf-8",
    )

    record = _final_force_record(path)

    assert record["time"] == 100.0
    assert record["pressureForce"] == [-1.0e-3, 0.0, 0.0]
    assert record["viscousForce"] == [-1.25e-9, 2.0e-18, 3.0e-18]
    assert record["totalForce"][0] == pytest.approx(-0.00100000125)


def test_patch_stats_retains_face_attribution_and_zero_gradient_state() -> None:
    rows = [
        {
            "patch_face": "0",
            "mesh_face": "10",
            "owner_cell": "2",
            "cf_x": "0",
            "cf_y": "0.25",
            "cf_z": "0.25",
            "area": "0.5",
            "sn_grad_u_x": "0",
            "sn_grad_u_y": "0",
            "sn_grad_u_z": "0",
            "sn_grad_normal_velocity": "0",
            "viscous_force_x": "2e-20",
            "viscous_force_y": "0",
            "viscous_force_z": "0",
            "pressure_force_x": "0",
            "pressure_force_y": "0",
            "pressure_force_z": "0",
            "boundary_error_x": "1e-8",
            "owner_error_x": "1e-8",
        },
        {
            "patch_face": "1",
            "mesh_face": "11",
            "owner_cell": "3",
            "cf_x": "0",
            "cf_y": "0.75",
            "cf_z": "0.25",
            "area": "0.5",
            "sn_grad_u_x": "0",
            "sn_grad_u_y": "0",
            "sn_grad_u_z": "0",
            "sn_grad_normal_velocity": "0",
            "viscous_force_x": "-2e-20",
            "viscous_force_y": "0",
            "viscous_force_z": "0",
            "pressure_force_x": "0",
            "pressure_force_y": "0",
            "pressure_force_z": "0",
            "boundary_error_x": "2e-8",
            "owner_error_x": "2e-8",
        },
    ]

    stats = _patch_stats(rows)

    assert stats["faceCount"] == 2
    assert stats["area"] == 1.0
    assert stats["viscousForce"] == [0.0, 0.0, 0.0]
    assert stats["viscousForceXContributions"]["absoluteSum"] == 4.0e-20
    assert stats["snGradU"]["allComponentsExactlyZero"] is True
    assert stats["velocityError"]["maxAbsBoundaryUx"] == 2.0e-8


def test_kinematic_viscosity_is_read_with_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "physicalProperties"
    path.write_text(
        "viscosityModel constant; nu [0 2 -1 0 0 0 0] 9.9999999999999995e-07;\n",
        encoding="utf-8",
    )

    assert _read_kinematic_viscosity(path) == pytest.approx(1.0e-6)


def test_emitted_face_csv_has_unique_patch_face_pairs() -> None:
    csv_path = (
        Path(__file__).resolve().parents[2]
        / "benchmarks/cases/open-boundary/runs/2026-07-16-forced-mms-v15-face-traction-audit"
        / "artifacts/face-decomposition.csv"
    )
    if not csv_path.exists():
        pytest.skip("retained face-audit evidence is not present")

    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    pairs = {(row["patch"], row["patch_face"]) for row in rows}
    assert len(rows) == 288
    assert len(pairs) == len(rows)
    assert {row["patch"] for row in rows} == {"inlet", "outlet"}
