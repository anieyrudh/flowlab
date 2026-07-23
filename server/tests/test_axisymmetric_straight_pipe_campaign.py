from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from server.flowlab.axisymmetric_straight_pipe_campaign import (
    AxisymmetricStraightPipeCampaignError,
    AxisymmetricStraightPipeSpec,
    LEVELS,
    RUN_RESULT_SCHEMA,
    build_level_case,
    build_evidence_package,
    evaluate_completed_level,
    materialize_campaign,
)
from server.flowlab.execution import JOB_RECORD_FILENAME, materialize_case_files


def _write_completed_level(
    case_dir: Path,
    *,
    level: str,
    axial_cells: int,
    radial_cells: int,
    pressure_drop_factor: float,
) -> tuple[dict[str, object], str]:
    spec = AxisymmetricStraightPipeSpec()
    case = build_level_case(
        spec,
        level=level,
        axial_cells=axial_cells,
        radial_cells=radial_cells,
    )
    materialize_case_files(case, case_dir)
    profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
    contract = profile["benchmarkContract"]
    target_velocity = contract["meanVelocityTargetMPerS"]
    pressure_drop = spec.reference()["pressureDropPa"] * pressure_drop_factor
    gradient = pressure_drop / (spec.length_m * spec.density_kg_m3)
    wedge_flow = spec.volumetric_flow_rate_m3_s / contract["fullCircleScale"]
    rows: list[str] = []
    for index in range(50):
        rows.extend(
            [
                f"Time = {index + 1}s",
                (
                    "Pressure gradient source: uncorrected Ubar = "
                    f"{target_velocity:.17g}, pressure gradient = {gradient:.17g}"
                ),
                (
                    "smoothSolver:  Solving for Ux, Initial residual = 1e-9, "
                    "Final residual = 1e-10, No Iterations 1"
                ),
                (
                    "smoothSolver:  Solving for Uy, Initial residual = 1e-9, "
                    "Final residual = 1e-10, No Iterations 1"
                ),
                (
                    "smoothSolver:  Solving for Uz, Initial residual = 1e-9, "
                    "Final residual = 1e-10, No Iterations 1"
                ),
                (
                    "GAMG:  Solving for p, Initial residual = 1e-9, "
                    "Final residual = 1e-10, No Iterations 1"
                ),
                (
                    "time step continuity errors : sum local = 1e-14, "
                    "global = 1e-15, cumulative = 1e-13"
                ),
            ]
        )
    rows.extend(
        [
            f"sum(inlet) of phi = {-wedge_flow:.17g}",
            f"sum(outlet) of phi = {wedge_flow:.17g}",
            "End",
        ]
    )
    solver_log = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    solver_log.parent.mkdir(parents=True)
    solver_log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (case_dir / "log.checkMesh").write_text(
        f"""
cells: {axial_cells * radial_cells}
inlet outlet walls front back axis
Mesh has 3 solution (non-empty) directions (1 1 1)
Mesh OK.
""",
        encoding="utf-8",
    )
    poly_mesh = case_dir / "constant" / "polyMesh"
    poly_mesh.mkdir(parents=True)
    (poly_mesh / "boundary").write_text("retained test boundary\n", encoding="utf-8")
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
    vtk_path = case_dir / "VTK" / "case_50.vtk"
    vtk_path.parent.mkdir(parents=True)
    vtk_path.write_text(case.files["mesh/flowlab_mesh.vtk"], encoding="utf-8")
    return evaluate_completed_level(case_dir), case.id


def test_axisymmetric_straight_pipe_level_uses_product_periodic_wedge(monkeypatch: pytest.MonkeyPatch) -> None:
    from server.flowlab import adapters

    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    case = build_level_case(
        AxisymmetricStraightPipeSpec(),
        level="coarse",
        axial_cells=16,
        radial_cells=4,
    )

    profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
    contract = profile["benchmarkContract"]
    assert profile["totalLengthM"] == pytest.approx(0.024)
    assert profile["nRadial"] == 4
    assert profile["segments"][0]["nAxial"] == 16
    assert profile["boundaryRoles"]["inlet"] == "cyclic"
    assert contract["fixtureId"] == "straight-pipe"
    assert contract["fixtureStatus"] == "pending-real-run"
    assert contract["fullCircleScale"] == pytest.approx(72.0)
    assert contract["wedgeCrossSectionAreaM2"] == pytest.approx(
        0.006**2 * math.tan(math.radians(2.5))
    )
    assert (
        contract["meanVelocityTargetMPerS"]
        * contract["wedgeCrossSectionAreaM2"]
        * contract["fullCircleScale"]
        == pytest.approx(1e-5)
    )
    assert contract["reynoldsNumber"] < 2100
    assert "type cyclic;" in case.files["system/blockMeshDict"]
    assert "neighbourPatch outlet;" in case.files["system/blockMeshDict"]
    assert "type            meanVelocityForce;" in case.files["system/fvConstraints"]
    assert "type            cyclic;" in case.files["0/U"]
    assert "PIMPLE" not in case.files["system/fvSolution"]
    assert "residualControl" in case.files["system/fvSolution"]


