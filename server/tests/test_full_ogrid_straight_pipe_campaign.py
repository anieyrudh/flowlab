from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.flowlab.execution import materialize_case_files
from server.flowlab.full_ogrid_straight_pipe_campaign import (
    CASE_ID,
    CONTRACT_SCHEMA,
    FROZEN_SOURCE_PATHS,
    FullOGridCampaignError,
    _reference,
    _sequence_assessment,
    build_evidence_package,
    build_level_case,
    evaluate_completed_level,
    load_contract,
    materialize_campaign,
)
from server.flowlab.results import parse_vtk_result


def _offline_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.flowlab import adapters

    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)


def test_contract_freezes_independent_bounded_full_ogrid_claim() -> None:
    contract = load_contract()
    reference = _reference(contract)

    assert contract["schema"] == CONTRACT_SCHEMA
    assert contract["status"] == "prospective-frozen-before-retained-scientific-execution"
    assert contract["promotionAuthorized"] is False
    assert "axisymmetric validation inheritance" in contract["claim"]["excluded"]
    assert contract["review"]["controlledIndependentReviewRequired"] is True
    assert contract["review"]["benchmarkFixtureMutationAuthorizedBeforeAcceptance"] is False
    assert reference["pressureDropPa"] == pytest.approx(contract["physicalCase"]["analyticPressureDropPa"])
    assert reference["reynoldsNumber"] < contract["physicalCase"]["laminarUpperBound"]

    levels = contract["levels"]
    assert [row["expectedCellCount"] for row in levels] == [3072, 24576, 196608]
    for previous, current in zip(levels, levels[1:]):
        for key in (
            "axialCells",
            "annularRadialCells",
            "circumferentialCells",
            "coreCellsPerSide",
        ):
            assert current[key] == 2 * previous[key]
        assert current["expectedCellCount"] == 8 * previous["expectedCellCount"]


def test_level_case_uses_qoi_stability_product_verification_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_adapters(monkeypatch)
    contract = load_contract()
    case = build_level_case(contract["levels"][0], contract)
    profile = json.loads(case.files["constant/flowlab_full_ogrid_profile.json"])

    assert profile["effectiveMeshMode"] == "full-revolution-five-block-ogrid"
    assert profile["topology"]["blockCount"] == 5
    assert profile["topology"]["collapsedAxisCells"] == 0
    assert profile["verificationContract"]["contractId"] == "straight-circular-pipe-hagen-poiseuille-v2"
    assert profile["verificationContract"]["qoiHistoryWriteIntervalIterations"] == 1
    assert "type codedFixedValue;" in case.files["0/U"]
    assert "targetFlow = 1.0000000000000001e-05;" in case.files["0/U"]
    assert "p               1e-8;" in case.files["system/fvSolution"]
    assert "U               1e-8;" in case.files["system/fvSolution"]
    assert case.files["system/functions"].count("writeControl    timeStep;") >= 4
    assert "writeInterval   1;" in case.files["system/functions"]
    assert "meanVelocityForce" not in "".join(case.files.values())
    assert "fullCircleScale" not in profile["verificationContract"]


def test_foundation_runtime_preserves_v2_qoi_sampling_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.flowlab import execution

    _offline_adapters(monkeypatch)
    contract = load_contract()
    case = build_level_case(contract["levels"][0], contract)
    functions_text = case.files["system/functions"]
    functions_text = execution._foundation_patch_flow_rate_objects(functions_text)
    functions_text = execution._foundation_patch_average_objects(functions_text)

    for object_name in (
        "patchFlowRate_inlet",
        "patchFlowRate_outlet",
        "patchAverage_inlet",
        "patchAverage_outlet",
    ):
        block = execution._control_dict_function_block(functions_text, object_name)
        assert block is not None
        assert "writeControl    timeStep;" in block
        assert "writeInterval   1;" in block


