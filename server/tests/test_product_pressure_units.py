"""The application must not show kinematic pressure labelled as pascals."""
import json
from pathlib import Path

from server.flowlab.execution import collect_patch_metrics, product_patch_metrics

DENSITY = 870.0
KINEMATIC_DELTA_P = 0.023063852


def _case(tmp_path: Path, dimensions: str, *, density: float | None = DENSITY) -> Path:
    case = tmp_path / "case"
    (case / "0").mkdir(parents=True)
    (case / "0" / "p").write_text(f"dimensions      {dimensions};\ninternalField   uniform 0;\n")
    if density is not None:
        (case / "flowlab_project.json").write_text(json.dumps({"fluid": {"density": density}}))
    postpro = case / "postProcessing" / "patchAverage_inlet" / "0"
    postpro.mkdir(parents=True)
    (postpro / "surfaceFieldValue.dat").write_text(
        "# Time areaAverage(p)\n0.5 " + repr(KINEMATIC_DELTA_P) + "\n"
    )
    outlet = case / "postProcessing" / "patchAverage_outlet" / "0"
    outlet.mkdir(parents=True)
    (outlet / "surfaceFieldValue.dat").write_text("# Time areaAverage(p)\n0.5 0.0\n")
    return case


def _drop(metrics):
    drops = metrics.get("pressureDrops") or []
    return drops[0] if drops else None


def test_incompressible_pressure_is_converted_to_pascals(tmp_path):
    case = _case(tmp_path, "[0 2 -2 0 0 0 0]")

    raw = _drop(collect_patch_metrics(case))
    assert raw is not None, "fixture must produce a pressure drop"
    # The shared collector stays kinematic: the frozen campaign path multiplies
    # by density itself, and converting here would make it multiply twice.
    assert raw["deltaP"] == KINEMATIC_DELTA_P
    assert "convertedFromKinematic" not in raw

    product = _drop(product_patch_metrics(case))
    assert product["unit"] == "Pa"
    assert product["convertedFromKinematic"] is True
    assert product["densityKgPerM3"] == DENSITY
    assert product["deltaP"] == KINEMATIC_DELTA_P * DENSITY


def test_compressible_pressure_is_left_alone(tmp_path):
    case = _case(tmp_path, "[1 -1 -2 0 0 0 0]")
    product = _drop(product_patch_metrics(case))
    assert product["deltaP"] == KINEMATIC_DELTA_P
    assert "convertedFromKinematic" not in product


def test_unknown_density_is_relabelled_not_mislabelled(tmp_path):
    case = _case(tmp_path, "[0 2 -2 0 0 0 0]", density=None)
    product = _drop(product_patch_metrics(case))
    assert product["unit"] == "m2/s2"
    assert product["deltaP"] == KINEMATIC_DELTA_P
