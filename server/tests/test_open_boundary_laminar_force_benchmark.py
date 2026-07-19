from __future__ import annotations

import math

from server.flowlab.open_boundary_laminar_force_benchmark import (
    PlanePoiseuille,
    _case_files,
    _mesh_shape,
)


def test_plane_poiseuille_solution_and_integral_forces_are_analytic() -> None:
    spec = PlanePoiseuille()

    assert spec.velocity(0.4, 0.0, 0.3) == (0.0, 0.0, 0.0)
    assert spec.velocity(0.4, 1.0, 0.3) == (0.0, 0.0, 0.0)
    assert math.isclose(spec.pressure(0.0, 0.2, 0.3), spec.pressure_drop_m2_s2)
    assert spec.reynolds_number < 100.0
    assert spec.manifest()["derived"]["analyticOpenPressureForce"][0] == -spec.pressure_drop_m2_s2
    assert spec.manifest()["derived"]["analyticWallViscousForce"][0] == spec.pressure_drop_m2_s2


def test_analytic_viscous_traction_is_face_resolved() -> None:
    spec = PlanePoiseuille()
    wall = spec.analytic_traction((0.5, 0.0, 0.5), (0.0, -1.0, 0.0))
    inlet = spec.analytic_traction((0.0, 0.25, 0.5), (-1.0, 0.0, 0.0))

    assert math.isclose(wall[0], spec.pressure_drop_m2_s2 / 2.0)
    assert inlet[0] == 0.0
    assert inlet[1] != 0.0


def test_physical_case_retains_exact_pressure_and_flux_velocity_contract() -> None:
    files = _case_files(12, PlanePoiseuille())

    assert files["0/U"].count("type pressureInletOutletVelocity") == 2
    assert "inlet { type fixedValue;" in files["0/p"]
    assert "outlet { type fixedValue;" in files["0/p"]
    assert "forcesOpen" in files["system/controlDict"]
    assert "forcesWalls" in files["system/controlDict"]


def test_physical_case_accepts_diagnostic_iteration_budget() -> None:
    files = _case_files(12, PlanePoiseuille(), iterations=1250)

    assert "endTime 1250" in files["system/controlDict"]
    assert "writeInterval 1250" in files["system/controlDict"]


def test_rectangular_case_preserves_pressure_gradient_and_force_balance() -> None:
    spec = PlanePoiseuille(
        pressure_drop_m2_s2=-0.08,
        length_m=4.0,
        height_m=1.0,
        depth_m=1.0,
    )

    assert math.isclose(spec.pressure_gradient_m_s2, -0.02)
    assert math.isclose(spec.signed_reynolds_number, -16.666666666666668)
    assert spec.reynolds_number > 0.0
    assert spec.analytic_open_pressure_force == (0.08, 0.0, 0.0)
    assert spec.analytic_wall_viscous_force == (-0.08, 0.0, 0.0)


def test_physical_matrix_mesh_shape_controls_axial_cell_aspect_ratio() -> None:
    spec = PlanePoiseuille(length_m=4.0)

    assert _mesh_shape(12, spec, 1.0) == (48, 12, 12)
    assert _mesh_shape(12, spec, 2.0) == (24, 12, 12)
    files = _case_files(12, spec, 2.0)
    assert "(24 12 12)" in files["system/blockMeshDict"]
    assert "(4 1 1)" in files["system/blockMeshDict"]