def test_materialization_checks_two_independent_builds_and_all_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_adapters(monkeypatch)
    output = tmp_path / "campaign"
    manifest = materialize_campaign(output)

    assert manifest["caseId"] == CASE_ID
    assert manifest["validated"] is False
    assert manifest["promotionAuthorized"] is False
    assert len(manifest["contractSha256"]) == 64
    assert [row["level"] for row in manifest["levels"]] == ["coarse", "medium", "fine"]
    assert all(row["determinism"]["generatedFileHashesMatch"] for row in manifest["levels"])
    assert [row["mesh"]["cellCount"] for row in manifest["levels"]] == [3072, 24576, 196608]
    assert [row["mesh"]["circumferentialCells"] for row in manifest["levels"]] == [32, 64, 128]
    assert manifest["refinementInterpretation"]["geometryErrorMayNotBeCancelled"] is True
    for level in ("coarse", "medium", "fine"):
        assert (output / "cases" / level / "flowlab_case_manifest.json").is_file()
        assert (output / "cases" / level / "full-ogrid-verification-level.json").is_file()

    with pytest.raises(FullOGridCampaignError, match="refusing to overwrite"):
        materialize_campaign(output)


def test_clean_source_gate_includes_contract_and_not_axisymmetric_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.flowlab import full_ogrid_straight_pipe_campaign as campaign

    commands: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path | None = None) -> SimpleNamespace:
        del cwd
        commands.append(command)
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(campaign, "_run_command", fake_run)
    identity = campaign._source_control_identity()

    assert identity["frozenPathsClean"] is True
    assert "docs/validation/full-ogrid-straight-pipe/VERIFICATION_CONTRACT_V2.json" in FROZEN_SOURCE_PATHS
    assert "server/flowlab/full_ogrid.py" in FROZEN_SOURCE_PATHS
    assert "benchmarks/cases/straight-pipe/benchmark.json" not in FROZEN_SOURCE_PATHS
    assert commands[-1][:4] == ["git", "status", "--porcelain", "--"]


def _vtk_with_analytic_velocity(vtk_text: str, mean_velocity: float, radius: float) -> str:
    dataset = parse_vtk_result(vtk_text)
    points = dataset["points"]
    cells = dataset["cells"]
    vectors = []
    for cell in cells:
        coordinates = [points[index] for index in cell]
        y = sum(point[1] for point in coordinates) / 8.0
        z = sum(point[2] for point in coordinates) / 8.0
        velocity = 2.0 * mean_velocity * max(0.0, 1.0 - (y * y + z * z) / (radius * radius))
        vectors.append((velocity, 0.0, 0.0))
    return (
        vtk_text
        + f"CELL_DATA {len(cells)}\n"
        + "VECTORS U float\n"
        + "\n".join(f"{x:.17g} {y:.17g} {z:.17g}" for x, y, z in vectors)
        + "\n"
    )


