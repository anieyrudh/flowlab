from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab.open_boundary_campaign import MmsDefinition, RefinementObservation
from server.flowlab.open_boundary_mms_runner import (
    _case_files,
    _coarse_advancement_report,
    _inlet_pressure_advancement_report,
)


def _observation(*, velocity_error: float, pressure_error: float) -> RefinementObservation:
    return RefinementObservation(
        level="coarse",
        effective_spacing=1 / 12,
        check_mesh_passed=True,
        velocity_l2_error=velocity_error,
        pressure_l2_error=pressure_error,
        axial_velocity_l2_error=velocity_error,
        transverse_velocity_l2_error=0.0,
        mass_relative_imbalance=1.0e-12,
        final_linear_residual=1.0e-10,
        boundary_traction_relative_imbalance=1.0e-8,
        artifacts={"solver": "foamRun.log"},
    )


def test_case_files_preserve_original_fixed_value_outlet_by_default() -> None:
    files = _case_files(12, MmsDefinition())

    assert "outlet { type fixedValue; value uniform (1 0 0); }" in files["0/U"]
    assert "pressureInletOutletVelocity" not in files["0/U"]
    implementation = json.loads(files["boundary-implementation.json"])
    assert implementation["outletPressure"] == "fixedValue"
    assert implementation["outletVelocity"] == "fixedValue"


def test_case_files_can_release_only_outlet_velocity() -> None:
    files = _case_files(
        12,
        MmsDefinition(),
        outlet_velocity_type="pressureInletOutletVelocity",
    )

    assert "outlet { type pressureInletOutletVelocity; value uniform (1 0 0); }" in files["0/U"]
    assert "outlet { type fixedValue; value uniform 0; }" in files["0/p"]
    implementation = json.loads(files["boundary-implementation.json"])
    assert implementation["outletVelocity"] == "pressureInletOutletVelocity"


def test_case_files_can_change_only_inlet_pressure_to_fixed_flux() -> None:
    common = {
        "outlet_velocity_type": "pressureInletOutletVelocity",
        "force_write_interval": 1,
    }
    control = _case_files(12, MmsDefinition(), **common)  # type: ignore[arg-type]
    changed = _case_files(
        12,
        MmsDefinition(),
        inlet_pressure_type="fixedFluxPressure",
        **common,  # type: ignore[arg-type]
    )

    assert "inlet { type fixedFluxPressure; value uniform 0.001; }" in changed["0/p"]
    assert "outlet { type fixedValue; value uniform 0; }" in changed["0/p"]
    assert changed["0/U"] == control["0/U"]
    implementation = json.loads(changed["boundary-implementation.json"])
    assert implementation["inletPressure"] == "fixedFluxPressure"
    assert implementation["inletPressureAnalyticInitialValue"] == 0.001
    for name in control:
        if name not in {"0/p", "boundary-implementation.json"}:
            assert changed[name] == control[name]


def test_case_files_can_change_only_inlet_pressure_to_analytic_fixed_gradient() -> None:
    common = {
        "outlet_velocity_type": "pressureInletOutletVelocity",
        "force_write_interval": 1,
    }
    control = _case_files(12, MmsDefinition(), **common)  # type: ignore[arg-type]
    changed = _case_files(
        12,
        MmsDefinition(),
        inlet_pressure_type="fixedGradient",
        **common,  # type: ignore[arg-type]
    )

    assert "inlet { type fixedGradient; gradient uniform 0.001; }" in changed["0/p"]
    assert "outlet { type fixedValue; value uniform 0; }" in changed["0/p"]
    assert changed["0/U"] == control["0/U"]
    implementation = json.loads(changed["boundary-implementation.json"])
    assert implementation["inletPressure"] == "fixedGradient"
    assert implementation["inletPressureAnalyticNormalGradient"] == 0.001
    assert "inletPressureAnalyticInitialValue" not in implementation
    for name in control:
        if name not in {"0/p", "boundary-implementation.json"}:
            assert changed[name] == control[name]


