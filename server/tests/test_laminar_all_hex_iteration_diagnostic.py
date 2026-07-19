from __future__ import annotations

from server.flowlab.laminar_all_hex_iteration_diagnostic import residual_history


def test_residual_history_finds_sustained_gate_crossing(tmp_path) -> None:
    log = tmp_path / "foamRun.log"
    log.write_text(
        """Time = 1s
Solving for Ux, Initial residual = 2e-6, Final residual = 1e-12
Solving for p, Initial residual = 2e-8, Final residual = 1e-12
Time = 2s
Solving for Ux, Initial residual = 9e-7, Final residual = 1e-12
Solving for p, Initial residual = 9e-9, Final residual = 1e-12
Time = 3s
Solving for Ux, Initial residual = 8e-7, Final residual = 1e-12
Solving for p, Initial residual = 8e-9, Final residual = 1e-12
""",
        encoding="utf-8",
    )

    history = residual_history(log)

    assert history["firstSustainedAxialPassIteration"] == 2
    assert history["firstSustainedPressurePassIteration"] == 2
    assert history["final"]["iteration"] == 3