def _write_completed_coarse_case(
    case_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], dict[str, object]]:
    _offline_adapters(monkeypatch)
    contract = load_contract()
    level = contract["levels"][0]
    case = build_level_case(level, contract)
    materialize_case_files(case, case_dir)
    rows = ["sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE)."]
    for index in range(50):
        rows.extend(
            [
                f"Time = {index + 1}s",
                "smoothSolver:  Solving for Ux, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "GAMG:  Solving for p, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "time step continuity errors : sum local = 1e-12, global = 1e-12, cumulative = 1e-11",
            ]
        )
    rows.extend(["SIMPLE solution converged in 50 iterations", "End"])
    solver_log = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    solver_log.parent.mkdir(parents=True, exist_ok=True)
    solver_log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (case_dir / "log.checkMesh").write_text(
        """Mesh stats
    points:           3553
    faces:            9664
    internal faces:   8768
    cells:            3072
    hexahedra:        3072
Mesh has 3 geometric (non-empty/wedge) directions (1 1 1)
Mesh has 3 solution (non-empty) directions (1 1 1)
Number of regions: 1 (OK).
Max aspect ratio = 16 OK.
Min volume = 2.6367187e-11. Max volume = 1e-9. Total volume = 2.7e-6. Cell volumes OK.
Mesh non-orthogonality Max: 32.152674 average: 8
Max skewness = 0.55753716 OK.
minimum determinant = 0.10455798
minimum face interpolation weight = 0.16487075
minimum face volume ratio = 0.151744
Mesh OK.
""",
        encoding="utf-8",
    )
    poly_mesh = case_dir / "constant" / "polyMesh"
    poly_mesh.mkdir(parents=True, exist_ok=True)
    (poly_mesh / "boundary").write_text(
        """3
(
inlet
{
    type patch;
    nFaces 192;
    startFace 8768;
}
outlet
{
    type patch;
    nFaces 192;
    startFace 8960;
}
walls
{
    type wall;
    nFaces 512;
    startFace 9152;
}
)
""",
        encoding="utf-8",
    )
    acceptance = json.loads((case_dir / "mesh" / "production_mesh_acceptance.json").read_text())
    acceptance["nativeQualityEvidence"]["solverReports"]["openfoam"]["commandRuns"] = [
        {"command": "blockMesh", "exitCode": 0, "logPath": "log.blockMesh"},
        {"command": "checkMesh", "exitCode": 0, "logPath": "log.checkMesh"},
    ]
    (case_dir / "mesh" / "production_mesh_acceptance.json").write_text(
        json.dumps(acceptance) + "\n",
        encoding="utf-8",
    )
    reference = _reference(contract)
    diagnostics = {
        "schema": "flowlab.openfoam_diagnostics_acceptance.v1",
        "patchMetrics": {
            "flowBalance": {
                "inletFlow": contract["physicalCase"]["volumetricFlowRateM3PerS"],
                "outletFlow": contract["physicalCase"]["volumetricFlowRateM3PerS"],
            },
            "pressureDrops": [
                {
                    "fromPatch": "inlet",
                    "toPatch": "outlet",
                    "deltaP": reference["pressureDropPa"] / contract["physicalCase"]["densityKgPerM3"],
                }
            ],
        },
    }
    diagnostics_path = case_dir / "postProcessing" / "flowlab_diagnostics_acceptance.json"
    diagnostics_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostics_path.write_text(json.dumps(diagnostics) + "\n", encoding="utf-8")
    history_rows = "\n".join(f"{iteration} {{value:.17g}}" for iteration in range(1, 101))
    for object_name, patch_name, value in (
        (
            "patchAverage",
            "inlet",
            reference["pressureDropPa"] / contract["physicalCase"]["densityKgPerM3"],
        ),
        ("patchAverage", "outlet", 0.0),
        (
            "patchFlowRate",
            "inlet",
            -contract["physicalCase"]["volumetricFlowRateM3PerS"],
        ),
        (
            "patchFlowRate",
            "outlet",
            contract["physicalCase"]["volumetricFlowRateM3PerS"],
        ),
    ):
        history_path = (
            case_dir
            / "postProcessing"
            / f"{object_name}_{patch_name}"
            / "0"
            / "surfaceFieldValue.dat"
        )
        history_path.parent.mkdir(parents=True, exist_ok=True)
        history_path.write_text(
            "# Time value\n" + history_rows.format(value=value) + "\n",
            encoding="utf-8",
        )
    vtk_path = case_dir / "VTK" / "case_50.vtk"
    vtk_path.parent.mkdir(parents=True)
    vtk_path.write_text(
        _vtk_with_analytic_velocity(
            case.files["mesh/flowlab_mesh.vtk"],
            reference["meanVelocityMPerS"],
            contract["physicalCase"]["radiusM"],
        ),
        encoding="utf-8",
    )
    return contract, level


