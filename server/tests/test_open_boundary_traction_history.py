from __future__ import annotations

import math
from pathlib import Path

import pytest

from server.flowlab.open_boundary_traction_history import (
    _force_history,
    _normalize_control_dict,
    _residual_history,
    _trend,
)


def test_force_history_retains_pressure_viscous_and_relative_balance(tmp_path: Path) -> None:
    path = tmp_path / "forces.dat"
    path.write_text(
        "# Time forces\n"
        "1 (((-1.0000000000000000e-03 0 0) (-2.0000000000000000e-09 0 0)) ((0 0 0) (0 0 0)))\n",
        encoding="utf-8",
    )

    rows = _force_history(path, source_x=-1.0e-3)

    assert rows[0]["iteration"] == 1
    assert rows[0]["pressureForceX"] == -1.0e-3
    assert rows[0]["viscousForceX"] == -2.0e-9
    assert rows[0]["relativeTractionImbalance"] == pytest.approx(2.0e-6)


def test_residual_history_collects_complete_outer_iterations(tmp_path: Path) -> None:
    path = tmp_path / "foamRun.log"
    path.write_text(
        "Time = 1s\n"
        "smoothSolver: Solving for Ux, Initial residual = 0.4, Final residual = 1e-11, No Iterations 12\n"
        "smoothSolver: Solving for Uy, Initial residual = 0.3, Final residual = 2e-11, No Iterations 11\n"
        "smoothSolver: Solving for Uz, Initial residual = 0.2, Final residual = 3e-11, No Iterations 10\n"
        "GAMG: Solving for p, Initial residual = 0.01, Final residual = 4e-11, No Iterations 5\n",
        encoding="utf-8",
    )

    rows = _residual_history(path)

    assert rows[0]["maxVelocityEquationInitialResidual"] == 0.4
    assert rows[0]["pressureEquationInitialResidual"] == 0.01
    assert rows[0]["maxLinearFinalResidual"] == 4.0e-11


def test_trend_uses_predeclared_last_twenty_sample_rule() -> None:
    rows = [
        {"iteration": iteration, "value": 2.0 * (0.98**iteration)}
        for iteration in range(1, 31)
    ]

    trend = _trend(rows, "value")

    assert trend["startIteration"] == 11
    assert trend["endIteration"] == 30
    assert trend["relativeDrop"] > 0.10
    assert trend["logSlopePerIteration"] == pytest.approx(math.log(0.98))
    assert trend["decreasingStepFraction"] == 1.0


def test_control_dict_normalization_ignores_only_force_sampling_interval() -> None:
    control = "endTime 100; functions { forces { writeControl timeStep; writeInterval 100; } }"
    diagnostic = "endTime 100; functions { forces { writeControl timeStep; writeInterval 1; } }"

    assert _normalize_control_dict(control) == _normalize_control_dict(diagnostic)
    assert _normalize_control_dict(diagnostic) != _normalize_control_dict(
        "endTime 101; functions { forces { writeControl timeStep; writeInterval 1; } }"
    )