def test_case_files_support_matrix_boundary_and_solver_factors() -> None:
    files = _case_files(
        12,
        MmsDefinition(),
        outlet_velocity_type="zeroGradient",
        simple_consistent=False,
        u_solver_type="PBiCGStab",
    )

    assert "outlet { type zeroGradient; }" in files["0/U"]
    assert "solver PBiCGStab; preconditioner DILU;" in files["system/fvSolution"]
    assert "consistent no;" in files["system/fvSolution"]
    controls = json.loads(files["solver-controls.json"])
    assert controls["simpleConsistent"] is False
    assert controls["uSolverType"] == "PBiCGStab"


def test_case_files_can_sample_forces_every_iteration_without_changing_solve() -> None:
    default = _case_files(12, MmsDefinition(), outlet_velocity_type="pressureInletOutletVelocity")
    diagnostic = _case_files(
        12,
        MmsDefinition(),
        outlet_velocity_type="pressureInletOutletVelocity",
        force_write_interval=1,
    )

    assert "forces {" in diagnostic["system/controlDict"]
    assert "writeInterval 1;" in diagnostic["system/controlDict"]
    sampling = json.loads(diagnostic["diagnostic-sampling.json"])
    assert sampling == {
        "changesSolve": False,
        "forcesWriteInterval": 1,
        "residualsWriteInterval": 1,
        "schema": "flowlab.open-boundary-mms-diagnostic-sampling.v1",
    }
    for name in (
        "0/U",
        "0/p",
        "constant/fvModels",
        "system/blockMeshDict",
        "system/fvSchemes",
        "system/fvSolution",
    ):
        assert diagnostic[name] == default[name]


def test_case_files_reject_unknown_outlet_velocity_type() -> None:
    with pytest.raises(ValueError, match="unsupported outlet velocity type"):
        _case_files(12, MmsDefinition(), outlet_velocity_type="inletOutlet")  # type: ignore[arg-type]


def test_case_files_reject_unknown_inlet_pressure_type() -> None:
    with pytest.raises(ValueError, match="unsupported inlet pressure type"):
        _case_files(12, MmsDefinition(), inlet_pressure_type="zeroGradient")  # type: ignore[arg-type]


def test_case_files_reject_nonpositive_force_write_interval() -> None:
    with pytest.raises(ValueError, match="force write interval must be positive"):
        _case_files(12, MmsDefinition(), force_write_interval=0)


def test_case_files_can_change_only_u_equation_relaxation() -> None:
    control = _case_files(
        12,
        MmsDefinition(),
        outlet_velocity_type="pressureInletOutletVelocity",
        force_write_interval=1,
    )
    stabilized = _case_files(
        12,
        MmsDefinition(),
        outlet_velocity_type="pressureInletOutletVelocity",
        force_write_interval=1,
        u_equation_relaxation=0.9,
    )

    assert "relaxationFactors { equations { U 1; } }" in control["system/fvSolution"]
    assert "relaxationFactors { equations { U 0.9; } }" in stabilized["system/fvSolution"]
    assert json.loads(stabilized["solver-controls.json"])["uEquationRelaxation"] == 0.9
    for name in control:
        if name not in {"system/fvSolution", "solver-controls.json"}:
            assert stabilized[name] == control[name]


@pytest.mark.parametrize("relaxation", [0.0, -0.1, 1.1])
def test_case_files_reject_invalid_u_equation_relaxation(relaxation: float) -> None:
    with pytest.raises(ValueError, match=r"U equation relaxation must be in \(0, 1\]"):
        _case_files(12, MmsDefinition(), u_equation_relaxation=relaxation)


def test_case_files_reject_unknown_u_solver() -> None:
    with pytest.raises(ValueError, match="unsupported U solver type"):
        _case_files(12, MmsDefinition(), u_solver_type="PCG")  # type: ignore[arg-type]


