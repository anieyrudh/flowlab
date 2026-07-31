from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab import adapters
from server.flowlab.curved_elbow import preview_mesh
from server.flowlab.curved_elbow_campaign import (
    CASE_ID,
    CONTRACT_SCHEMA,
    CurvedElbowCampaignError,
    _cell_geometry,
    _component_ranges,
    _field_physics,
    _geometry_metrics,
    _sequence_assessment,
    _spec,
    build_level_case,
    load_contract,
)
from server.flowlab.execution import materialize_case_files


def _offline_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapters, "_command_exists", lambda _command: False)
    monkeypatch.setattr(adapters, "_docker_available", lambda: False)


def test_contract_freezes_one_bounded_unpromoted_elbow_sequence() -> None:
    contract = load_contract()

    assert contract["schema"] == CONTRACT_SCHEMA
    assert contract["contractId"] == CASE_ID
    assert contract["status"] == (
        "prospective-frozen-before-retained-scientific-execution"
    )
    assert contract["promotionAuthorized"] is False
    assert contract["physicalCase"] | {
        "centrelineRadiusOverDiameter": 3.0,
        "inletLegOverDiameter": 10.0,
        "outletLegOverDiameter": 10.0,
        "reynoldsNumber": 100.0,
    } == contract["physicalCase"]
    assert [row["id"] for row in contract["levels"]] == [
        "coarse",
        "medium",
        "fine",
    ]
    assert [row["expectedCellCount"] for row in contract["levels"]] == [
        2496,
        19968,
        159744,
    ]
    assert contract["gates"]["geometryPerLevel"][
        "maximumRelativeDimensionError"
    ] == 0.01
    assert contract["gates"]["physicsPerLevel"][
        "maximumRelativeMassFlowImbalance"
    ] == 0.001
    assert contract["gates"]["physicsPerLevel"][
        "maximumSymmetryPlaneError"
    ] == 0.02
    assert contract["gates"]["sequence"]["maximumFineGridGciPercent"] == 5.0
    assert "arbitrary CAD" in contract["claim"]["excluded"]
    assert "axisymmetric validation inheritance" in contract["claim"]["excluded"]
    assert "SU2 without a supported three-dimensional result identity" in contract[
        "claim"
    ]["excluded"]


def test_level_case_is_deterministic_and_uses_explicit_component_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_adapters(monkeypatch)
    contract = load_contract()
    level = contract["levels"][0]

    first = build_level_case(level, contract)
    second = build_level_case(level, contract)

    assert first.files == second.files
    assert first.resultComponentMap == second.resultComponentMap
    assert first.resultComponentMap is not None
    binding = first.resultComponentMap.artifactBindings[0].model_dump()
    assert binding["scope"] == "cell-ranges"
    assert binding["sourceCellCount"] == 2496
    assert [row["componentId"] for row in binding["cellRanges"]] == [
        "inlet-leg",
        "elbow",
        "outlet-leg",
    ]
    assert "curvedElbowXYZProbes" in first.files["system/functions"]
    probes = json.loads(
        first.files[
            "constant/flowlab_curved_elbow_probe_provenance.json"
        ]
    )
    assert probes["sourceCellIdentity"] == (
        "result-component-map-v2-cell-ranges"
    )
    assert probes["geometryInferredOwnershipAllowed"] is False
    assert probes["probeCount"] == 7
    assert all(
        row["geometryInferredOwnership"] is False
        and row["sourceCellRange"]["cellCount"] > 0
        for row in probes["probes"]
    )


def test_runtime_geometry_operator_recovers_all_frozen_dimensions() -> None:
    contract = load_contract()
    level = contract["levels"][0]
    spec = _spec(contract, level)
    vtk = preview_mesh(spec)

    metrics = _geometry_metrics(
        vtk,
        contract,
        f"Total volume = {vtk['volumeQuality']['totalCellVolumeM3']:.17g}\n",
    )

    assert metrics["maximumRelativeDimensionError"] < 1.0e-10
    assert metrics["measured"]["diameterFromZSpanM"] == pytest.approx(0.01)
    assert metrics["measured"]["centrelineRadiusM"] == pytest.approx(0.03)
    assert metrics["measured"]["bendAngleDegrees"] == pytest.approx(90.0)
    assert metrics["measured"]["inletLegLengthM"] == pytest.approx(0.1)
    assert metrics["measured"]["outletLegLengthM"] == pytest.approx(0.1)
    assert metrics["relativeAnalyticVolumeError"] > 0.0


