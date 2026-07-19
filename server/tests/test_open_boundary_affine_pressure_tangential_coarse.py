from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.flowlab.open_boundary_affine_flux_pressure_probe import _case_files
from server.flowlab.open_boundary_affine_pressure_tangential_coarse import (
    ITERATIONS,
    _authorized_upstream,
)
from server.flowlab.open_boundary_mms_redesign import AffineCrossflowMms
from server.flowlab.open_boundary_affine_pressure_tangential_probe import SCHEMA


def _report(path: Path, *, status: str = "authorized", check: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "status": status,
                "checks": {"one": check, "two": check},
            }
        ),
        encoding="utf-8",
    )


def test_coarse_gate_requires_every_upstream_check(tmp_path: Path) -> None:
    path = tmp_path / "one-step.json"
    _report(path)

    report = _authorized_upstream(path)

    assert report["status"] == "authorized"


def test_coarse_case_writes_only_the_final_primary_fields() -> None:
    files = _case_files(12, AffineCrossflowMms(), iterations=ITERATIONS)

    assert "endTime 100" in files["system/controlDict"]
    assert "writeControl timeStep; writeInterval 100;" in files[
        "system/controlDict"
    ]


@pytest.mark.parametrize(
    ("status", "check"),
    (("blocked", True), ("authorized", False)),
)
def test_coarse_gate_rejects_incomplete_upstream(
    tmp_path: Path,
    status: str,
    check: bool,
) -> None:
    path = tmp_path / "one-step.json"
    _report(path, status=status, check=check)

    with pytest.raises(ValueError):
        _authorized_upstream(path)
