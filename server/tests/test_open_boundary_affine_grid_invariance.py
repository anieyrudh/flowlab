from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab.open_boundary_affine_grid_invariance import (
    _authorized_upstream,
    _level_summary,
)
from server.flowlab.open_boundary_affine_pressure_tangential_coarse import SCHEMA


def _report(path: Path, *, status: str = "authorized", check: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "status": status,
                "checks": {"one": check, "two": check},
                "nextStage": {"threeGridValidation": "authorized"},
            }
        ),
        encoding="utf-8",
    )


def test_affine_grid_invariance_requires_authorized_coarse_gate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "coarse.json"
    _report(path)

    assert _authorized_upstream(path)["status"] == "authorized"


@pytest.mark.parametrize(
    ("status", "check"),
    (("blocked", True), ("authorized", False)),
)
def test_affine_grid_invariance_rejects_failed_coarse_gate(
    tmp_path: Path,
    status: str,
    check: bool,
) -> None:
    path = tmp_path / "coarse.json"
    _report(path, status=status, check=check)

    with pytest.raises(ValueError):
        _authorized_upstream(path)


def test_level_summary_retains_face_and_field_regression_metrics() -> None:
    report = {
        "status": "authorized",
        "checks": {"all": True},
        "observation": {
            "velocityRelativeL2Error": 1e-15,
            "pressureRelativeL2Error": 2e-14,
            "mass": {"relativeImbalance": 3e-16},
            "finalLinearResidual": 4e-15,
            "diagnostics": {
                "faceCompatibility": {
                    "maxAbsoluteCorrectionMismatch": 5e-16,
                    "maxAbsoluteNormalPressureGradientError": 6e-15,
                    "maxAbsoluteBoundaryPressureError": 7e-16,
                },
                "pressurePreSolve": {
                    "initializedPressureResidual": {"max": 8e-15}
                },
            },
        },
    }

    summary = _level_summary("fine", 48, report)

    assert summary["cellsPerAxis"] == 48
    assert summary["allChecksPassed"] is True
    assert summary["pressureEquationExactStateResidualMax"] == 8e-15