def test_completed_level_uses_frozen_mesh_patch_profile_and_conservation_operators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = tmp_path / "case"
    contract, level = _write_completed_coarse_case(case_dir, monkeypatch)
    evaluation = evaluate_completed_level(case_dir, level, contract=contract)

    assert evaluation["allPerLevelGatesPassed"] is True
    assert evaluation["mesh"]["cellCount"] == 3072
    assert evaluation["mesh"]["patches"] == {
        "inlet": {"type": "patch", "nFaces": 192},
        "outlet": {"type": "patch", "nFaces": 192},
        "walls": {"type": "wall", "nFaces": 512},
    }
    assert evaluation["qoi"]["pressureDropRelativeError"] == pytest.approx(0.0)
    assert evaluation["qoi"]["relativeMassFlowImbalance"] == pytest.approx(0.0)
    assert evaluation["qoi"]["velocityProfile"]["relativeL2"] < 1e-12
    assert evaluation["qoi"]["velocityProfile"]["transverseVelocityRmsRatio"] == pytest.approx(0.0)
    assert evaluation["mesh"]["runtimeVtkSpansM"] == pytest.approx([0.024, 0.012, 0.012])
    assert evaluation["solver"]["qoiTailStability"]["pressureDropRelativeSpan"] == pytest.approx(0.0)
    assert evaluation["solver"]["qoiTailStability"]["measuredFlowRateRelativeSpan"] == pytest.approx(0.0)
    assert evaluation["solver"]["residualAndContinuityRole"] == "diagnostic-not-v2-pass-fail-gates"


def test_v2_solver_gate_uses_stable_qoi_not_absolute_transverse_residual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = tmp_path / "case"
    contract, level = _write_completed_coarse_case(case_dir, monkeypatch)
    solver_log = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    solver_log.write_text(
        solver_log.read_text(encoding="utf-8")
        .replace(
            "Final residual = 1e-10",
            "Final residual = 1.4e-7",
        ),
        encoding="utf-8",
    )

    evaluation = evaluate_completed_level(case_dir, level, contract=contract)

    assert evaluation["solver"]["maximumFinalLinearResidual"] == pytest.approx(1.4e-7)
    assert evaluation["gates"]["solver"]["passed"] is True


def test_v2_solver_gate_fails_closed_when_pressure_qoi_tail_is_not_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = tmp_path / "case"
    contract, level = _write_completed_coarse_case(case_dir, monkeypatch)
    history_path = (
        case_dir
        / "postProcessing"
        / "patchAverage_inlet"
        / "0"
        / "surfaceFieldValue.dat"
    )
    rows = ["# Time value"]
    reference_value = (
        _reference(contract)["pressureDropPa"]
        / contract["physicalCase"]["densityKgPerM3"]
    )
    for iteration in range(1, 101):
        multiplier = 1.0 if iteration <= 50 else 1.01
        rows.append(f"{iteration} {reference_value * multiplier:.17g}")
    history_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    evaluation = evaluate_completed_level(case_dir, level, contract=contract)

    assert evaluation["solver"]["qoiTailStability"]["pressureDropRelativeSpan"] > 0.0005
    assert evaluation["gates"]["solver"]["passed"] is False
    assert evaluation["allPerLevelGatesPassed"] is False


