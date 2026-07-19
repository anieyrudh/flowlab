from __future__ import annotations

from pathlib import Path

from server.flowlab.open_boundary_campaign import MmsDefinition
from server.flowlab.open_boundary_inlet_pressure_audit import (
    _inlet_pressure_equivalence,
    _inlet_pressure_spec,
    _normalize_inlet_pressure,
)
from server.flowlab.open_boundary_mms_runner import _case_files


def _materialize(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_inlet_pressure_normalization_isolates_only_patch_type() -> None:
    common = {"outlet_velocity_type": "pressureInletOutletVelocity", "force_write_interval": 1}
    control = _case_files(12, MmsDefinition(), **common)["0/p"]  # type: ignore[arg-type]
    changed = _case_files(
        12,
        MmsDefinition(),
        inlet_pressure_type="fixedFluxPressure",
        **common,  # type: ignore[arg-type]
    )["0/p"]

    assert _inlet_pressure_spec(control) == {
        "type": "fixedValue",
        "datumKind": "value",
        "datum": 0.001,
    }
    assert _inlet_pressure_spec(changed) == {
        "type": "fixedFluxPressure",
        "datumKind": "value",
        "datum": 0.001,
    }
    assert _normalize_inlet_pressure(control) == _normalize_inlet_pressure(changed)


def test_inlet_pressure_normalization_supports_analytic_fixed_gradient() -> None:
    common = {"outlet_velocity_type": "pressureInletOutletVelocity", "force_write_interval": 1}
    control = _case_files(12, MmsDefinition(), **common)["0/p"]  # type: ignore[arg-type]
    changed = _case_files(
        12,
        MmsDefinition(),
        inlet_pressure_type="fixedGradient",
        **common,  # type: ignore[arg-type]
    )["0/p"]

    assert _inlet_pressure_spec(changed) == {
        "type": "fixedGradient",
        "datumKind": "gradient",
        "datum": 0.001,
    }
    assert _normalize_inlet_pressure(control) == _normalize_inlet_pressure(changed)


def test_equivalence_proves_inlet_pressure_type_is_only_solve_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    changed = tmp_path / "changed"
    common = {"outlet_velocity_type": "pressureInletOutletVelocity", "force_write_interval": 1}
    _materialize(control, _case_files(12, MmsDefinition(), **common))  # type: ignore[arg-type]
    _materialize(
        changed,
        _case_files(
            12,
            MmsDefinition(),
            inlet_pressure_type="fixedFluxPressure",
            **common,  # type: ignore[arg-type]
        ),
    )

    result = _inlet_pressure_equivalence(control, changed)

    assert result["allOtherSolveFilesIdentical"] is True
    assert result["pIdenticalAfterInletTypeNormalization"] is True
    assert result["boundaryMetadataIdenticalAfterInletTypeNormalization"] is True
    assert result["onlyInletPressureTypeChanged"] is True


def test_equivalence_rejects_an_additional_scheme_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    changed = tmp_path / "changed"
    _materialize(control, _case_files(12, MmsDefinition()))
    files = _case_files(12, MmsDefinition(), inlet_pressure_type="fixedFluxPressure")
    files["system/fvSchemes"] = files["system/fvSchemes"].replace(
        "Gauss linear", "Gauss upwind", 1
    )
    _materialize(changed, files)

    result = _inlet_pressure_equivalence(control, changed)

    assert result["allOtherSolveFilesIdentical"] is False
    assert result["onlyInletPressureTypeChanged"] is False


def test_equivalence_proves_fixed_gradient_is_only_solve_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    changed = tmp_path / "changed"
    common = {"outlet_velocity_type": "pressureInletOutletVelocity", "force_write_interval": 1}
    _materialize(control, _case_files(12, MmsDefinition(), **common))  # type: ignore[arg-type]
    _materialize(
        changed,
        _case_files(
            12,
            MmsDefinition(),
            inlet_pressure_type="fixedGradient",
            **common,  # type: ignore[arg-type]
        ),
    )

    result = _inlet_pressure_equivalence(
        control,
        changed,
        expected_type="fixedGradient",
    )

    assert result["allOtherSolveFilesIdentical"] is True
    assert result["pIdenticalAfterInletTypeNormalization"] is True
    assert result["boundaryMetadataIdenticalAfterInletTypeNormalization"] is True
    assert result["onlyInletPressureBoundaryChanged"] is True
    assert result["inletPressure"]["after"]["datumKind"] == "gradient"
