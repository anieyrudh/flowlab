from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab.execution import materialize_case_files
from server.flowlab.y_junction_campaign import (
    _level_rows,
    _sequence,
    _write_terminal_state,
    build_case,
    evaluate_case,
    load_contract,
    materialize_campaign,
)

ROOT = Path(__file__).resolve().parents[2]


def _write_surface(case_dir: Path, name: str, value: float) -> None:
    path = case_dir / "postProcessing" / name / "0" / "surfaceFieldValue.dat"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        f"{iteration} {value:.17g}" for iteration in range(2300, 2501, 25)
    )
    path.write_text(f"# Time value\n{rows}\n", encoding="utf-8")


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
    assert manifest["fixedMasterHierarchy"]["passed"] is True
    assert manifest["fixedMasterHierarchy"]["masterGeometrySha256"]
    fine = next(record for record in manifest["cases"] if record["label"] == "fine")
    assert fine["cellCount"] == 340992
    assert fine["parentProvenance"]["minimumChildrenPerMasterCell"] == 64
    assert fine["parentProvenance"]["maximumChildrenPerMasterCell"] == 64
    assert fine["resultBinding"]["unownedCellRanges"][0]["artifactId"] == (
        "generated:y-junction:junction-core:v1"
    )


def test_v5_contract_freezes_fixed_master_subdivision_and_probe_sampling() -> None:
    contract = load_contract()
    case = build_case(_level_rows(contract)[-1], contract)
    profile = json.loads(case.files["constant/flowlab_y_junction_profile.json"])

    assert contract["contractId"] == "bounded-symmetric-y-junction-v5"
    assert [row["cellSizeM"] for row in _level_rows(contract)] == [
        0.00075,
        0.000375,
        0.0001875,
    ]
    assert [row["refinementFactor"] for row in _level_rows(contract)] == [1, 2, 4]
    assert profile["mesh"]["refinement"]["factor"] == 4
    assert profile["mesh"]["refinement"]["regionOwnershipReclassifiedFromGeometry"] is False
    assert profile["mesh"]["geometryInvariants"]["masterCellCount"] == 5328
    assert profile["probeSampling"] == contract["probeSampling"]
    assert "fixedLocations  true;" in case.files["system/functions"]
    assert "interpolationScheme cellPoint;" in case.files["system/functions"]


def test_v5_preserves_every_v4_scientific_threshold() -> None:
    v4 = json.loads(
        (
            ROOT
            / "docs"
            / "validation"
            / "y-junction"
            / "QUALIFICATION_CONTRACT_V4.json"
        ).read_text(encoding="utf-8")
    )
    v5 = load_contract()

    for gate in ("equalPressurePerLevel", "sequence", "negativeControl"):
        assert v5["gates"][gate] == v4["gates"][gate]
    for key in ("exitCode", "normalTerminationRequired", "finitePressureAndVelocityRequired"):
        assert v5["gates"]["solverPerCase"][key] == v4["gates"]["solverPerCase"][key]
    assert v5["gates"]["meshPerCase"] == v4["gates"]["meshPerCase"]
    assert v5["gates"]["ownership"] == v4["gates"]["ownership"]


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
    assert equal["solver"]["iterativeStability"]["commonPressureSampleCount"] == 9
    assert equal["solver"]["iterativeStability"]["primaryQoiRelativeRange"] == pytest.approx(0.0)
    assert control["allPerCaseGatesPassed"] is True
    assert control["physics"]["gates"]["lowerPressureOutletHasGreaterOutflow"] is True


def test_iterative_stability_fails_closed_on_a_drifting_final_window(
    tmp_path: Path,
) -> None:
    case_dir = _synthetic_completed_case(tmp_path)
    inlet_path = (
        case_dir
        / "postProcessing"
        / "inletPressure"
        / "0"
        / "surfaceFieldValue.dat"
    )
    inlet_path.write_text(
        "# Time value\n"
        + "\n".join(
            f"{iteration} {0.001 + index * 0.00001:.17g}"
            for index, iteration in enumerate(range(2300, 2501, 25))
        )
        + "\n",
        encoding="utf-8",
    )

    evaluation = evaluate_case(
        case_dir,
        label="fine",
        asymmetric=False,
        solver_exit_code=0,
    )

    assert evaluation["solver"]["iterativeStability"]["passed"] is False
    assert evaluation["solver"]["gates"]["iterativeStability"] is False
    assert evaluation["allPerCaseGatesPassed"] is False


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
        "coarse": {"qoi": {"primaryPressureDropPa": 1.20}},
        "medium": {"qoi": {"primaryPressureDropPa": 1.04}},
        "fine": {"qoi": {"primaryPressureDropPa": 1.0}},
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


def test_terminal_state_updates_live_journal_and_assessment_together(
    tmp_path: Path,
) -> None:
    state = {
        "status": "qualification-gate-failed-retained",
        "allQualificationGatesPassed": False,
        "validated": False,
        "promotionAuthorized": False,
    }

    _write_terminal_state(tmp_path, state)

    assert json.loads((tmp_path / "campaign-run-state.json").read_text()) == state
    assert json.loads((tmp_path / "campaign-assessment.json").read_text()) == state
