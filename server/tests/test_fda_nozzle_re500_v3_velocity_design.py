from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab import fda_nozzle_re500_v3_velocity_design as design


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    ROOT
    / "docs/validation/fda-nozzle-re500/V3_VELOCITY_VERIFICATION_DESIGN_CONTRACT.json"
)


def test_frozen_velocity_design_passes_offline_validator() -> None:
    assessment = design.validate_design(CONTRACT)
    assert assessment["status"] == "design-valid-execution-blocked"
    assert all(assessment["checks"].values())
    assert assessment["nextAuthorizedWork"] == (
        "independent-review-of-design-and-separate-execution-authorization"
    )
    assert assessment["solverExecutionAuthorized"] is False
    assert assessment["promotionAuthorized"] is False


def test_design_uses_every_critical_functional_not_a_ninety_percent_vote() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    acceptance = contract["acceptance"]
    assert acceptance["everyCriticalFunctionalMustQualify"] is True
    assert acceptance["everyCriticalFunctionalMustPass"] is True
    assert acceptance["everyEligibleCentrelinePointMustQualify"] is True
    assert acceptance["everyEligibleCentrelinePointMustPass"] is True
    assert acceptance["historicalAggregatePassFractionGate"] is None
    assert acceptance["pointwisePassFractionMayAuthorize"] is False
    assert contract["observationDesign"]["stationFunctionalCount"] == 18


def test_design_preserves_pressure_as_nonpromotional() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    pressure = contract["pressurePolicy"]
    assert pressure["status"] == "mandatory-diagnostic-nonpromotional"
    assert pressure["mayAuthorizePromotion"] is False
    assert pressure["mayRescueVelocityPromotion"] is False
    assert pressure["mayDefeatVelocityPromotion"] is False


def test_case_matrix_and_compute_estimate_are_exact() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert design._case_matrix(contract) == design.EXPECTED_CASES
    assert design._numbers_close(
        contract["computeEstimate"], design.expected_compute_estimate()
    )
    assert contract["computeEstimate"]["baselineSolverHours"] == pytest.approx(
        7.1511801534351145
    )
    assert contract["computeEstimate"]["plannedWallClockHours"] == pytest.approx(
        12.872124276183206
    )
    assert contract["computeEstimate"]["estimateIsAuthorization"] is False


def test_any_execution_authorization_fails_design_validation(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["authorization"]["runSolver"] = True
    tampered = tmp_path / "tampered-contract.json"
    tampered.write_text(json.dumps(contract), encoding="utf-8")
    assessment = design.validate_design(tampered)
    assert assessment["status"] == "design-invalid-fail-closed"
    assert assessment["checks"]["executionFailsClosed"] is False
    assert assessment["solverExecutionAuthorized"] is False


def test_design_assessment_refuses_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "assessment.json"
    report = tmp_path / "REPORT.md"
    assessment = design.write_assessment(CONTRACT, output, report)
    assert assessment["status"] == "design-valid-execution-blocked"
    with pytest.raises(ValueError, match="refusing to overwrite"):
        design.write_assessment(CONTRACT, output, report)


def test_validator_has_no_execution_command_surface() -> None:
    source = Path(design.__file__).read_text(encoding="utf-8")
    assert "import subprocess" not in source
    assert "docker run" not in source
    assert not hasattr(design, "prepare_campaign")
    assert not hasattr(design, "execute_case")
    assert not hasattr(design, "run_solver")
