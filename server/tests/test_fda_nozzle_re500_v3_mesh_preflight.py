from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab import fda_nozzle_re500_v3_mesh_preflight as preflight


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT / "docs/validation/fda-nozzle-re500/V3_MESH_PREFLIGHT_CONTRACT.json"
)
PRESSURE = (
    ROOT / "docs/validation/fda-nozzle-re500/PRESSURE_REFERENCE_DISPOSITION.json"
)


def test_frozen_contract_and_pressure_disposition_are_integral() -> None:
    contract = preflight._verify_contract(CONTRACT, PRESSURE)
    pressure = json.loads(PRESSURE.read_text(encoding="utf-8"))
    assert preflight._sha256(CONTRACT) == preflight.CONTRACT_SHA256
    assert preflight._sha256(PRESSURE) == preflight.PRESSURE_DISPOSITION_SHA256
    assert pressure["status"] == "not-qualified-nonpromotional"
    assert pressure["disposition"] == {
        "wallPressure": "mandatory-reported-diagnostic-nonpromotional",
        "pressureDrop": "mandatory-reported-diagnostic-nonpromotional",
        "mayAuthorizePromotion": False,
        "mayRescuePromotion": False,
        "mayDefeatVelocityPromotion": False,
        "postHocOffsetOrUncertaintyAllowanceAllowed": False,
    }
    assert contract["authorization"]["runSolver"] is False
    assert contract["authorization"]["runFullSuccessorCampaign"] is False
    assert contract["promotionAuthorized"] is False


def test_mesh_family_has_exact_counts_and_eightfold_refinement() -> None:
    declared = []
    boundary_segments = []
    for label in preflight.LEVELS:
        mesh = preflight.v3_block_mesh(label)
        declared.append(preflight._declared_cells(mesh))
        boundary_segments.append(
            4 * preflight.LEVEL_CELLS[label]["annularTangential"]
        )
        assert mesh.count("hex (") == 24
        assert "wedge (" not in mesh
        assert "poly (" not in mesh
    assert declared == [44_256, 354_048, 2_832_384]
    assert [right / left for left, right in zip(declared, declared[1:])] == [
        8.0,
        8.0,
    ]
    assert boundary_segments == [16, 32, 64]


def test_mesh_only_case_has_required_control_dictionary_without_solver() -> None:
    control = preflight._mesh_only_control_dict()
    assert "object controlDict;" in control
    assert "application blockMesh;" in control
    assert "foamRun" not in control


def _levels(errors: tuple[float, float, float]) -> dict[str, object]:
    return {
        label: {"geometry": {"volume": {"relativeError": error}}}
        for label, error in zip(preflight.LEVELS, errors)
    }


def test_geometry_acceptance_includes_exact_one_percent_boundary() -> None:
    monotonic, fine = preflight.geometry_acceptance(
        _levels((-0.03, -0.02, -0.01)), ("volume",), 0.01
    )
    assert monotonic == {"volume": True}
    assert fine == {"volume": True}


def test_geometry_acceptance_rejects_nonmonotonic_and_over_tolerance() -> None:
    monotonic, fine = preflight.geometry_acceptance(
        _levels((-0.03, -0.03, -0.0100001)), ("volume",), 0.01
    )
    assert monotonic == {"volume": False}
    assert fine == {"volume": False}


def test_openfoam_label_parser_accepts_standard_footer(tmp_path: Path) -> None:
    labels = tmp_path / "neighbour"
    labels.write_text(
        "FoamFile\n{\n class labelList;\n}\n3\n(\n1\n8\n64\n)\n"
        "// ************************************************************************* //\n",
        encoding="utf-8",
    )
    assert preflight._foam_labels(labels) == [1, 8, 64]


def test_missing_frozen_evidence_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing frozen"):
        preflight._verify_contract(CONTRACT, tmp_path / "missing-pressure.json")


def test_nonempty_output_is_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "campaign"
    output.mkdir()
    (output / "existing-evidence.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        preflight._require_new(output, "mesh-preflight campaign")


def test_empty_output_directory_is_accepted_once(tmp_path: Path) -> None:
    output = tmp_path / "campaign"
    output.mkdir()
    preflight._require_new(output, "mesh-preflight campaign")