def test_completed_level_accepts_native_foundation_checkmesh_metric_wording(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case_dir = tmp_path / "case"
    contract, level = _write_completed_coarse_case(case_dir, monkeypatch)
    check_mesh_path = case_dir / "log.checkMesh"
    native_text = (
        check_mesh_path.read_text(encoding="utf-8")
        .replace(
            "minimum determinant = 0.10455798",
            "Cell determinant (wellposedness) : minimum: 0.36785994 average: 3.2887974",
        )
        .replace(
            "minimum face interpolation weight = 0.16487075",
            "Face interpolation weight : minimum: 0.16487075 average: 0.47528808",
        )
        .replace(
            "minimum face volume ratio = 0.151744",
            "Face volume ratio : minimum: 0.151744 average: 0.89130191",
        )
    )
    check_mesh_path.write_text(native_text, encoding="utf-8")

    evaluation = evaluate_completed_level(case_dir, level, contract=contract)

    assert evaluation["mesh"]["quality"]["minimumCellDeterminant"] == pytest.approx(0.36785994)
    assert evaluation["mesh"]["quality"]["minimumFaceInterpolationWeight"] == pytest.approx(0.16487075)
    assert evaluation["mesh"]["quality"]["minimumFaceVolumeRatio"] == pytest.approx(0.151744)


def test_sequence_assessment_reports_combined_geometry_and_solution_gci() -> None:
    contract = load_contract()
    deficits = [
        1.0 - count * math.sin(2.0 * math.pi / count) / (2.0 * math.pi)
        for count in (32, 64, 128)
    ]
    evaluations = {}
    for level, h, pressure, deficit in zip(
        ("coarse", "medium", "fine"),
        (0.004, 0.002, 0.001),
        (1.16, 1.04, 1.01),
        deficits,
        strict=True,
    ):
        evaluations[level] = {
            "characteristicCellSizeM": h,
            "provenance": {"runtimeVtkSha256": hashlib.sha256(level.encode()).hexdigest()},
            "qoi": {"pressureDropPa": pressure},
            "mesh": {"wallGeometry": {"areaRelativeDeficit": deficit}},
        }

    assessment = _sequence_assessment(evaluations, contract)

    assert assessment["gridConvergence"]["qualified"] is True
    assert assessment["gridConvergence"]["observedOrder"] == pytest.approx(2.0)
    assert assessment["gates"]["polygonAreaDeficitMonotone"] is True
    assert assessment["gates"]["polygonAreaDeficitRatios"] is True
    assert "combined solution-discretization and wall-geometry-realization" in assessment["interpretation"]


def test_partial_failure_package_is_immutable_review_candidate_not_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_adapters(monkeypatch)
    campaign_dir = tmp_path / "campaign"
    manifest = materialize_campaign(campaign_dir)
    job_dir = campaign_dir / "runtime" / "jobs" / "job-coarse"
    case_dir = job_dir / "case"
    contract, level = _write_completed_coarse_case(case_dir, monkeypatch)
    (case_dir / "constant" / "flowlab_openfoam_runtime.json").write_text(
        json.dumps(
            {
                "schema": "flowlab.openfoam_runtime_detection.v1",
                "detectedStyle": "foundation",
                "detectedVersion": "11",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (job_dir / "flowlab_job_record.json").write_text(
        json.dumps({"id": "job-coarse", "status": "complete", "exitCode": 0}) + "\n",
        encoding="utf-8",
    )
    (job_dir / "flowlab_case_record.json").write_text(
        json.dumps({"jobId": "job-coarse", "caseId": "case-coarse"}) + "\n",
        encoding="utf-8",
    )
    evaluation = evaluate_completed_level(case_dir, level, contract=contract)
    evaluation["characteristicCellSizeM"] = manifest["levels"][0]["mesh"]["characteristicCellSizeM"]
    evaluation_path = campaign_dir / "evaluations" / "coarse.json"
    evaluation_path.parent.mkdir(parents=True)
    evaluation_path.write_text(json.dumps(evaluation) + "\n", encoding="utf-8")
    campaign_result = {
        "schema": "flowlab.full-ogrid-straight-pipe-run-result.v2",
        "caseId": CASE_ID,
        "status": "scientific-gate-failed-retained",
        "scientificStatus": "verification-candidate-gates-failed",
        "validated": False,
        "promotionAuthorized": False,
        "sourceControl": {"commit": "a" * 40},
        "runtimeEnvironment": {
            "container": {
                "imageTag": "flowlab/openfoam11-gmsh:2026-07-13",
                "imageId": "sha256:" + "b" * 64,
            }
        },
        "levels": [
            {
                "level": "coarse",
                "caseDirectory": str(case_dir.relative_to(campaign_dir)),
                "evaluationPath": str(evaluation_path.relative_to(campaign_dir)),
            }
        ],
    }
    (campaign_dir / "campaign-result.json").write_text(
        json.dumps(campaign_result) + "\n",
        encoding="utf-8",
    )

    package = build_evidence_package(campaign_dir, freeze=False)

    assert package["status"] == "candidate-gates-failed"
    assert package["allCandidateGatesPassed"] is False
    assert package["campaignGates"]["threeLevelsComplete"] is False
    assert package["fineLevelGates"]["evaluated"] is False
    assert package["sequenceAssessment"]["gridConvergence"]["qualified"] is False
    assert package["promotionAuthorized"] is False
    assert package["fixtureMutationAuthorized"] is False
    assert (campaign_dir / "immutable-evidence-package" / "package-manifest.json").is_file()
    review = json.loads((campaign_dir / "independent-review-request.json").read_text())
    assert review["status"] == "pending-controlled-independent-review"
    assert review["packageTreeDigestSha256"] == package["treeDigestSha256"]
