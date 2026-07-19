from __future__ import annotations

from pathlib import Path

from server.flowlab.open_boundary_campaign import MmsDefinition
from server.flowlab.open_boundary_mms_runner import _case_files
from server.flowlab.open_boundary_stabilization_audit import (
    _normalize_relaxation,
    _relaxation_value,
    _stabilization_equivalence,
)


def _materialize(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_relaxation_normalization_isolates_only_u_equation_value() -> None:
    control = _case_files(12, MmsDefinition())["system/fvSolution"]
    stabilized = _case_files(12, MmsDefinition(), u_equation_relaxation=0.9)["system/fvSolution"]

    assert _relaxation_value(control) == 1.0
    assert _relaxation_value(stabilized) == 0.9
    assert _normalize_relaxation(control) == _normalize_relaxation(stabilized)


def test_equivalence_proves_u_relaxation_is_the_only_solve_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    stabilized = tmp_path / "stabilized"
    common = {
        "outlet_velocity_type": "pressureInletOutletVelocity",
        "force_write_interval": 1,
    }
    _materialize(control, _case_files(12, MmsDefinition(), **common))  # type: ignore[arg-type]
    _materialize(
        stabilized,
        _case_files(12, MmsDefinition(), u_equation_relaxation=0.9, **common),  # type: ignore[arg-type]
    )

    result = _stabilization_equivalence(control, stabilized, expected_after=0.9)

    assert result["allOtherSolveFilesIdentical"] is True
    assert result["fvSolutionIdenticalAfterRelaxationNormalization"] is True
    assert result["onlyUEquationRelaxationChanged"] is True


def test_equivalence_rejects_an_additional_scheme_change(tmp_path: Path) -> None:
    control = tmp_path / "control"
    stabilized = tmp_path / "stabilized"
    _materialize(control, _case_files(12, MmsDefinition()))
    files = _case_files(12, MmsDefinition(), u_equation_relaxation=0.9)
    files["system/fvSchemes"] = files["system/fvSchemes"].replace("Gauss linear", "Gauss upwind", 1)
    _materialize(stabilized, files)

    result = _stabilization_equivalence(control, stabilized, expected_after=0.9)

    assert result["allOtherSolveFilesIdentical"] is False
    assert result["onlyUEquationRelaxationChanged"] is False
