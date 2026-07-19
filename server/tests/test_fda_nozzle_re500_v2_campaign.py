from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from server.flowlab.fda_nozzle_re500 import PRIMARY_PROFILE_STATIONS_M
from server.flowlab.fda_nozzle_re500_v2_campaign import (
    EXPECTED_CELLS,
    FROZEN_CONTRACT_SHA256,
    LEVEL_CELLS,
    RECOVERY_CONTRACT_SHA256,
    _patch_recovery_control_dict,
    _recovery_completed,
    _snapshot_manifest,
    _outlet_traction_audit,
    _pressure_validation,
    _with_operator_uncertainty,
    prepare_v2_case,
    v2_block_mesh,
)
from server.flowlab.fda_nozzle_re500_v2_observation import piv_probe_plan
from server.flowlab.fda_nozzle_re500_v2_preflight import preflight_block_mesh


def _declared_cells(mesh: str) -> int:
    return sum(
        int(a) * int(b) * int(c)
        for a, b, c in re.findall(
            r"hex \([^)]*\) \((\d+) (\d+) (\d+)\) simpleGrading", mesh
        )
    )


@pytest.mark.parametrize("level", ("coarse", "medium", "fine"))
def test_v2_mesh_family_matches_frozen_cell_budgets(level: str) -> None:
    mesh = v2_block_mesh(level)
    assert _declared_cells(mesh) == EXPECTED_CELLS[level]
    assert "hex (" in mesh
    assert all(token not in mesh for token in ("tet", "prism", "wedge", "pyramid"))


def test_medium_mesh_exactly_reproduces_selected_preflight_topology() -> None:
    assert v2_block_mesh("medium") == preflight_block_mesh(0.120, True)


def test_levels_are_exact_linear_one_two_four_family() -> None:
    for name, scale in (("coarse", 1), ("medium", 2), ("fine", 4)):
        assert LEVEL_CELLS[name] == {
            "coreTangential": 2 * scale,
            "annularTangential": 2 * scale,
            "upstreamAnnularRadial": 1 * scale,
            "inletAxial": 58 * scale,
            "contractionAxial": 45 * scale,
            "throatAxial": 80 * scale,
            "nearDownstreamAxial": 240 * scale,
            "downstreamOuterRadial": 8 * scale,
            "farExtensionAxial": 0,
        }


def test_prepared_case_preserves_boundary_and_second_order_contract(tmp_path: Path) -> None:
    prepare_v2_case(tmp_path, "medium", "medium", 1.0)
    pressure = (tmp_path / "0" / "p").read_text(encoding="utf-8")
    velocity = (tmp_path / "0" / "U").read_text(encoding="utf-8")
    schemes = (tmp_path / "system" / "fvSchemes").read_text(encoding="utf-8")
    definition = json.loads((tmp_path / "case-definition.json").read_text())
    assert "inlet { type fixedFluxPressure" in pressure
    assert "outlet { type fixedValue; value uniform 0; }" in pressure
    assert "inlet { type fixedValue" in velocity
    assert "gradSchemes { default Gauss linear; }" in schemes
    assert "laplacianSchemes { default Gauss linear corrected; }" in schemes
    assert "snGradSchemes { default corrected; }" in schemes
    assert definition["expectedCells"] == EXPECTED_CELLS["medium"]
    assert definition["frozenContractSha256"] == FROZEN_CONTRACT_SHA256


def test_frozen_contract_hash_is_current() -> None:
    contract = Path(
        "benchmarks/cases/fda-nozzle/campaigns/2026-07-17-re500-v2-preflight/"
        "v2-full-campaign-contract.json"
    )
    assert hashlib.sha256(contract.read_bytes()).hexdigest() == FROZEN_CONTRACT_SHA256


def test_recovery_contract_hash_is_current() -> None:
    contract = Path(
        "docs/validation/fda-nozzle-re500/V2_FINE_RECOVERY_CONTRACT.json"
    )
    assert hashlib.sha256(contract.read_bytes()).hexdigest() == RECOVERY_CONTRACT_SHA256


def test_recovery_snapshot_manifest_is_deterministic(tmp_path: Path) -> None:
    for directory in ("0", "750", "constant", "system"):
        (tmp_path / directory).mkdir()
    for relative, content in {
        "0/U": "initial velocity\n",
        "750/U": "checkpoint velocity\n",
        "constant/physicalProperties": "nu 1e-6;\n",
        "system/controlDict": "startFrom startTime;\nendTime 800;\nrunTimeModifiable false;\n",
        "case-definition.json": "{}\n",
        "initialization.json": "{}\n",
    }.items():
        (tmp_path / relative).write_text(content, encoding="utf-8")
    first = _snapshot_manifest(tmp_path)
    second = _snapshot_manifest(tmp_path)
    assert first == second
    assert [row["path"] for row in first["files"]] == [
        "0/U",
        "750/U",
        "constant/physicalProperties",
        "system/controlDict",
        "case-definition.json",
        "initialization.json",
    ]


