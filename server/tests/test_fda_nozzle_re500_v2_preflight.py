from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.flowlab.fda_nozzle_re500 import PRIMARY_PROFILE_STATIONS_M
from server.flowlab.fda_nozzle_re500_v2_observation import piv_probe_plan
from server.flowlab.fda_nozzle_re500_v2_preflight import (
    compact_preflight,
    matrix_cases,
    preflight_block_mesh,
    preflight_contract,
    prepare_preflight_case,
)


def _declared_cells(mesh: str) -> int:
    return sum(
        int(a) * int(b) * int(c)
        for a, b, c in re.findall(
            r"hex \([^)]*\) \((\d+) (\d+) (\d+)\) simpleGrading", mesh
        )
    )


@pytest.mark.parametrize(
    ("outlet_m", "enhanced", "expected"),
    (
        (0.120, False, 53_808),
        (0.120, True, 163_488),
        (0.720, False, 106_608),
        (0.720, True, 254_688),
    ),
)
def test_preflight_mesh_declares_expected_all_hex_cell_budget(
    outlet_m: float, enhanced: bool, expected: int
) -> None:
    mesh = preflight_block_mesh(outlet_m, enhanced)
    assert _declared_cells(mesh) == expected
    assert "hex (" in mesh
    assert "tet" not in mesh
    assert "prism" not in mesh
    assert "wedge" not in mesh


def test_matrix_and_contract_are_complete_and_fail_closed() -> None:
    cases = matrix_cases()
    assert len(cases) == 8
    assert len({case["label"] for case in cases}) == 8
    assert {
        (case["outlet"], case["scheme"], case["resolution"])
        for case in cases
    } == {
        (outlet, scheme, resolution)
        for outlet in ("short", "extended")
        for scheme in ("bounded", "second-order")
        for resolution in ("base", "enhanced")
    }
    contract = preflight_contract()
    assert contract["design"]["type"] == "full factorial 2^3"
    assert contract["authorization"]["desktopPromotion"] is False
    assert contract["promotionAuthorized"] is False


def test_formal_scheme_case_changes_only_declared_spatial_discretization(
    tmp_path: Path,
) -> None:
    factors = next(
        case
        for case in matrix_cases()
        if case["label"] == "short__second-order__base"
    )
    prepare_preflight_case(tmp_path, factors)
    schemes = (tmp_path / "system" / "fvSchemes").read_text(encoding="utf-8")
    assert "gradSchemes { default Gauss linear; }" in schemes
    assert "laplacianSchemes { default Gauss linear corrected; }" in schemes
    assert "snGradSchemes { default corrected; }" in schemes
    assert "div(phi,U) bounded Gauss linearUpwind grad(U);" in schemes
    pressure = (tmp_path / "0" / "p").read_text(encoding="utf-8")
    assert "inlet { type fixedFluxPressure;" in pressure
    assert "outlet { type fixedValue; value uniform 0; }" in pressure


def test_piv_plan_uses_normalized_area_quadrature_and_full_window_support() -> None:
    summary = {
        "axialVelocityProfiles": {
            f"{station:.6f}": {
                "axial": (
                    [
                        {"coordinateM": 0.0, "mean": 1.0, "n": 3, "u95": 0.1},
                        {
                            "coordinateM": 0.006,
                            "mean": 0.0,
                            "n": 3,
                            "u95": 0.1,
                        },
                    ]
                    if station == -0.088
                    else []
                ),
                "radial": [],
            }
            for station in PRIMARY_PROFILE_STATIONS_M
        },
        "centrelineAxialVelocity": [
            {"coordinateM": 0.02, "mean": 1.0, "n": 3, "u95": 0.1}
        ],
    }
    plan = piv_probe_plan(summary)
    valid = next(
        row
        for row in plan["records"]
        if row["qoi"] == "axialVelocityProfile" and row["radialCoordinateM"] == 0.0
    )
    edge = next(
        row
        for row in plan["records"]
        if row["qoi"] == "axialVelocityProfile" and row["radialCoordinateM"] == 0.006
    )
    assert valid["supportValid"]
    assert not edge["supportValid"]
    assert len(valid["kernels"]) == 3
    assert all(
        sum(sample["weight"] for sample in kernel["samples"])
        == pytest.approx(1.0)
        for kernel in valid["kernels"]
    )


def test_compaction_refuses_to_delete_preassessment_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="refusing to compact"):
        compact_preflight(tmp_path)
