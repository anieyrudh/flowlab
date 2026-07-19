import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.gmsh_immutable_surface_probe import msh2_surface_fingerprint
from server.flowlab.layered_immutable_three_core import (
    CORE_SIZES_M,
    ScreenPreflightError,
    load_accepted_screen,
    run_three_core,
)


def _accepted_screen(master: Path) -> Dict[str, Any]:
    fingerprint = msh2_surface_fingerprint(master)
    selected = {
        "id": "layers4-chord1.0",
        "layer_count": 4,
        "chord_multiplier": 1.0,
        "first_layer_m": 0.0001,
        "growth_ratio": 1.2,
        "core_size_m": 0.003,
    }
    return {
        "schema": "flowlab.layered-immutable-screen.v4",
        "volumeStrategy": {
            "id": "coarsened-inner-interface",
            "version": "v2",
            "interfaceChordSchedule": [
                {"coreSizeM": 0.003, "interfaceChords": 64},
                {"coreSizeM": 0.002, "interfaceChords": 96},
                {"coreSizeM": 0.0015, "interfaceChords": 128},
            ],
            "transitionThicknessM": 0.0005,
        },
        "master": {"declaredSurfaceSha256": fingerprint["surfaceSha256"], "observed": fingerprint},
        "disposition": {"accepted": True, "selectedCandidate": selected},
        "candidates": [],
    }


def _write_screen(path: Path, contents: Dict[str, Any]) -> None:
    path.write_text(json.dumps(contents), encoding="utf-8")


def test_core_debug_measurement_uses_actual_tetrahedral_volume(tmp_path: Path) -> None:
    from server.flowlab.layered_immutable_three_core import _read_core_debug_evidence

    debug = tmp_path / "core-debug.msh"
    debug.write_text(
        "\n".join(
            [
                "$MeshFormat", "2.2 0 8", "$EndMeshFormat", "$Nodes", "4",
                "1 0 0 0", "2 1 0 0", "3 0 1 0", "4 0 0 1", "$EndNodes",
                "$Elements", "1", "1 4 2 1 1 1 2 3 4", "$EndElements", "",
            ]
        ),
        encoding="utf-8",
    )

    evidence = _read_core_debug_evidence(debug)

    assert evidence["valid"] is True
    assert evidence["coreTetrahedra"] == 1
    assert evidence["totalCoreVolumeM3"] == pytest.approx(1.0 / 6.0)
    assert evidence["effectiveCoreSpacingM"] == pytest.approx(2.0 ** (1.0 / 6.0))


def test_load_accepted_screen_rejects_unaccepted_report_before_execution(tmp_path: Path) -> None:
    master = tmp_path / "master.msh"
    write_cylinder_surface_master(master, length_m=0.05, radius_m=0.005, circumferential_chords=8, axial_cells=3)
    report = _accepted_screen(master)
    report["disposition"]["accepted"] = False
    screen = tmp_path / "screen.json"
    _write_screen(screen, report)

    with pytest.raises(ScreenPreflightError, match="did not accept"):
        load_accepted_screen(screen, master)


def _core_evidence(tetrahedra: int, spacing: float) -> Dict[str, Any]:
    return {
        "valid": True,
        "coreTetrahedra": tetrahedra,
        "totalCoreVolumeM3": 1.0,
        "meanCoreTetraVolumeM3": 1.0 / tetrahedra,
        "effectiveCoreSpacingM": spacing,
        "method": "test",
    }


def test_three_core_rebuilds_all_levels_for_versioned_volume_strategy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master = tmp_path / "master.msh"
    write_cylinder_surface_master(master, length_m=0.05, radius_m=0.005, circumferential_chords=8, axial_cells=3)
    screen_data = _accepted_screen(master)
    selected_gate = {
        "accepted": True,
        "candidate": dict(screen_data["disposition"]["selectedCandidate"]),
        "surface": {"before": msh2_surface_fingerprint(master), "after": msh2_surface_fingerprint(master)},
        "checkMesh": {"failedChecks": 0, "counts": {"cells": 100}, "metrics": {"minVolume": 1e-9}, "issueCounts": {
            "smallDeterminantCells": 0, "lowInterpolationWeightFaces": 0, "lowVolumeRatioFaces": 0, "concaveCells": 0,
        }},
        "artifacts": {},
    }
    for name in ("volumeMsh", "configuration", "gmshLog", "gmshToFoamLog", "checkMeshLog"):
        artifact = tmp_path / (name + ".txt")
        artifact.write_text("evidence\n", encoding="utf-8")
        selected_gate["artifacts"][name] = {"present": True, "path": str(artifact)}
    screen_data["candidates"] = [selected_gate]
    screen = tmp_path / "screen.json"
    _write_screen(screen, screen_data)

    invoked: List[tuple[float, int]] = []

    def fake_run_level(root: Path, master_path: Path, expected_hash: str, selected: Any, strategy: Any, core_size_m: float) -> Dict[str, Any]:
        invoked.append((core_size_m, strategy.chords_for(core_size_m)))
        index = list(CORE_SIZES_M).index(core_size_m)
        return {
            "accepted": True,
            "checkMesh": {"counts": {"cells": (100, 200, 300)[index]}},
            "coreMeshEvidence": _core_evidence((1000, 2000, 3000)[index], (0.003, 0.002, 0.0015)[index]),
            "volumeStrategyBinding": {
                "expected": {
                    "identifier": "coarsened-inner-interface",
                    "version": "v2",
                    "interface_chord_schedule": ((0.003, 64), (0.002, 96), (0.0015, 128)),
                    "transition_thickness_m": 0.0005,
                },
                "observed": {
                    "id": "coarsened-inner-interface",
                    "version": "v2",
                    "interfaceChords": 64,
                    "transitionThicknessM": 0.0005,
                },
                "matched": True,
            },
            "level": {"coreSizeM": core_size_m, "reusedFromAcceptedScreen": False},
        }

    monkeypatch.setattr("server.flowlab.layered_immutable_three_core._run_level", fake_run_level)
    result = run_three_core(tmp_path / "run", master, screen, {"image": "test", "imageId": "test"})

    assert invoked == [(0.003, 64), (0.002, 96), (0.0015, 128)]
    assert result["disposition"]["accepted"] is True
    assert result["disposition"]["allThreeStrictGatesPassed"] is True
    assert [level["level"]["coreSizeM"] for level in result["levels"]] == list(CORE_SIZES_M)
    assert result["levels"][0]["level"]["reusedFromAcceptedScreen"] is False
    assert (tmp_path / "run" / "artifacts" / "three-core-report.json").is_file()