def test_coarse_gate_only_authorizes_three_grid_after_all_checks_improve(tmp_path: Path) -> None:
    baseline = {"velocity_l2_error": 0.0013, "pressure_l2_error": 0.086}
    report = _coarse_advancement_report(
        _observation(velocity_error=0.0012, pressure_error=0.080),
        baseline,
        baseline_path=tmp_path / "control.json",
        outlet_velocity_type="pressureInletOutletVelocity",
    )

    assert report["status"] == "advance"
    assert report["validated"] is False
    assert report["nextStage"]["forcedMmsThreeGrid"] == "authorized"


def test_coarse_gate_blocks_when_either_field_error_regresses(tmp_path: Path) -> None:
    baseline = {"velocity_l2_error": 0.0013, "pressure_l2_error": 0.086}
    report = _coarse_advancement_report(
        _observation(velocity_error=0.0012, pressure_error=0.087),
        baseline,
        baseline_path=tmp_path / "control.json",
        outlet_velocity_type="pressureInletOutletVelocity",
    )

    assert report["status"] == "blocked"
    assert report["failedChecks"] == ["pressureErrorImprovedVsControl"]
    assert report["nextStage"]["forcedMmsThreeGrid"] == "blocked"


def test_inlet_pressure_gate_authorizes_only_after_every_coarse_check(tmp_path: Path) -> None:
    baseline = {
        "velocity_l2_error": 1.6e-5,
        "pressure_l2_error": 0.027,
        "boundary_traction_relative_imbalance": 1.33e-6,
    }
    report = _inlet_pressure_advancement_report(
        _observation(velocity_error=1.5e-5, pressure_error=0.026),
        baseline,
        baseline_path=tmp_path / "v12-observation.json",
        inlet_pressure_type="fixedFluxPressure",
        exact_inlet_pressure=0.001,
    )

    assert report["status"] == "advance"
    assert all(report["checks"].values())
    assert report["singleBoundaryImplementationChange"]["field"] == "p"
    assert report["singleBoundaryImplementationChange"]["patch"] == "inlet"
    assert report["nextStage"]["forcedMmsThreeGrid"] == "authorized"
    assert report["nextStage"]["threeGridExecuted"] is False


def test_inlet_pressure_gate_blocks_on_a_field_error_regression(tmp_path: Path) -> None:
    baseline = {
        "velocity_l2_error": 1.6e-5,
        "pressure_l2_error": 0.027,
        "boundary_traction_relative_imbalance": 1.33e-6,
    }
    report = _inlet_pressure_advancement_report(
        _observation(velocity_error=1.5e-5, pressure_error=0.028),
        baseline,
        baseline_path=tmp_path / "v12-observation.json",
        inlet_pressure_type="fixedFluxPressure",
        exact_inlet_pressure=0.001,
    )

    assert report["status"] == "blocked"
    assert report["failedChecks"] == ["pressureErrorImprovedVsControl"]
    assert report["nextStage"]["forcedMmsThreeGrid"] == "blocked"


def test_fixed_gradient_gate_records_analytic_normal_gradient(tmp_path: Path) -> None:
    baseline = {
        "velocity_l2_error": 1.6e-5,
        "pressure_l2_error": 0.027,
        "boundary_traction_relative_imbalance": 1.33e-6,
    }
    report = _inlet_pressure_advancement_report(
        _observation(velocity_error=1.5e-5, pressure_error=0.026),
        baseline,
        baseline_path=tmp_path / "v12-observation.json",
        inlet_pressure_type="fixedGradient",
        exact_inlet_pressure=0.001,
    )

    change = report["singleBoundaryImplementationChange"]
    assert change["after"] == "fixedGradient"
    assert change["analyticNormalGradient"] == 0.001
    assert "analyticInitialValue" not in change
    assert report["nextStage"]["threeGridExecuted"] is False
