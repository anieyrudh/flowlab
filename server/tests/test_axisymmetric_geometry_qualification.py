from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.flowlab import adapters
from server.flowlab import axisymmetric_geometry_qualification as qualification
from server.flowlab.execution import validate_solver_case


STRAIGHT_PIPE_CAMPAIGN_BASELINE_SHA256 = (
    "c03e608b486a2f1cd0374bede89a579bb9092554624c2ba1803b1bd049f88925"
)


def test_contract_is_prospective_nonpromotional_and_keeps_straight_pipe_unchanged() -> None:
    contract, digest = qualification.load_frozen_contract()

    assert len(digest) == 64
    assert (
        contract["schema"]
        == "flowlab.axisymmetric-geometry-experimental-qualification-contract.v2"
    )
    assert contract["prospectiveRevision"]["scientificGateChanges"] is False
    assert (
        contract["identity"]["algorithm"]
        == "axisymmetric-logical-cell-vertex-signature-v2"
    )
    assert contract["claim"]["qualificationClass"] == "experimental-software-geometry-only"
    assert contract["promotionAuthorized"] is False
    assert contract["review"]["validatedStatusChangeAuthorized"] is False
    assert contract["retention"]["trackedEvidenceMutationAuthorized"] is False
    straight_pipe_path = (
        qualification.REPOSITORY_ROOT
        / contract["existingStraightPipeCampaign"]["module"]
    )
    assert hashlib.sha256(straight_pipe_path.read_bytes()).hexdigest() == (
        STRAIGHT_PIPE_CAMPAIGN_BASELINE_SHA256
    )


def test_preflight_materializes_generation_matrix_and_two_identical_builds(
    tmp_path: Path,
) -> None:
    contract, digest = qualification.load_frozen_contract()

    cases, report = qualification.materialize_preflight(
        tmp_path,
        contract,
        digest,
    )

    assert report["allGenerationCasesPassed"] is True
    assert report["allGeneratedFileHashesMatch"] is True
    assert {item["caseId"] for item in report["generationOnly"]} == {
        "venturi",
        "contraction",
        "expansion",
        "nozzle",
    }
    assert all(item["minimumCellVolumeM3"] > 0.0 for item in report["generationOnly"])
    assert set(cases) == {"coarse", "medium", "fine"}
    for level, case in cases.items():
        assert validate_solver_case(case) == []
        profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
        request = profile["experimentalQualificationContract"]
        assert request["contractSha256"] == digest
        assert request["promotionAuthorized"] is False
        assert case.resultComponentMap is not None
        assert case.resultComponentMap.version == 2
        assert (
            case.resultComponentMap.artifactBindings[0].artifactName
            == "postProcessing/flowlabNative/*.vtk"
        )
        identity = json.loads(
            case.files["constant/flowlab_result_identity_contract.json"]
        )
        assert (
            identity["algorithm"]
            == "axisymmetric-logical-cell-vertex-signature-v2"
        )
        assert (
            tmp_path
            / "preflight"
            / "build-a"
            / level
            / adapters.CASE_MANIFEST_PATH
        ).is_file()


def test_multi_edge_exact_levels_double_every_logical_count() -> None:
    contract, digest = qualification.load_frozen_contract()
    levels = contract["levels"]
    cases = [
        qualification._build_case(
            qualification._runtime_project(contract, digest, level)
        )
        for level in levels
    ]
    profiles = [
        json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
        for case in cases
    ]

    assert [profile["nRadial"] for profile in profiles] == [4, 8, 16]
    for coarse, medium, fine in zip(
        profiles[0]["segments"],
        profiles[1]["segments"],
        profiles[2]["segments"],
        strict=True,
    ):
        assert medium["nAxial"] == 2 * coarse["nAxial"]
        assert fine["nAxial"] == 2 * medium["nAxial"]


def test_qualification_request_rejects_missing_contract_hash() -> None:
    contract, digest = qualification.load_frozen_contract()
    project = qualification._runtime_project(contract, digest, contract["levels"][0])
    project["solver"]["axisymmetricQualification"]["contractSha256"] = "missing"

    with pytest.raises(ValueError, match="contract SHA-256"):
        qualification._build_case(project)


def test_check_mesh_minimum_volume_parser_excludes_sentence_punctuation() -> None:
    cells, minimum_volume = qualification._check_mesh_cell_count_and_minimum_volume(
        "    cells: 1664\n    min volume = 1.119835e-10.  Max aspect ratio = 8.0\n"
    )

    assert cells == 1664
    assert minimum_volume == pytest.approx(1.119835e-10)
