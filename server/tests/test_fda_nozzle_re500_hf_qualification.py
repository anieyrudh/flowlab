from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

from server.flowlab import fda_nozzle_re500_hf_qualification as hfq
from server.flowlab.fda_nozzle_re500_v3_mesh_preflight import _declared_cells


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "docs/validation/fda-nozzle-re500/HF_INFRASTRUCTURE_QUALIFICATION_CONTRACT.json"


def load_remote_runner():
    path = ROOT / "docker/openfoam11-gmsh415-immutable-amd64-hf/remote_runner.py"
    spec = importlib.util.spec_from_file_location("flowlab_hf_remote_runner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_hf_qualification_contract_passes_offline() -> None:
    contract = hfq.verify_contract(CONTRACT)
    assert contract["authorization"]["runCpuUpgradeCoarsePilot"] is True
    assert contract["authorization"]["runCpuXlCoarseComparison"] is True
    assert contract["authorization"]["runSixCaseCampaign"] is False
    assert contract["promotionAuthorized"] is False


def test_future_parallelism_is_isolated_and_phase_gated() -> None:
    design = hfq.verify_contract(CONTRACT)["futureConcurrencyDesign"]
    assert design["maximumConcurrentPhase1Jobs"] == 2
    assert design["maximumConcurrentPhase2Jobs"] == 4
    assert design["phase1MustPassBeforePhase2"] is True
    assert design["automaticProgressionBetweenPhases"] is False
    assert design["sharedMutableCaseDirectoryAllowed"] is False


def test_tampered_campaign_authorization_fails_closed(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["authorization"]["runSixCaseCampaign"] = True
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="fail closed"):
        hfq.verify_contract(path)


def test_tampered_probe_payload_fails_closed(tmp_path: Path) -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    contract["probe"]["payload"] = "different"
    path = tmp_path / "tampered-probe.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="payload hash mismatch"):
        hfq.verify_contract(path)


def test_prepared_coarse_source_has_exact_v3_cell_budget(tmp_path: Path) -> None:
    case = tmp_path / "case"
    hfq.prepare_case(case, "a" * 64)
    definition = json.loads((case / "case-definition.json").read_text(encoding="utf-8"))
    mesh = (case / "system/blockMeshDict").read_text(encoding="utf-8")
    assert definition["expectedCells"] == 44_256
    assert _declared_cells(mesh) == 44_256
    assert definition["promotionAuthorized"] is False


def test_prepare_case_refuses_overwrite(tmp_path: Path) -> None:
    case = tmp_path / "case"
    hfq.prepare_case(case, "a" * 64)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        hfq.prepare_case(case, "a" * 64)


def test_remote_parsers_enforce_terminal_numerical_evidence(tmp_path: Path) -> None:
    runner = load_remote_runner()
    check = tmp_path / "checkMesh.log"
    check.write_text("cells: 44256\nhexahedra: 44256\nMesh OK.\n", encoding="utf-8")
    solve = tmp_path / "foamRun.log"
    solve.write_text(
        "Time = 800\n"
        "Solving for Ux, Initial residual = 1e-8, Final residual = 1e-12, No Iterations 2\n"
        "Solving for Uy, Initial residual = 1e-8, Final residual = 2e-12, No Iterations 2\n"
        "Solving for Uz, Initial residual = 1e-8, Final residual = 3e-12, No Iterations 2\n"
        "Solving for p, Initial residual = 1e-8, Final residual = 4e-12, No Iterations 2\n"
        "time step continuity errors : sum local = 5e-13, global = 0, cumulative = 0\n"
        "End\n",
        encoding="utf-8",
    )
    assert runner.parse_check_mesh(check) == {
        "meshOk": True,
        "cells": 44_256,
        "hexahedra": 44_256,
        "strictAllHex": True,
    }
    parsed = runner.parse_solver(solve)
    assert parsed["latestLoggedTime"] == 800
    assert parsed["terminalEnd"] is True
    assert parsed["maximumFinalResidual"] == 4e-12
    assert parsed["finalAbsoluteContinuitySumLocal"] == 5e-13


def test_remote_field_parser_ignores_vector_boundary_values(tmp_path: Path) -> None:
    runner = load_remote_runner()
    field = tmp_path / "U"
    field.write_text(
        "internalField nonuniform List<vector>\n2\n(\n(1 2 3)\n(4 5 6)\n);\n"
        "boundaryField { inlet { value uniform (999 999 999); } }\n",
        encoding="utf-8",
    )
    parsed = runner.parse_internal(field, vector=True)
    assert parsed["count"] == 6
    assert parsed["sum"] == 21
    assert parsed["maximum"] == 6


def test_remote_archive_extraction_rejects_traversal(tmp_path: Path) -> None:
    runner = load_remote_runner()
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "payload"
    payload.write_text("x", encoding="utf-8")
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="../escape")
    with pytest.raises(ValueError, match="unsafe archive member"):
        runner.safe_extract(archive, tmp_path / "out")
