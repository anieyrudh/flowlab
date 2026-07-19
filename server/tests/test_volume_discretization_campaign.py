import pytest

from server.flowlab.volume_discretization_campaign import EXPECTED_DP, SCHEMA, _assert_exact_iteration_limit, _select, candidates


def test_v6_candidates_are_materially_distinct_and_bounded() -> None:
    prism, core = candidates(0.00012)

    assert prism.identifier == "prismatic-direct-core-v6"
    assert core.identifier == "frontal-dense-core-v6"
    assert prism.layer_count > core.layer_count
    assert prism.first_layer_m == core.first_layer_m
    assert prism.growth_ratio < core.growth_ratio
    assert prism.algorithm_3d == 4
    assert core.algorithm_3d == 4
    assert prism.volume_strategy_version == "v6"
    assert core.volume_strategy_version == "v6"
    assert core.core_interface_chords is not None
    assert prism.core_interface_chords is None
    assert prism.transition_thickness_m is None
    assert core.transition_thickness_m > 0
    assert SCHEMA == "flowlab.volume-discretization-campaign.v6"
    assert EXPECTED_DP == pytest.approx(0.0020371832715762603)


def test_selector_ignores_mesh_rejected_candidate_without_exact_gate() -> None:
    rejected = {"candidate": {"identifier": "mesh-rejected"}, "exactInitGate": None}
    accepted = {
        "candidate": {"identifier": "accepted"},
        "exactInitGate": {"accepted": True},
        "meshGate": {"maxNonOrthogonality": 10.0, "maxSkewness": 0.2, "cells": 100},
    }

    assert _select([rejected, accepted]) is accepted
    assert _select([rejected]) is None


def test_exact_iteration_limit_is_checked_before_solver_launch(tmp_path) -> None:
    control = tmp_path / "controlDict"
    control.write_text("endTime 100;\n")
    _assert_exact_iteration_limit(control, 100)
    control.write_text("endTime 2000;\n")
    with pytest.raises(RuntimeError, match="does not equal"):
        _assert_exact_iteration_limit(control, 100)
