from __future__ import annotations

from server.flowlab.fda_nozzle_re500_successor import (
    audit_experimental_reference,
    audit_grid_family,
    matrix_rank,
    parse_check_mesh,
)


def _check_mesh(*, cells: int, inlet_faces: int, volume: float) -> str:
    return f"""
Mesh stats
    cells:            {cells}
Overall number of cells of each type:
    hexahedra:     {cells}
    inlet               {inlet_faces}       17       ok (non-closed singly connected)
    Min volume = 1e-12. Max volume = 1e-9.  Total volume = {volume}.  Cell volumes OK.
    Mesh non-orthogonality Max: 12.5 average: 2.0
Mesh OK.
"""


def test_matrix_rank_handles_centred_three_trace_limit() -> None:
    centred = [
        [-1.0, -1.0, 0.0, -2.0],
        [0.0, 2.0, 0.0, 1.0],
        [1.0, -1.0, 0.0, 1.0],
    ]
    assert matrix_rank(centred) == 2


def test_parse_check_mesh_extracts_geometry_invariants() -> None:
    parsed = parse_check_mesh(
        _check_mesh(cells=20_436, inlet_faces=12, volume=1.961951e-5)
    )
    assert parsed == {
        "cells": 20_436,
        "hexahedra": 20_436,
        "inletFaces": 12,
        "totalVolumeM3": 1.961951e-5,
        "maximumNonOrthogonalityDegrees": 12.5,
        "strictAllHex": True,
        "checkMeshPassed": True,
    }


def test_reference_audit_blocks_rank_limited_pressure() -> None:
    experiment = {
        "source": {"sha256": "abc", "commit": "def"},
        "pressureEligibility": {"codes": [243, 468, 763]},
        "files": [
            {"dataset-code": 243, "plots": [{"deleted": False}]},
            {"dataset-code": 297, "plots": [{"deleted": True}]},
            {"dataset-code": 468, "plots": [{"deleted": False}]},
            {"dataset-code": 763, "plots": [{"deleted": False}]},
            {"dataset-code": 999, "plots": [{"deleted": False}]},
        ],
    }
    rows = [
        {
            "experiment": {
                "trialValuesPa": [float(index), float(index + 1), float(index + 3)]
            }
        }
        for index in range(16)
    ]
    pressure = {"wall": {"adjacent": {"rows": rows}}}
    audit = audit_experimental_reference(experiment, pressure)
    assert audit["pressureTraceCount"] == 3
    assert audit["adjacentPressureDimension"] == 16
    assert audit["maximumPossibleSampleCovarianceRank"] == 2
    assert audit["centredTraceMatrixRank"] <= 2
    assert audit["deletedPressureDatasetCodes"] == [297]
    assert audit["publishedExcludedPressureDatasetCodes"] == [999]
    assert audit["pressureReferencePromotionReady"] is False


def test_grid_audit_quantifies_geometry_discretization_and_blocks_successor() -> None:
    meshes = {
        "coarse": parse_check_mesh(
            _check_mesh(cells=20_436, inlet_faces=12, volume=1.961951e-5)
        ),
        "medium": parse_check_mesh(
            _check_mesh(cells=163_488, inlet_faces=48, volume=2.123600e-5)
        ),
        "fine": parse_check_mesh(
            _check_mesh(cells=1_307_904, inlet_faces=192, volume=2.165204e-5)
        ),
    }
    audit = audit_grid_family(meshes)
    assert audit["cellCountRatios"] == [8.0, 8.0]
    assert audit["exactThreeDimensionalCellRefinement"] is True
    assert audit["domainVolumeRelativeRange"] > 0.09
    assert audit["relativeDomainVolumeErrorToNominal"]["coarse"] < -0.09
    assert audit["relativeDomainVolumeErrorToNominal"]["fine"] > -0.01
    assert audit["geometryErrorMagnitudeDecreasesWithRefinement"] is True
    assert audit["geometryAndSolutionDiscretizationSeparated"] is False
    assert audit["successorGridFamilyPreflightComplete"] is False
