from __future__ import annotations

from server.flowlab.open_boundary_factorial_matrix import (
    FACTOR_ORDER,
    _slug,
    calculate_effects,
    matrix_configurations,
    select_candidate,
)


def _cell(
    config: dict[str, object],
    *,
    passes: bool,
    healthy: bool,
    velocity: float = 0.01,
    pressure: float = 0.02,
    traction: float = 5.0e-7,
) -> dict[str, object]:
    coded = {
        "outletVelocity": -1 if config["outletVelocity"] == "pressureInletOutletVelocity" else 1,
        "simpleConsistent": -1 if config["simpleConsistent"] is True else 1,
        "uSolverType": -1 if config["uSolverType"] == "smoothSolver" else 1,
        "uEquationRelaxation": -1 if config["uEquationRelaxation"] == 1.0 else 1,
    }
    change_count = sum(value == 1 for value in coded.values())
    return {
        "cellId": _slug(config),
        "configuration": config,
        "codedFactors": coded,
        "changeCountVsCurrent": change_count,
        "passesCoarseGate": passes,
        "history": {"firstUxHealthy": healthy},
        "observation": {"velocity_l2_error": velocity, "pressure_l2_error": pressure},
        "responses": {
            "boundaryTractionRelativeImbalance": traction,
            "velocityL2Error": velocity,
            "pressureL2Error": pressure,
            "log10FirstUxFinalResidual": -10.0 if healthy else 0.0,
            "firstUxLinearIterations": 10 if healthy else 1000,
        },
    }


def test_matrix_is_complete_balanced_two_to_the_fourth_design() -> None:
    configurations = matrix_configurations()

    assert len(configurations) == 16
    assert len({_slug(config) for config in configurations}) == 16
    for factor in FACTOR_ORDER:
        values = [config[factor] for config in configurations]
        assert values.count(values[0]) == 8


def test_main_effect_recovers_alternative_minus_current_contrast() -> None:
    cells = []
    for config in matrix_configurations():
        traction = 3.0 if config["simpleConsistent"] is False else 1.0
        cells.append(_cell(config, passes=False, healthy=False, traction=traction))

    main, interactions = calculate_effects(cells)

    assert main["effects"]["simpleConsistent"]["responses"]["boundaryTractionRelativeImbalance"]["effect"] == 2.0
    assert main["effects"]["outletVelocity"]["responses"]["boundaryTractionRelativeImbalance"]["effect"] == 0.0
    assert interactions["effects"]["outletVelocity x simpleConsistent"]["responses"]["boundaryTractionRelativeImbalance"]["effect"] == 0.0


def test_selection_prefers_healthy_minimal_change_over_lower_error_multi_change() -> None:
    configs = matrix_configurations()
    one_change = next(
        config
        for config in configs
        if config["uSolverType"] == "PBiCGStab"
        and config["outletVelocity"] == "pressureInletOutletVelocity"
        and config["simpleConsistent"] is True
        and config["uEquationRelaxation"] == 1.0
    )
    multi_change = next(
        config
        for config in configs
        if config["uSolverType"] == "PBiCGStab"
        and config["outletVelocity"] == "zeroGradient"
        and config["simpleConsistent"] is False
        and config["uEquationRelaxation"] == 0.9
    )
    cells = [
        _cell(one_change, passes=True, healthy=True, velocity=0.02, pressure=0.03),
        _cell(multi_change, passes=True, healthy=True, velocity=0.001, pressure=0.001),
    ]

    result = select_candidate(cells, {"velocity_l2_error": 1.0, "pressure_l2_error": 1.0})

    assert result["selectedCellId"] == _slug(one_change)
    assert result["selectedChangeCount"] == 1


def test_selection_blocks_when_no_cell_passes_all_hard_gates() -> None:
    config = matrix_configurations()[0]

    result = select_candidate(
        [_cell(config, passes=False, healthy=True)],
        {"velocity_l2_error": 1.0, "pressure_l2_error": 1.0},
    )

    assert result["status"] == "no-coarse-passing-candidate"
    assert result["threeGridForcedMms"] == "blocked"
