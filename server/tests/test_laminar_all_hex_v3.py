from __future__ import annotations

import math

from server.flowlab.laminar_all_hex_v3_confirmation import is_v2_sensitive_cell
from server.flowlab.laminar_all_hex_v3_contract import (
    CAMPAIGN_ID,
    CHECK_INTERVAL_ITERATIONS,
    FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT,
    HARD_CAP_ITERATIONS,
    SUSTAINED_WINDOW_ITERATIONS,
    build_manifest,
    termination_contract,
    validate_manifest,
)
from server.flowlab.laminar_all_hex_v3_observer import (
    first_sustained_joint_pass_iteration,
    staged_case_files,
    sustained_window,
)
from server.flowlab.laminar_all_hex_v3_reproducibility import (
    analytic_scaled_field_difference,
    primary_qoi_equivalence,
)
from server.flowlab.open_boundary_laminar_force_benchmark import PlanePoiseuille


def test_v3_manifest_freezes_78_cells_without_changing_scientific_limits() -> None:
    manifest = build_manifest()
    checks = validate_manifest(manifest)

    assert manifest["campaignId"] == CAMPAIGN_ID
    assert manifest["mobile"] == {"inScope": False, "changes": "none"}
    assert manifest["primaryScientificCellCount"] == 78
    assert len([cell for cell in manifest["cells"] if cell["lane"] == "physical-envelope"]) == 72
    assert all(checks.values()), checks
    assert manifest["campaignProfile"]["scientificThresholdChanges"] == []


def test_staged_control_uses_latest_time_checkpoints_and_hard_cap() -> None:
    files = staged_case_files(12, PlanePoiseuille(), 1.0)
    control = files["system/controlDict"]

    assert "startFrom latestTime;" in control
    assert f"endTime {CHECK_INTERVAL_ITERATIONS};" in control
    assert "purgeWrite 1;" in control
    assert f"writeInterval {CHECK_INTERVAL_ITERATIONS};" in control
    assert termination_contract()["hardCapIterations"] == HARD_CAP_ITERATIONS


def test_sustained_window_requires_25_consecutive_joint_passes() -> None:
    history = [
        {
            "iteration": iteration,
            "Ux": 9.0e-7 if iteration >= 300 else 2.0e-6,
            "p": 9.0e-9 if iteration >= 300 else 2.0e-8,
        }
        for iteration in range(1, 325)
    ]

    assert SUSTAINED_WINDOW_ITERATIONS == 25
    assert sustained_window(history)["passed"] is True
    assert first_sustained_joint_pass_iteration(history) == 324

    history[-1]["p"] = 1.1e-8
    assert sustained_window(history)["passed"] is False


def test_analytic_scaled_field_difference_is_absolute_scale_not_error_ratio() -> None:
    serial = [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
    parallel = [(1.0 + 1.0e-12, 0.0, 0.0), (2.0, 0.0, 0.0)]
    analytic = [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]

    difference = analytic_scaled_field_difference(serial, parallel, analytic)

    assert math.isfinite(difference)
    assert difference < FIELD_EQUIVALENCE_ANALYTIC_NORM_LIMIT


def test_primary_qoi_contract_excludes_derived_error_norms() -> None:
    observation = {
        "directFaceIntegration": {
            "open": {"pressure": [-0.02, 0.0, 0.0]},
            "walls": {"viscous": [0.02, 0.0, 0.0]},
        },
        "openFoamForces": {
            "open": {"pressure": [-0.02, 0.0, 0.0]},
            "walls": {"viscous": [0.02, 0.0, 0.0]},
        },
    }
    result = primary_qoi_equivalence(observation, observation)

    assert result["passed"] is True
    assert set(result["comparisons"]) == {
        "directOpenPressureX",
        "directWallViscousX",
        "openFoamOpenPressureX",
        "openFoamWallViscousX",
    }


def test_confirmation_selector_is_exactly_the_v2_sensitive_factor_slice() -> None:
    manifest = build_manifest()
    selected = [cell for cell in manifest["cells"] if is_v2_sensitive_cell(cell)]

    assert len(selected) == 6
    assert all(cell["parameters"]["reynoldsNumberHeightBased"] == 66.7 for cell in selected)
    assert all(cell["parameters"]["lengthToHeightRatio"] == 1.0 for cell in selected)
