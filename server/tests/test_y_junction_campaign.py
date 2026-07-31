from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab.execution import materialize_case_files
from server.flowlab.y_junction_campaign import (
    _level_rows,
    _sequence,
    build_case,
    evaluate_case,
    load_contract,
    materialize_campaign,
)


def _write_surface(case_dir: Path, name: str, value: float) -> None:
    path = case_dir / "postProcessing" / name / "0" / "surfaceFieldValue.dat"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Time value\n2500 {value:.17g}\n", encoding="utf-8")


def _write_probes(case_dir: Path) -> None:
    root = case_dir / "postProcessing" / "yJunctionMirroredProbes" / "0"
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("p").write_text(
        "# Time junction upper1 lower1 upper2 lower2 upper3 lower3\n"
        "2500 0.0007 0.0005 0.0005 0.00035 0.00035 0.0002 0.0002\n",
        encoding="utf-8",
    )
    root.joinpath("U").write_text(
        "# Time vectors\n"
        "2500 (0.01 0 0) "
        "(0.012 0.006 0) (0.012 -0.006 0) "
        "(0.014 0.007 0) (0.014 -0.007 0) "
        "(0.016 0.008 0) (0.016 -0.008 0)\n",
        encoding="utf-8",
    )


def _synthetic_completed_case(tmp_path: Path, *, asymmetric: bool = False) -> Path:
    contract = load_contract()
    fine = _level_rows(contract)[-1]
    case = build_case(fine, contract, asymmetric=asymmetric)
    case_dir = tmp_path / ("asymmetric" if asymmetric else "equal")
    materialize_case_files(case, case_dir)
    preview = json.loads(case.files["mesh/flowlab_mesh.json"])
    cell_count = len(preview["cells"])
    check_mesh = (
        "Mesh stats\n"
        f"    cells:          {cell_count}\n"
        f"    hexahedra:      {cell_count}\n"
        "Number of regions: 1\n"
        "min volume = 1.25e-10. Max aspect ratio = 1\n"
        "Mesh OK.\n"
    )
    (case_dir / "log.checkMesh").write_text(check_mesh, encoding="utf-8")
    (case_dir / "log.foamRun").write_text(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\n"
        "Time = 2500\n"
        "End\n",
        encoding="utf-8",
    )
    _write_surface(case_dir, "inletFlow", -1.0)
    _write_surface(case_dir, "upperFlow", 0.4 if asymmetric else 0.5)
    _write_surface(case_dir, "lowerFlow", 0.6 if asymmetric else 0.5)
    _write_surface(case_dir, "inletPressure", 0.001)
    _write_surface(case_dir, "upperPressure", 0.0)
    _write_surface(case_dir, "lowerPressure", -0.0002 if asymmetric else 0.0)
    _write_probes(case_dir)
    return case_dir


def test_materialization_freezes_duplicate_hashes_and_unowned_junction(tmp_path: Path) -> None:
    manifest = materialize_campaign(tmp_path / "campaign")

    assert manifest["status"] == "materialized-pending-openfoam"
    assert len(manifest["cases"]) == 4
    assert all(
        record["determinism"]["duplicateGeneratedFileHashesMatch"]
        for record in manifest["cases"]
    )
    fine = next(record for record in manifest["cases"] if record["label"] == "fine")
    assert fine["resultBinding"]["unownedCellRanges"][0]["artifactId"] == (
        "generated:y-junction:junction-core:v1"
    )


def test_v2_contract_freezes_fixed_cell_point_probe_sampling() -> None:
    contract = load_contract()
    case = build_case(_level_rows(contract)[-1], contract)
    profile = json.loads(case.files["constant/flowlab_y_junction_profile.json"])

    assert contract["contractId"] == "bounded-symmetric-y-junction-v2"
    assert profile["probeSampling"] == contract["probeSampling"]
    assert "fixedLocations  true;" in case.files["system/functions"]
    assert "interpolationScheme cellPoint;" in case.files["system/functions"]


def test_synthetic_equal_pressure_and_asymmetric_control_evaluators(tmp_path: Path) -> None:
    equal = evaluate_case(
        _synthetic_completed_case(tmp_path),
        label="fine",
        asymmetric=False,
        solver_exit_code=0,
    )
    control = evaluate_case(
        _synthetic_completed_case(tmp_path, asymmetric=True),
        label="fine-asymmetric-control",
        asymmetric=True,
        solver_exit_code=0,
    )

    assert equal["allPerCaseGatesPassed"] is True
    assert equal["qoi"]["upperOutletFlowFraction"] == pytest.approx(0.5)
    assert equal["qoi"]["mirroredPressureRelativeError"] == pytest.approx(0.0)
    assert equal["qoi"]["mirroredVelocityRelativeError"] == pytest.approx(0.0)
    assert control["allPerCaseGatesPassed"] is True
    assert control["physics"]["gates"]["lowerPressureOutletHasGreaterOutflow"] is True


def test_solver_header_is_not_a_crash_but_real_floating_point_signal_is(tmp_path: Path) -> None:
    case_dir = _synthetic_completed_case(tmp_path)
    accepted = evaluate_case(
        case_dir,
        label="fine",
        asymmetric=False,
        solver_exit_code=0,
    )
    assert accepted["solver"]["normalTermination"] is True

    (case_dir / "log.foamRun").write_text(
        "Time = 2500\nEnd\nFloating point exception (core dumped)\n",
        encoding="utf-8",
    )
    rejected = evaluate_case(
        case_dir,
        label="fine",
        asymmetric=False,
        solver_exit_code=0,
    )
    assert rejected["solver"]["normalTermination"] is False


def test_three_grid_order_and_gci_fail_closed() -> None:
    contract = load_contract()
    convergent = {
        "coarse": {"qoi": {"primaryPressureDropPa": 1.09}},
        "medium": {"qoi": {"primaryPressureDropPa": 1.04}},
        "fine": {"qoi": {"primaryPressureDropPa": 1.0177777777777777}},
    }
    accepted = _sequence(convergent, contract)
    assert accepted["observedOrder"] == pytest.approx(2.0)
    assert accepted["passed"] is True

    non_monotone = {
        "coarse": {"qoi": {"primaryPressureDropPa": 1.0}},
        "medium": {"qoi": {"primaryPressureDropPa": 1.1}},
        "fine": {"qoi": {"primaryPressureDropPa": 1.05}},
    }
    rejected = _sequence(non_monotone, contract)
    assert rejected["qualified"] is False
    assert rejected["passed"] is False
