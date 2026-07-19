from __future__ import annotations

from server.flowlab.laminar_all_hex_mpi_worker import _decompose_par_dict
from server.flowlab.laminar_all_hex_reproducibility import qoi_equivalence


def _observation(scale: float = 1.0) -> dict:
    return {
        "directFaceIntegration": {
            "open": {"pressure": [-0.02 * scale, 0.0, 0.0]},
            "walls": {"viscous": [0.0199 * scale, 0.0, 0.0]},
        },
        "openFoamForces": {
            "open": {"pressure": [-0.02 * scale, 0.0, 0.0]},
            "walls": {"viscous": [0.0199 * scale, 0.0, 0.0]},
        },
        "velocityRelativeL2Error": 3.0e-4 * scale,
        "pressureRelativeL2Error": 4.0e-6 * scale,
    }


def test_mpi_dictionary_is_restricted_to_campaign_rank_counts() -> None:
    assert "numberOfSubdomains 2;" in _decompose_par_dict(2)
    assert "method scotch;" in _decompose_par_dict(4)


def test_qoi_equivalence_uses_existing_one_part_per_million_limit() -> None:
    assert qoi_equivalence(_observation(), _observation(1.0 + 5.0e-7))["passed"] is True
    assert qoi_equivalence(_observation(), _observation(1.0 + 2.0e-6))["passed"] is False
