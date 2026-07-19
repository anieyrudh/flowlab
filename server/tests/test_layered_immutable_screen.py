from pathlib import Path

from server.flowlab.cad_cylinder_surface_master import write_cylinder_surface_master
from server.flowlab.layered_immutable_screen import (
    DEFAULT_CHORD_MULTIPLIERS,
    DEFAULT_LAYER_COUNTS,
    bounded_candidates,
    parse_interface_chord_schedule,
    run_screen,
    select_candidate,
    _write_minimal_case,
)


def test_bounded_candidates_preserve_declared_v1_matrix() -> None:
    candidates = bounded_candidates(wall_chord_m=0.0002, core_size_m=0.003, growth_ratio=1.2)

    assert len(candidates) == len(DEFAULT_LAYER_COUNTS) * len(DEFAULT_CHORD_MULTIPLIERS)
    assert [(candidate.layer_count, candidate.chord_multiplier) for candidate in candidates] == [
        (layers, multiplier) for layers in DEFAULT_LAYER_COUNTS for multiplier in DEFAULT_CHORD_MULTIPLIERS
    ]
    assert candidates[0].first_layer_m == 0.0001
    assert all(candidate.core_size_m == 0.003 for candidate in candidates)


def test_select_candidate_uses_declared_quality_order() -> None:
    reports = [
        {
            "accepted": True,
            "candidate": {"id": "less-skew"},
            "checkMesh": {"metrics": {"maxNonOrthogonality": 70.0, "maxSkewness": 1.5}, "counts": {"cells": 100}},
        },
        {
            "accepted": True,
            "candidate": {"id": "best-nonorth"},
            "checkMesh": {"metrics": {"maxNonOrthogonality": 60.0, "maxSkewness": 9.0}, "counts": {"cells": 10_000}},
        },
        {
            "accepted": False,
            "candidate": {"id": "rejected"},
            "checkMesh": {"metrics": {"maxNonOrthogonality": 1.0, "maxSkewness": 1.0}, "counts": {"cells": 1}},
        },
    ]

    assert select_candidate(reports) == reports[1]


def test_select_candidate_returns_none_without_zero_failure_gate() -> None:
    assert select_candidate([{"accepted": False}]) is None


def test_screen_rejects_wrong_master_hash_before_starting_candidates(tmp_path: Path) -> None:
    master = tmp_path / "master.msh"
    write_cylinder_surface_master(
        master,
        length_m=0.05,
        radius_m=0.005,
        circumferential_chords=8,
        axial_cells=3,
    )

    report = run_screen(
        root=tmp_path / "run",
        master=master,
        expected_surface_sha256="0" * 64,
        wall_chord_m=0.0002,
        core_size_m=0.003,
        growth_ratio=1.2,
        interface_chord_schedule=((0.003, 16), (0.002, 24), (0.0015, 32)),
        transition_thickness_m=0.0005,
        runtime={"image": "test", "imageId": "test"},
    )

    assert report["disposition"]["accepted"] is False
    assert report["candidates"] == []
    assert "does not match master" in report["master"]["validationError"]
    assert (tmp_path / "run" / "artifacts" / "screen-report.json").is_file()
    assert (tmp_path / "run" / "artifacts" / "master-provenance.json").is_file()
    assert (tmp_path / "run" / "README.md").is_file()
    assert not (tmp_path / "run" / "candidates").exists()
    assert report["schema"] == "flowlab.layered-immutable-screen.v4"
    assert report["volumeStrategy"] == {
        "id": "coarsened-inner-interface",
        "version": "v2",
        "interfaceChordSchedule": [
            {"coreSizeM": 0.003, "interfaceChords": 16},
            {"coreSizeM": 0.002, "interfaceChords": 24},
            {"coreSizeM": 0.0015, "interfaceChords": 32},
        ],
        "transitionThicknessM": 0.0005,
    }


def test_interface_chord_schedule_requires_every_declared_core_size() -> None:
    assert parse_interface_chord_schedule("0.003:64,0.002:96,0.0015:128") == (
        (0.003, 64), (0.002, 96), (0.0015, 128)
    )
    try:
        parse_interface_chord_schedule("0.003:64,0.002:96")
    except ValueError as error:
        assert "all and only" in str(error)
    else:
        raise AssertionError("incomplete interface chord schedule was accepted")


def test_minimal_case_has_checkmesh_control_dict(tmp_path: Path) -> None:
    foam = tmp_path / "foam"
    _write_minimal_case(foam)

    control_dict = (foam / "system" / "controlDict").read_text(encoding="utf-8")
    assert "application     checkMesh;" in control_dict
    assert "object      controlDict;" in control_dict