def test_materialize_axisymmetric_campaign_freezes_three_levels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.flowlab import adapters

    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    output_dir = tmp_path / "campaign"
    manifest = materialize_campaign(output_dir)

    assert manifest["status"] == "materialized-pending-real-run"
    assert manifest["fixtureStatus"] == "pending-real-run"
    assert manifest["validated"] is False
    assert manifest["promotionAuthorized"] is False
    assert [level["level"] for level in manifest["levels"]] == [item[0] for item in LEVELS]
    assert [level["mesh"]["characteristicCellSizeM"] for level in manifest["levels"]] == pytest.approx(
        [0.0015, 0.00075, 0.000375]
    )
    for level, axial_cells, radial_cells in LEVELS:
        case_dir = output_dir / "cases" / level
        assert (case_dir / "Allrun").is_file()
        assert (case_dir / "flowlab_case_manifest.json").is_file()
        level_manifest = json.loads((case_dir / "axisymmetric-benchmark-level.json").read_text(encoding="utf-8"))
        assert level_manifest["mesh"]["axialCells"] == axial_cells
        assert level_manifest["mesh"]["radialCells"] == radial_cells
        assert level_manifest["scientificStatus"] == "experimental-candidate"

    with pytest.raises(AxisymmetricStraightPipeCampaignError, match="refusing to overwrite"):
        materialize_campaign(output_dir)


