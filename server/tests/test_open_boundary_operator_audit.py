from __future__ import annotations

from pathlib import Path

from server.flowlab.open_boundary_operator_audit import audit


def test_operator_audit_clears_exact_pressure_gradient_and_specific_source(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "artifacts").mkdir(parents=True)
    report = {"definition": {"parameters": {"pressure_gradient_m2_s2_per_m": 0.001}}, "levels": [{"level": "coarse", "boundary_traction_relative_imbalance": 1e-8}]}
    (root / "artifacts/mms-stage-report.json").write_text(__import__("json").dumps(report), encoding="utf-8")
    (root / "coarse/case/constant").mkdir(parents=True)
    (root / "coarse/case/constant/fvModels").write_text("volumeMode specific; explicit (-0.001 0 0);", encoding="utf-8")
    field = "internalField nonuniform List<vector>\n2\n(\n(-0.001 0 0)\n(-0.001 0 0)\n)\n"
    path = root / "coarse/case/0/grad(p)"
    path.parent.mkdir(parents=True)
    path.write_text(field, encoding="utf-8")
    result = audit(root)
    assert result["sourceVolumeAudit"]["passed"] is True
    assert result["pressureGradientAudit"]["passed"] is True
    assert result["conclusion"]["remainingSuspect"].startswith("coupled SIMPLE")
