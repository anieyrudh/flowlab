from __future__ import annotations

from server.flowlab.laminar_all_hex_campaign_runner import (
    _aggregate_physical,
    _convergence_checks,
)


def test_convergence_checks_accept_second_order_sequence() -> None:
    result = {
        "relativeL2Errors": [0.04, 0.01, 0.0025],
        "observedOrder": {"coarseToMedium": 2.0, "mediumToFine": 2.0},
        "orderSpread": 0.0,
        "gciRelativeToAnalyticFieldNorm": {"fine": 0.003},
    }

    assert all(_convergence_checks("velocity", result).values())


def test_physical_aggregate_requires_all_24_points_and_72_cells() -> None:
    report = _aggregate_physical([])

    assert report["status"] == "rejected"
    assert report["checks"]["all24OperatingPointsPresent"] is False
    assert report["checks"]["all72CellsPresent"] is False