def test_recovery_control_mutation_is_exact_and_one_shot(tmp_path: Path) -> None:
    control = tmp_path / "controlDict"
    control.write_text(
        "startFrom startTime;\nstartTime 0;\nendTime 800;\n"
        "runTimeModifiable false;\n",
        encoding="utf-8",
    )
    mutation = _patch_recovery_control_dict(control)
    assert mutation["before"] == "startFrom startTime;"
    assert mutation["after"] == "startFrom latestTime;"
    assert control.read_text(encoding="utf-8").count("startFrom latestTime;") == 1
    with pytest.raises(ValueError, match="exact frozen startFrom"):
        _patch_recovery_control_dict(control)


@pytest.mark.parametrize(
    ("exit_code", "latest", "log", "expected"),
    (
        (0, "800", "Time = 800s\n\nEnd\n", True),
        (1, "800", "Time = 800s\n\nEnd\n", False),
        (0, "750", "Time = 800s\n\nEnd\n", False),
        (0, "800", "Time = 800s\n", False),
        (0, "800", "End\n", False),
    ),
)
def test_recovery_completion_is_fail_closed(
    exit_code: int, latest: str, log: str, expected: bool
) -> None:
    assert _recovery_completed(exit_code, latest, log) is expected


def test_piv_plan_includes_radial_profiles_as_nonpromotional_observations() -> None:
    summary = {
        "axialVelocityProfiles": {
            f"{station:.6f}": {
                "axial": [{"coordinateM": 0.0, "mean": 1.0, "n": 3, "u95": 0.1}],
                "radial": [{"coordinateM": 0.0, "mean": 0.0, "n": 3, "u95": 0.1}],
            }
            for station in PRIMARY_PROFILE_STATIONS_M
        },
        "centrelineAxialVelocity": [],
    }
    plan = piv_probe_plan(summary)
    radial = [row for row in plan["records"] if row["qoi"] == "radialVelocityProfile"]
    assert len(radial) == len(PRIMARY_PROFILE_STATIONS_M)
    assert all(row["supportValid"] and len(row["kernels"]) == 3 for row in radial)


def _diagnostics() -> dict[str, dict]:
    simulations = {
        "coarse": 1.0,
        "medium": 1.5,
        "fine": 1.625,
        "input-minus-5pct": 1.45,
        "input-plus-5pct": 1.55,
    }
    result = {}
    for label, value in simulations.items():
        result[label] = {"wall": {}, "centreline": {}}
        for kind in ("wall", "centreline"):
            for group in ("adjacent", "named"):
                result[label][kind][group] = {
                    "rows": [
                        {
                            "name": "overall-pressure-drop",
                            "leftM": -0.1,
                            "rightM": 0.03,
                            "experiment": {"mean": 1.6, "n": 3, "u95": 0.1},
                            "simulationPa": value,
                            "simulationPreviousPa": value - 0.001,
                        }
                    ]
                }
    return result


def test_pressure_validation_keeps_all_uncertainty_components() -> None:
    result = _pressure_validation(_diagnostics())
    row = result["wall"]["adjacent"]["rows"][0]
    assert row["grid"]["qualified"]
    assert row["uncertainty"]["input"] == pytest.approx(0.05)
    assert row["uncertainty"]["iterative"] == pytest.approx(0.001)
    assert row["uncertainty"]["grid"] is not None
    assert row["passesVv20"]


def test_operator_uncertainty_is_added_in_quadrature() -> None:
    validation = {
        "qualified": True,
        "passesVv20": False,
        "comparisonError": 0.2,
        "validationUncertainty": 0.1,
        "uncertainty": {},
    }
    updated = _with_operator_uncertainty(validation, 0.2)
    assert updated["validationUncertainty"] == pytest.approx((0.1**2 + 0.2**2) ** 0.5)
    assert updated["uncertainty"]["observationOperator"] == 0.2
    assert updated["passesVv20"]


def test_outlet_audit_retains_face_normal_gradient_and_traction(tmp_path: Path) -> None:
    header = (
        "patch,area,sn_grad_normal_velocity,traction_x_pa,traction_y_pa,traction_z_pa,"
        "viscous_force_x_n,viscous_force_y_n,viscous_force_z_n\n"
    )
    row = "outlet,2,0.25,3,4,0,0.1,0.2,0.3\n"
    for label in ("coarse", "medium", "fine"):
        result = tmp_path / "results" / label
        result.mkdir(parents=True)
        for time in ("750", "800"):
            (result / f"face-integration-{time}.csv").write_text(
                header + row, encoding="utf-8"
            )
    audit = _outlet_traction_audit(tmp_path)
    sample = audit["cases"]["fine"]["800"]
    assert sample["maxAbsNormalVelocityGradientPerS"] == pytest.approx(0.25)
    assert sample["maxViscousTractionPa"] == pytest.approx(5.0)
    assert sample["directViscousForceN"] == pytest.approx([0.1, 0.2, 0.3])
    assert sample["analyticZeroViscousTractionPa"] == 0.0
