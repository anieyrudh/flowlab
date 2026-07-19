from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import pytest

from server.flowlab.fda_nozzle_re500 import (
    FdaNozzleDefinition,
    OFFICIAL_ARCHIVE_SHA256,
    PRESSURE_CODES,
    block_mesh_dict,
    _gci,
    _polygon_area,
    _probe_records,
    _wall_samples,
    ingest_experiment,
    predeclared_contract,
    prepare_case,
)


def test_definition_reproduces_fda_reynolds_number_and_geometry() -> None:
    spec = FdaNozzleDefinition()
    assert spec.throat_reynolds_number == pytest.approx(500.0, rel=2.0e-6)
    assert spec.contraction_start_x_m == pytest.approx(-0.062685127, rel=1.0e-7)
    assert spec.radius(-0.088) == pytest.approx(0.006)
    assert spec.radius(-0.040) == pytest.approx(0.002)
    assert spec.radius(0.008) == pytest.approx(0.006)


@pytest.mark.parametrize("refinement", [1, 2, 4])
def test_block_mesh_is_nested_and_declares_only_required_patches(refinement: int) -> None:
    mesh = block_mesh_dict(refinement)
    assert "type patch;" in mesh
    assert "type wall;" in mesh
    assert "symmetry" not in mesh
    assert "wedge" not in mesh
    assert "tet" not in mesh
    assert f"({2 * refinement} {2 * refinement}" in mesh


def test_contract_is_fail_closed_and_distinguishes_diagnostic_experiment_rows() -> None:
    contract = predeclared_contract()
    assert contract["campaignId"] == "fda-nozzle-re500-v1"
    assert contract["comparisons"]["axialVelocityProfiles"]["role"] == "mandatory-validation"
    assert contract["comparisons"]["radialVelocityProfiles"]["role"] == "mandatory-reporting-nonpromotional"
    assert contract["comparisons"]["wallShearViscousTraction"]["role"] == "mandatory-reporting-nonpromotional"
    assert contract["comparisons"]["centrelineAndWallPressure"]["eligibleExperimentCodes"] == list(PRESSURE_CODES)
    assert all(contract["mandatoryGates"].values())


def test_prepare_case_refuses_overwrite(tmp_path: Path) -> None:
    case = tmp_path / "case"
    prepare_case(case, "coarse", 1)
    assert (case / "system" / "blockMeshDict").is_file()
    assert "fixedFluxPressure" in (case / "0" / "p").read_text(encoding="utf-8")
    assert "type fixedValue" in (case / "0" / "U").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare_case(case, "coarse", 1)


def test_experimental_archive_hash_is_enforced(tmp_path: Path) -> None:
    archive = tmp_path / "SE_exp_0500.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("experiment/sample.txt", "dataset-code 1\n")
    assert hashlib.sha256(archive.read_bytes()).hexdigest() != OFFICIAL_ARCHIVE_SHA256
    with pytest.raises(ValueError, match="hash mismatch"):
        ingest_experiment(archive, tmp_path / "out")


def test_polygon_area_supports_openfoam_boundary_face_quadrature() -> None:
    assert _polygon_area(
        [(0.0, -1.0, -1.0), (0.0, 1.0, -1.0), (0.0, 1.0, 1.0), (0.0, -1.0, 1.0)]
    ) == pytest.approx(4.0)


def test_probe_records_exclude_published_padding_outside_physical_wall() -> None:
    summary = {
        "centrelineAxialVelocity": [],
        "wallPressureRelativeToExpansion": [],
        "axialVelocityProfiles": {
            "-0.088000": {
                "axial": [
                    {"coordinateM": -0.007},
                    {"coordinateM": -0.006},
                    {"coordinateM": 0.0},
                    {"coordinateM": 0.006},
                    {"coordinateM": 0.007},
                ],
                "radial": [],
            }
        },
    }
    records = _probe_records(summary)
    assert [record["radialCoordinateM"] for record in records] == [-0.006, 0.0, 0.006]


def test_expansion_profile_is_sampled_on_downstream_side() -> None:
    summary = {
        "centrelineAxialVelocity": [],
        "wallPressureRelativeToExpansion": [],
        "axialVelocityProfiles": {
            "0.000000": {
                "axial": [{"coordinateM": 0.005}],
                "radial": [],
            }
        },
    }
    records = _probe_records(summary)
    assert records[0]["point"][0] > 0.0
    assert records[0]["radialCoordinateM"] == pytest.approx(0.005)


def test_gci_qualifies_monotonic_convergence_and_rejects_oscillation() -> None:
    monotonic = _gci(1.0, 1.5, 1.625)
    assert monotonic["qualified"]
    assert monotonic["observedOrder"] == pytest.approx(2.0)
    assert monotonic["absoluteFineGridGci"] == pytest.approx(1.25 * 0.125 / 3.0)
    assert not _gci(1.0, 1.5, 1.4)["qualified"]


def test_wall_samples_area_average_pressure_and_tangential_traction(tmp_path: Path) -> None:
    csv_path = tmp_path / "faces.csv"
    csv_path.write_text(
        "patch,n_x,n_y,n_z,cf_x,area,pressure_pa,traction_x_pa,traction_y_pa,traction_z_pa\n"
        "wall,0,1,0,0.01,1,10,0,2,0\n"
        "wall,0,-1,0,0.01,3,14,0,-4,0\n"
        "wall,0,1,0,0.02,4,20,0,3,0\n"
        "wall,1,0,0,0.01,100,1000,9,0,0\n"
        "outlet,0,1,0,0.01,100,1000,0,9,0\n",
        encoding="utf-8",
    )
    sample = _wall_samples(csv_path, [0.015])[0]
    assert sample["sampledCoordinateM"] == pytest.approx(0.015)
    assert sample["pressurePa"] == pytest.approx(16.5)
    # Both test tractions are normal to the wall and therefore have zero
    # tangential magnitude; the axial-normal expansion face is excluded.
    assert sample["tangentialTractionPa"] == pytest.approx(0.0)
