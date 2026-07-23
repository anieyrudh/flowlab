from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from server.flowlab.axisymmetric_straight_pipe_campaign import (
    AxisymmetricStraightPipeCampaignError,
    AxisymmetricStraightPipeSpec,
    LEVELS,
    build_level_case,
    evaluate_completed_level,
    materialize_campaign,
)


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