def test_completed_level_evaluator_recomputes_physical_qois(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.flowlab import adapters
    from server.flowlab.execution import materialize_case_files

    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    spec = AxisymmetricStraightPipeSpec()
    case = build_level_case(spec, level="coarse", axial_cells=16, radial_cells=4)
    case_dir = tmp_path / "case"
    materialize_case_files(case, case_dir)
    profile = json.loads(case.files["constant/flowlab_axisymmetric_profile.json"])
    contract = profile["benchmarkContract"]
    target_velocity = contract["meanVelocityTargetMPerS"]
    gradient = spec.reference()["pressureDropPa"] / (spec.length_m * spec.density_kg_m3)
    wedge_flow = spec.volumetric_flow_rate_m3_s / contract["fullCircleScale"]
    rows: list[str] = []
    for index in range(50):
        rows.extend(
            [
                f"Time = {index + 1}s",
                f"Pressure gradient source: uncorrected Ubar = {target_velocity:.17g}, pressure gradient = {gradient:.17g}",
                "smoothSolver:  Solving for Ux, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "smoothSolver:  Solving for Uy, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "smoothSolver:  Solving for Uz, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "GAMG:  Solving for p, Initial residual = 1e-9, Final residual = 1e-10, No Iterations 1",
                "time step continuity errors : sum local = 1e-14, global = 1e-15, cumulative = 1e-13",
            ]
        )
    rows.extend(
        [
            f"sum(inlet) of phi = {-wedge_flow:.17g}",
            f"sum(outlet) of phi = {wedge_flow:.17g}",
            "End",
        ]
    )
    solver_log = case_dir / "postProcessing" / "solverLogs" / "solve.log"
    solver_log.parent.mkdir(parents=True)
    solver_log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (case_dir / "log.checkMesh").write_text(
        """
cells: 64
inlet outlet walls front back axis
Mesh has 3 solution (non-empty) directions (1 1 1)
Mesh OK.
""",
        encoding="utf-8",
    )
    vtk_path = case_dir / "VTK" / "case_50.vtk"
    vtk_path.parent.mkdir(parents=True)
    vtk_path.write_text(case.files["mesh/flowlab_mesh.vtk"], encoding="utf-8")

    result = evaluate_completed_level(case_dir)

    assert result["allNumericalConvergenceGatesPassed"] is True
    assert result["validated"] is False
    assert result["qoi"]["pressureDropPa"] == pytest.approx(spec.reference()["pressureDropPa"])
    assert result["qoi"]["flowRateRelativeError"] == pytest.approx(0.0, abs=1e-14)
    assert result["qoi"]["relativeMassFlowImbalance"] == pytest.approx(0.0, abs=1e-14)
    assert result["geometryRealization"]["runtimeMeshSource"] == "solver-produced-polyMesh"


def test_completed_campaign_builds_content_hashed_candidate_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server.flowlab import adapters

    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)
    campaign_dir = tmp_path / "campaign"
    manifest = materialize_campaign(campaign_dir)
    factors = {"coarse": 0.97, "medium": 0.9925, "fine": 0.998125}
    level_records: list[dict[str, object]] = []
    for level, axial_cells, radial_cells in LEVELS:
        job_id = f"job-{level}"
        job_dir = campaign_dir / "runtime" / "jobs" / job_id
        case_dir = job_dir / "case"
        evaluation, case_id = _write_completed_level(
            case_dir,
            level=level,
            axial_cells=axial_cells,
            radial_cells=radial_cells,
            pressure_drop_factor=factors[level],
        )
        (job_dir / JOB_RECORD_FILENAME).write_text(
            json.dumps(
                {
                    "id": job_id,
                    "caseId": case_id,
                    "solver": "openfoam",
                    "status": "complete",
                    "exitCode": 0,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (job_dir / "flowlab_case_record.json").write_text(
            json.dumps({"id": case_id, "solver": "openfoam"}) + "\n",
            encoding="utf-8",
        )
        evaluation.update(
            {
                "level": level,
                "jobId": job_id,
                "caseId": case_id,
                "characteristicCellSizeM": next(
                    item["mesh"]["characteristicCellSizeM"]
                    for item in manifest["levels"]
                    if item["level"] == level
                ),
            }
        )
        evaluation_path = campaign_dir / "evaluations" / f"{level}.json"
        evaluation_path.parent.mkdir(parents=True, exist_ok=True)
        evaluation_path.write_text(
            json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        level_records.append(
            {
                "level": level,
                "jobId": job_id,
                "caseId": case_id,
                "status": "complete",
                "exitCode": 0,
                "caseDirectory": str(case_dir.relative_to(campaign_dir)),
                "evaluationPath": str(evaluation_path.relative_to(campaign_dir)),
                "allNumericalConvergenceGatesPassed": True,
            }
        )

    run_result = {
        "schema": RUN_RESULT_SCHEMA,
        "fixtureId": "straight-pipe",
        "status": "completed-evaluated-experimental-candidate",
        "scientificStatus": "experimental-candidate",
        "validated": False,
        "promotionAuthorized": False,
        "sourceControl": {"commit": "a" * 40},
        "runtimeEnvironment": {
            "container": {
                "imageTag": "flowlab/openfoam11-gmsh:2026-07-13",
                "imageId": f"sha256:{'b' * 64}",
            }
        },
        "levels": level_records,
    }
    (campaign_dir / "campaign-result.json").write_text(
        json.dumps(run_result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    package = build_evidence_package(campaign_dir, freeze=False)

    assert package["allCandidateGatesPassed"] is True
    assert package["candidateGates"] == {
        "allLevelConvergence": True,
        "fineGridGci": True,
        "finePressureDropError": True,
        "fineMassBalance": True,
    }
    assert package["gridConvergence"]["observedOrder"] == pytest.approx(2.0)
    assert package["gridConvergence"]["fineGridGciPercent"] < 1.0
    package_dir = campaign_dir / "immutable-evidence-package"
    assert (package_dir / "case-manifest.tar").is_file()
    assert (package_dir / "mesh-artifact.tar").is_file()
    assert (package_dir / "raw-result-fields.tar").is_file()
    assert (package_dir / "qoi-extraction.json").is_file()
    assert (package_dir / "evidence.json").is_file()
    assert (package_dir / "artifact-index.json").is_file()
    assert (campaign_dir / "independent-review-request.json").is_file()
    with pytest.raises(AxisymmetricStraightPipeCampaignError, match="refusing to overwrite"):
        build_evidence_package(campaign_dir, freeze=False)