def test_three_core_rejects_identical_cell_counts_even_when_checkmesh_passes(tmp_path: Path) -> None:
    from server.flowlab.layered_immutable_three_core import _report

    levels = [
        {"accepted": True, "checkMesh": {"counts": {"cells": 42}}, "coreMeshEvidence": _core_evidence(10, 0.003), "volumeStrategyBinding": {"matched": True}},
        {"accepted": True, "checkMesh": {"counts": {"cells": 42}}, "coreMeshEvidence": _core_evidence(20, 0.002), "volumeStrategyBinding": {"matched": True}},
        {"accepted": True, "checkMesh": {"counts": {"cells": 42}}, "coreMeshEvidence": _core_evidence(30, 0.0015), "volumeStrategyBinding": {"matched": True}},
    ]
    report = _report(tmp_path, tmp_path / "screen.json", "digest", tmp_path / "master.msh", "hash", {}, None, None, {}, levels)
    assert report["disposition"]["accepted"] is False
    assert report["disposition"]["allThreeStrictGatesPassed"] is True
    assert report["refinement"]["accepted"] is False


def test_three_core_rejects_static_core_even_if_total_cells_increase(tmp_path: Path) -> None:
    from server.flowlab.layered_immutable_three_core import _report

    levels = [
        {"accepted": True, "checkMesh": {"counts": {"cells": 100}}, "coreMeshEvidence": _core_evidence(10, 0.003), "volumeStrategyBinding": {"matched": True}},
        {"accepted": True, "checkMesh": {"counts": {"cells": 200}}, "coreMeshEvidence": _core_evidence(10, 0.003), "volumeStrategyBinding": {"matched": True}},
        {"accepted": True, "checkMesh": {"counts": {"cells": 300}}, "coreMeshEvidence": _core_evidence(10, 0.003), "volumeStrategyBinding": {"matched": True}},
    ]
    report = _report(tmp_path, tmp_path / "screen.json", "digest", tmp_path / "master.msh", "hash", {}, None, None, {}, levels)

    assert report["disposition"]["accepted"] is False
    assert report["refinement"]["coreTetrahedraCoarseToFine"] == [10, 10, 10]
    assert report["refinement"]["effectiveCoreSpacingMCoarseToFine"] == [0.003, 0.003, 0.003]
    assert any("core tetrahedra" in reason for reason in report["refinement"]["rejectionReasons"])
    assert any("effective core spacing" in reason for reason in report["refinement"]["rejectionReasons"])


def test_three_core_rejects_legacy_screen_without_versioned_volume_strategy(tmp_path: Path) -> None:
    master = tmp_path / "master.msh"
    write_cylinder_surface_master(master, length_m=0.05, radius_m=0.005, circumferential_chords=8, axial_cells=3)
    screen_data = _accepted_screen(master)
    del screen_data["volumeStrategy"]
    screen = tmp_path / "screen.json"
    _write_screen(screen, screen_data)

    result = run_three_core(tmp_path / "run", master, screen, {"image": "test", "imageId": "test"})

    assert result["disposition"]["accepted"] is False
    assert result["levels"] == []
    assert "volume strategy" in result["preflightError"]


def test_three_core_preflight_does_not_run_levels_when_screen_hash_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    master = tmp_path / "master.msh"
    write_cylinder_surface_master(master, length_m=0.05, radius_m=0.005, circumferential_chords=8, axial_cells=3)
    screen = tmp_path / "screen.json"
    report = _accepted_screen(master)
    report["master"]["declaredSurfaceSha256"] = "0" * 64
    _write_screen(screen, report)
    monkeypatch.setattr(
        "server.flowlab.layered_immutable_three_core._run_level",
        lambda *args, **kwargs: pytest.fail("a rejected screen must not launch a level"),
    )

    result = run_three_core(tmp_path / "run", master, screen, {"image": "test", "imageId": "test"})

    assert result["disposition"]["accepted"] is False
    assert result["levels"] == []
    assert "does not match" in result["preflightError"]
