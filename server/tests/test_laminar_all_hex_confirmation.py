from __future__ import annotations

from server.flowlab.laminar_all_hex_confirmation import compare_confirmation


def _report(cell_id: str, pressure: float = 2.0e-8) -> dict:
    return {
        "cellId": cell_id,
        "status": "rejected-scientific",
        "failedChecks": ["pressureResidual"],
        "observation": {
            "finalAxialInitialResidual": 1.0e-7,
            "finalPressureInitialResidual": pressure,
            "finalLinearResidual": 1.0e-13,
            "massRelativeImbalance": 1.0e-13,
            "velocityRelativeL2Error": 1.0e-3,
            "pressureRelativeL2Error": 1.0e-5,
        },
    }


def test_confirmation_requires_same_failure_and_numeric_signature() -> None:
    report = compare_confirmation(_report("source"), _report("confirmation"))

    assert report["status"] == "confirmed"
    assert all(report["checks"].values())


def test_confirmation_rejects_numeric_drift() -> None:
    report = compare_confirmation(
        _report("source"), _report("confirmation", pressure=3.0e-8)
    )

    assert report["status"] == "not-confirmed"
    assert report["checks"]["numericSignatureMatches"] is False
