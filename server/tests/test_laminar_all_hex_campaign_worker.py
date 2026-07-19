from __future__ import annotations

import math

from server.flowlab.laminar_all_hex_campaign_worker import (
    mms_cell_checks,
    physical_cell_checks,
    physical_spec,
)


def test_physical_spec_hits_target_reynolds_and_reverses_sign() -> None:
    forward = physical_spec(
        {
            "reynoldsNumberHeightBased": 16.67,
            "flowDirectionSign": 1,
            "lengthToHeightRatio": 4.0,
        }
    )
    reverse = physical_spec(
        {
            "reynoldsNumberHeightBased": 16.67,
            "flowDirectionSign": -1,
            "lengthToHeightRatio": 4.0,
        }
    )

    assert math.isclose(forward.reynolds_number, 16.67)
    assert math.isclose(reverse.reynolds_number, 16.67)
    assert forward.pressure_gradient_m_s2 == -reverse.pressure_gradient_m_s2
    assert forward.analytic_wall_viscous_force[0] == -reverse.analytic_wall_viscous_force[0]


def _physical_observation() -> dict:
    return {
        "checkMeshPassed": True,
        "solverExitCode": 0,
        "directAuditExitCode": 0,
        "massRelativeImbalance": 1.0e-12,
        "finalLinearResidual": 1.0e-12,
        "finalAxialInitialResidual": 1.0e-8,
        "finalPressureInitialResidual": 1.0e-10,
        "transverseVelocityRelativeL2Error": 1.0e-8,
        "velocityRelativeL2Error": 1.0e-3,
        "pressureRelativeL2Error": 1.0e-3,
        "forceComparison": {
            "openForceObjectVsDirectAbsolute": 1.0e-14,
            "wallForceObjectVsDirectAbsolute": 1.0e-14,
            "openPressureForceRelativeError": 1.0e-12,
            "wallViscousForceRelativeError": 1.0e-3,
            "totalMomentumRelativeImbalance": 1.0e-3,
        },
        "faceComparison": {"maxViscousTractionRelativeError": 1.0e-3},
    }


def test_physical_cell_fine_checks_include_fields_faces_and_momentum() -> None:
    checks = physical_cell_checks(_physical_observation(), level="fine")

    assert all(checks.values())
    assert "fineFields" in checks
    assert "fineFaceViscousTraction" in checks
    assert "fineMomentumBalance" in checks


def test_mms_cell_checks_fail_non_finite_errors() -> None:
    checks = mms_cell_checks(
        {
            "checkMeshPassed": True,
            "solverExitCode": 0,
            "massRelativeImbalance": 0.0,
            "finalLinearResidual": 0.0,
            "finalNonlinearResidual": 0.0,
            "velocityRelativeL2Error": math.inf,
            "pressureRelativeL2Error": 1.0e-3,
        }
    )

    assert checks["finiteVelocityError"] is False
    assert checks["finitePressureError"] is True