def test_field_operator_requires_finite_symmetric_source_bound_cells() -> None:
    contract = load_contract()
    level = contract["levels"][0]
    spec = _spec(contract, level)
    vtk = preview_mesh(spec)
    centroids, _volumes = _cell_geometry(vtk)
    owner = {
        cell_index: str(region["componentId"])
        for region in vtk["regions"]
        for cell_index in range(
            int(region["cellStart"]),
            int(region["cellStart"]) + int(region["cellCount"]),
        )
    }
    pressures = [
        0.002
        if owner[index] == "inlet-leg"
        else 0.0015
        if owner[index] == "elbow"
        else 0.001
        for index in range(len(centroids))
    ]
    vtk["cellData"] = {
        "scalars": {"p": pressures},
        "vectors": {"U": [[0.01, 0.0, 0.0] for _ in centroids]},
    }

    metrics = _field_physics(
        vtk,
        contract=contract,
        pressure_loss_pa=1.0,
        component_owner=owner,
    )

    assert metrics["finitePressureAndVelocity"] is True
    assert metrics["totalPressure"]["lossPa"] == pytest.approx(1.0)
    assert metrics["totalPressure"]["gainPa"] == 0.0
    assert metrics["symmetry"]["maximumNormalizedError"] == pytest.approx(0.0)
    assert metrics["symmetry"]["pairedCellCount"] > 0

    vtk["cellData"]["scalars"]["p"][0] = float("nan")
    assert _field_physics(
        vtk,
        contract=contract,
        pressure_loss_pa=1.0,
        component_owner=owner,
    )["finitePressureAndVelocity"] is False


def test_component_operator_rejects_overlap_and_unowned_cells(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offline_adapters(monkeypatch)
    contract = load_contract()
    level = contract["levels"][0]
    case_dir = tmp_path / "case"
    case = build_level_case(level, contract)
    materialize_case_files(case, case_dir)

    ranges, owner = _component_ranges(case_dir, 2496)
    assert [row["componentId"] for row in ranges] == [
        "inlet-leg",
        "elbow",
        "outlet-leg",
    ]
    assert sorted(owner) == list(range(2496))

    manifest_path = case_dir / adapters.CASE_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    binding = next(
        row
        for row in manifest["resultComponentMap"]["artifactBindings"]
        if row["artifactName"] == "VTK/*.vtk"
    )
    binding["cellRanges"][1]["cellStart"] = 959
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CurvedElbowCampaignError, match="overlap"):
        _component_ranges(case_dir, 2496)


def test_sequence_operator_passes_only_a_valid_bounded_three_grid_order() -> None:
    contract = load_contract()
    evaluations = {
        level["id"]: {
            "characteristicCellSizeM": (
                contract["physicalCase"]["analyticFluidVolumeM3"]
                / level["expectedCellCount"]
            )
            ** (1.0 / 3.0),
            "qoi": {"staticPressureLossPa": value},
            "provenance": {"runtimeVtkSha256": digest * 64},
        }
        for level, value, digest in zip(
            contract["levels"],
            (1.0, 1.1, 1.125),
            ("a", "b", "c"),
            strict=True,
        )
    }

    assessment = _sequence_assessment(evaluations, contract)

    assert assessment["passed"] is True
    assert assessment["gridConvergence"]["observedOrder"] == pytest.approx(2.0)
    assert assessment["gridConvergence"]["fineGridGciPercent"] < 5.0

    evaluations["fine"]["qoi"]["staticPressureLossPa"] = 1.05
    failed = _sequence_assessment(evaluations, contract)
    assert failed["passed"] is False
    assert failed["gates"]["gciMathematicallyQualified"] is False
