from __future__ import annotations

import math

from server.flowlab.open_boundary_non_affine_mms import (
    MINIMUM_OBSERVED_ORDER,
    NonAffineMms,
    _case_files,
    _convergence,
)


def test_non_affine_mms_is_divergence_free_and_boundary_compatible() -> None:
    spec = NonAffineMms()

    assert spec.velocity(0.0, 0.3, 0.7) == spec.velocity(1.0, 0.3, 0.7)
    assert math.isclose(spec.pressure(0.0, 0.3, 0.7), spec.pressure_drop_m2_s2)
    assert math.isclose(spec.pressure(1.0, 0.3, 0.7), 0.0, abs_tol=1e-15)
    assert spec.velocity(0.4, 0.0, 0.7) == (spec.base_velocity_m_s, 0.0, 0.0)
    assert spec.velocity(0.4, 0.7, 1.0) == (spec.base_velocity_m_s, 0.0, 0.0)


def test_non_affine_source_contains_pressure_gradient_and_viscous_closure() -> None:
    spec = NonAffineMms()
    x, y, z = 0.37, 0.41, 0.63
    source = spec.momentum_source(x, y, z)
    gradient = spec.pressure_gradient(x, y, z)
    expected_diffusion = (
        2
        * spec.viscosity_m2_s
        * spec.velocity_amplitude_m_s
        * math.pi**2
        * math.sin(math.pi * y)
        * math.sin(math.pi * z)
    )

    assert math.isclose(source[0] - gradient[0], expected_diffusion)
    assert source[1:] == gradient[1:]


def test_non_affine_case_uses_compatible_pressure_velocity_contract() -> None:
    files = _case_files(12, NonAffineMms())

    assert files["0/U"].count("type pressureInletOutletVelocity") == 2
    assert "inlet { type fixedValue;" in files["0/p"]
    assert "outlet { type fixedValue;" in files["0/p"]
    assert files["0/p"].count("type fixedFluxPressure") == 4
    assert "type coded;" in files["constant/fvModels"]
    assert "sin(pi*c[i].x())" in files["constant/fvModels"]


def test_second_order_error_sequence_produces_meaningful_order_and_gci() -> None:
    result = _convergence([8.0e-3, 2.0e-3, 5.0e-4])

    assert result["observedOrder"]["coarseToMedium"] == 2.0
    assert result["observedOrder"]["mediumToFine"] == 2.0
    assert result["observedOrder"]["mediumToFine"] >= MINIMUM_OBSERVED_ORDER
    assert result["gciRelativeToAnalyticFieldNorm"]["fine"] < 0.01
