from __future__ import annotations

from server.flowlab.open_boundary_affine_probe import _case_files
from server.flowlab.open_boundary_mms_redesign import AffineCrossflowMms


def test_affine_probe_case_matches_preflight_boundary_and_source_contract() -> None:
    spec = AffineCrossflowMms()
    files = _case_files(12, spec)

    assert "inlet { type fixedValue; value nonuniform List<vector>" in files["0/U"]
    assert "outlet { type pressureInletOutletVelocity; value nonuniform List<vector>" in files["0/U"]
    assert "yMin { type fixedValue; value uniform (1 0.10000000000000001 0); }" in files["0/U"]
    assert "yMax { type fixedGradient; gradient uniform (0.10000000000000001 0 0); }" in files["0/U"]
    assert "inlet { type fixedGradient; gradient uniform 0.001; }" in files["0/p"]
    assert "outlet { type fixedValue; value uniform 0; }" in files["0/p"]
    assert "explicit (0.0090000000000000011 0 0)" in files["constant/fvModels"]
    assert "endTime 1" in files["system/controlDict"]


def test_affine_probe_inlet_profile_is_exact_and_positive() -> None:
    spec = AffineCrossflowMms()
    field = _case_files(12, spec)["0/U"]

    assert "(1.0041666666666667 0.10000000000000001 0)" in field
    assert "(1.0958333333333334 0.10000000000000001 0)" in field
