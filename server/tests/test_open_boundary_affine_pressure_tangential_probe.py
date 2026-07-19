from __future__ import annotations

import json

from server.flowlab.open_boundary_affine_flux_pressure_probe import (
    FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    _case_files,
    _definition_manifest,
)
from server.flowlab.open_boundary_mms_redesign import AffineCrossflowMms
from server.flowlab.open_boundary_affine_pressure_tangential_probe import (
    LINEAR_SOLVER_TOLERANCE,
)


def test_pressure_tangential_inlet_is_well_posed_and_exact() -> None:
    files = _case_files(
        12,
        AffineCrossflowMms(),
        inlet_contract=FIXED_PRESSURE_TANGENTIAL_VELOCITY,
        u_solver_type="PBiCGStab",
        linear_solver_tolerance=LINEAR_SOLVER_TOLERANCE,
    )

    assert "inlet { type fixedValue; value uniform 0.001; }" in files["0/p"]
    assert "inlet { type pressureInletOutletVelocity; phi phi;" in files["0/U"]
    assert "tangentialVelocity uniform (0 0.10000000000000001 0);" in files["0/U"]
    assert "inlet { type fixedValue;" not in files["0/U"]
    assert "solver PBiCGStab; preconditioner DILU;" in files["system/fvSolution"]
    assert "tolerance 1e-14;" in files["system/fvSolution"]
    assert "yMin { type fixedFluxPressure;" in files["0/p"]
    assert "yMax { type fixedFluxPressure;" in files["0/p"]
    implementation = json.loads(files["boundary-implementation.json"])
    assert implementation["outlet"]["p"] == "fixedValue 0; downstream pressure trace"

    definition = _definition_manifest(
        AffineCrossflowMms(),
        FIXED_PRESSURE_TANGENTIAL_VELOCITY,
    )
    assert definition["boundaryTreatment"]["inlet"]["p"] == (
        "fixedValue G; upstream pressure trace"
    )


def test_rejected_flux_pressure_contract_remains_the_default() -> None:
    files = _case_files(12, AffineCrossflowMms())

    assert "inlet { type fixedValue;" in files["0/U"]
    assert "inlet { type fixedFluxPressure;" in files["0/p"]
    assert "solver smoothSolver; smoother symGaussSeidel;" in files[
        "system/fvSolution"
    ]
