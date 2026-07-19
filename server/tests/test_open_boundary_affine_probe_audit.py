from __future__ import annotations

from server.flowlab.open_boundary_affine_probe_audit import _solver_residuals


def test_solver_residual_parser_retains_all_components() -> None:
    text = """
smoothSolver:  Solving for Ux, Initial residual = 3.6e-15, Final residual = 3.6e-15, No Iterations 0
smoothSolver:  Solving for Uy, Initial residual = 0.0024, Final residual = 0.0020, No Iterations 1000
smoothSolver:  Solving for Uz, Initial residual = 0.98, Final residual = 8e-11, No Iterations 22
GAMG:  Solving for p, Initial residual = 0.0063, Final residual = 4e-11, No Iterations 15
"""

    result = _solver_residuals(text)

    assert result["Ux"] == {"initial": 3.6e-15, "final": 3.6e-15, "iterations": 0}
    assert result["Uy"]["iterations"] == 1000
    assert result["p"]["initial"] == 0.0063
