from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from server.flowlab import full_ogrid_geometry_qualification as qualification
from server.flowlab import execution
from server.flowlab.execution import materialize_case_files, validate_solver_case


AXISYMMETRIC_STRAIGHT_PIPE_SHA256 = (
    "c03e608b486a2f1cd0374bede89a579bb9092554624c2ba1803b1bd049f88925"
)
FULL_OGRID_STRAIGHT_PIPE_SHA256 = (
    "3a4db75b114970a4cde7c5c548d908728d3df3748551f2239c9baaec02bb4c7e"
)


def test_v3_contract_is_prospective_independent_and_nonpromotional() -> None:
    contract, digest = qualification.load_frozen_contract()

    assert len(digest) == 64
    assert contract["claim"]["qualificationClass"] == (
        "experimental-software-geometry-only"
    )
    assert contract["predecessorDisposition"]["freshV1OrV2ExecutionAuthorized"] is False
    assert contract["predecessorDisposition"]["v3IsIndependentTopology"] is True
    assert contract["promotionAuthorized"] is False
    assert contract["review"]["validatedStatusChangeAuthorized"] is False
    assert contract["retention"]["trackedEvidenceMutationAuthorized"] is False
    assert contract["identity"]["algorithm"] == (
        "full-ogrid-normalized-logical-vertex-signature-v4"
    )

    for relative_path, expected in (
        (
            "server/flowlab/axisymmetric_straight_pipe_campaign.py",
            AXISYMMETRIC_STRAIGHT_PIPE_SHA256,
        ),
        (
            "server/flowlab/full_ogrid_straight_pipe_campaign.py",
            FULL_OGRID_STRAIGHT_PIPE_SHA256,
        ),
    ):
        assert hashlib.sha256(
            (qualification.REPOSITORY_ROOT / relative_path).read_bytes()
        ).hexdigest() == expected


def test_contract_levels_are_uniform_2x_and_have_exact_cell_counts() -> None:
    contract, _digest = qualification.load_frozen_contract()
    levels = contract["levels"]

    assert [level["expectedCellCount"] for level in levels] == [
        2496,
        19968,
        159744,
    ]
    for coarse, fine in zip(levels, levels[1:], strict=False):
        assert fine["annularRadialCells"] == 2 * coarse["annularRadialCells"]
        assert fine["circumferentialCells"] == 2 * coarse["circumferentialCells"]
        assert fine["coreCellsPerSide"] == 2 * coarse["coreCellsPerSide"]
        assert all(
            fine["axialCellsByEdge"][edge_id]
            == 2 * coarse["axialCellsByEdge"][edge_id]
            for edge_id in coarse["axialCellsByEdge"]
        )
        assert fine["expectedCellCount"] == 8 * coarse["expectedCellCount"]


def test_preflight_covers_shapes_determinism_and_explicit_identity(
    tmp_path,
) -> None:
    contract, digest = qualification.load_frozen_contract()
    bounded_contract = deepcopy(contract)
    bounded_contract["levels"] = [contract["levels"][0]]

    cases, report = qualification.materialize_preflight(
        tmp_path,
        bounded_contract,
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
    assert set(cases) == {"coarse"}
    case = cases["coarse"]
    assert validate_solver_case(case) == []
    profile = json.loads(
        case.files["constant/flowlab_full_ogrid_profile.json"]
    )
    assert profile["qualificationContract"]["contractSha256"] == digest
    assert profile["qualificationContract"]["promotionAuthorized"] is False
    identity = json.loads(
        case.files["constant/flowlab_result_identity_contract.json"]
    )
    assert identity["sourceCellCount"] == 2496
    assert identity["unownedRanges"] == []
    assert (
        tmp_path
        / "preflight"
        / "build-a"
        / "coarse"
        / "constant"
        / "flowlab_result_identity_contract.json"
    ).is_file()


def test_qualification_request_rejects_missing_contract_hash() -> None:
    contract, digest = qualification.load_frozen_contract()
    project = qualification._runtime_project(
        contract, digest, contract["levels"][0]
    )
    project["solver"]["fullOGridQualification"]["contractSha256"] = "missing"

    with pytest.raises(ValueError, match="contract SHA-256"):
        qualification._build_case(project)


def test_multi_segment_full_ogrid_routes_directly_to_block_mesh(tmp_path) -> None:
    contract, digest = qualification.load_frozen_contract()
    case = qualification._build_case(
        qualification._runtime_project(
            contract, digest, contract["levels"][0]
        )
    )
    case_dir = tmp_path / "case"
    materialize_case_files(case, case_dir)

    assert execution._openfoam_case_is_full_ogrid(case_dir) is True
    assert execution._openfoam_required_mesh_commands(case_dir) == [
        "blockMesh",
        "checkMesh",
    ]


def test_iteration_count_ignores_auxiliary_openfoam_commands() -> None:
    """Regression: solve.log concatenates every command, not only foamRun.

    Under MPI the level also runs decomposePar, a second parallel checkMesh, and
    reconstructPar, and each emits its own `Time =` banner. Counting the whole
    file reported 2,004 iterations for a level that ran exactly its declared
    2,000, failing the iteration-control gate on an instrumentation artifact
    rather than a real early stop. Serial runs matched only because those
    auxiliary commands are absent.
    """

    from server.flowlab.full_ogrid_geometry_qualification import (
        _foam_run_log_section,
        _iteration_control_gate,
    )

    log = "\n".join(
        [
            "Exec   : checkMesh -allGeometry -allTopology",
            "Time = 0s",
            "Exec   : decomposePar -force",
            "Time = 0s",
            "Exec   : checkMesh -parallel -allGeometry -allTopology",
            "Time = 0s",
            "Exec   : foamRun -solver incompressibleFluid -parallel",
            *[f"Time = {step}s" for step in range(1, 2001)],
            "Exec   : reconstructPar -latestTime",
            "Time = 2000s",
            "",
        ]
    )

    section = _foam_run_log_section(log)
    assert section.count("Time = ") == 2000
    assert "decomposePar" not in section and "reconstructPar" not in section

    control = {
        "iterations": 2000,
        "method": "fixed-common-iteration-count",
        "commonAcrossLevels": True,
    }
    gate = _iteration_control_gate(log, control)
    assert gate["iterations"] == 2000
    assert gate["iterationCountMatchesDeclared"] is True
    assert gate["earlyStopObserved"] is False
    assert gate["passed"] is True

    # A genuine early stop must still fail closed.
    short = log.replace("Exec   : reconstructPar -latestTime\nTime = 2000s\n", "")
    short = "\n".join(short.split("\n")[:-1001])
    early = _iteration_control_gate(short, control)
    assert early["iterationCountMatchesDeclared"] is False
    assert early["passed"] is False

    # A log without banners must count as before, not silently report zero.
    assert _foam_run_log_section("Time = 1s\nTime = 2s\n").count("Time = ") == 2
