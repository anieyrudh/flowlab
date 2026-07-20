"""Fail-closed remote runner for the nonpromotional FDA coarse HF pilot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
from typing import Any

from huggingface_hub import HfApi


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(command: str, cwd: Path, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as stream:
        process = subprocess.run(
            ["bash", "-lc", "source /opt/openfoam11/etc/bashrc; " + command],
            cwd=cwd,
            stdout=stream,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return process.returncode


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as tar:
        root = destination.resolve()
        for member in tar.getmembers():
            resolved = (destination / member.name).resolve()
            if root not in resolved.parents and resolved != root:
                raise ValueError(f"unsafe archive member: {member.name}")
        tar.extractall(destination)


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    path = Path("/proc/meminfo")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+(\d+)\s+kB", line)
            if match:
                values[match.group(1)] = int(match.group(2)) * 1024
    return values


def telemetry(path: Path, stop: threading.Event, interval: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while not stop.is_set():
        row = {
            "capturedAt": now(),
            "loadAverage": os.getloadavg(),
            "memory": meminfo(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        stop.wait(interval)


def parse_check_mesh(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    cells = re.search(r"^\s*cells:\s+(\d+)", text, re.MULTILINE)
    hexes = re.search(r"^\s*hexahedra:\s+(\d+)", text, re.MULTILINE)
    return {
        "meshOk": "Mesh OK" in text,
        "cells": int(cells.group(1)) if cells else None,
        "hexahedra": int(hexes.group(1)) if hexes else None,
        "strictAllHex": bool(cells and hexes and cells.group(1) == hexes.group(1)),
    }


def parse_solver(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    times = [int(value) for value in re.findall(r"^Time = (\d+)\s*$", text, re.MULTILINE)]
    residuals: dict[str, float] = {}
    for field, value in re.findall(
        r"Solving for (Ux|Uy|Uz|p),.*?Final residual = ([-+0-9.eE]+)", text
    ):
        residuals[field] = float(value)
    continuity = [
        abs(float(value))
        for value in re.findall(
            r"time step continuity errors\s*:\s*sum local = ([-+0-9.eE]+)", text
        )
    ]
    return {
        "latestLoggedTime": max(times) if times else None,
        "terminalEnd": bool(re.search(r"^End\s*$", text, re.MULTILINE)),
        "finalResiduals": residuals,
        "maximumFinalResidual": max(residuals.values()) if residuals else None,
        "finalAbsoluteContinuitySumLocal": continuity[-1] if continuity else None,
    }


def parse_internal(path: Path, vector: bool) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if vector:
        match = re.search(
            r"internalField\s+nonuniform\s+List<vector>\s+\d+\s*\((.*?)\)\s*;",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(f"cannot parse vector internalField: {path}")
        values = [
            tuple(float(item) for item in row)
            for row in re.findall(
                r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)",
                match.group(1),
            )
        ]
        flat = [item for row in values for item in row]
    else:
        match = re.search(
            r"internalField\s+nonuniform\s+List<scalar>\s+\d+\s*\((.*?)\)\s*;",
            text,
            re.DOTALL,
        )
        if not match:
            raise ValueError(f"cannot parse scalar internalField: {path}")
        flat = [float(value) for value in re.findall(r"[-+0-9.eE]+", match.group(1))]
    canonical = "\n".join(format(value, ".15e") for value in flat).encode()
    return {
        "count": len(flat),
        "sum": math.fsum(flat),
        "sumSquares": math.fsum(value * value for value in flat),
        "minimum": min(flat),
        "maximum": max(flat),
        "canonical15Sha256": hashlib.sha256(canonical).hexdigest(),
    }


def file_manifest(root: Path) -> list[dict[str, Any]]:
    return [
        {"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def image_info() -> dict[str, Any]:
    foam = subprocess.run(
        ["bash", "-lc", "source /opt/openfoam11/etc/bashrc; foamVersion"],
        capture_output=True,
        text=True,
        check=False,
    )
    gmsh = subprocess.run(["gmsh", "--version"], capture_output=True, text=True, check=False)
    foam_raw = (foam.stdout + "\n" + foam.stderr).strip()
    gmsh_raw = (gmsh.stdout + "\n" + gmsh.stderr).strip()
    foam_version = foam_raw.splitlines()[-1] if foam_raw else ""
    gmsh_version = gmsh_raw.splitlines()[-1] if gmsh_raw else ""
    if gmsh_version.endswith("-nox"):
        gmsh_version = gmsh_version[:-4]
    return {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "openFoamVersion": foam_version,
        "openFoamVersionRaw": foam_raw,
        "openFoamExitCode": foam.returncode,
        "gmshVersion": gmsh_version,
        "gmshVersionRaw": gmsh_raw,
        "gmshExitCode": gmsh.returncode,
        "runnerSha256": sha256(Path(__file__)),
    }


def run_coarse() -> int:
    repo_id = os.environ["FLOWLAB_ARTIFACT_REPO"]
    prefix = os.environ["FLOWLAB_ARTIFACT_PREFIX"].rstrip("/")
    input_archive = Path(os.environ["FLOWLAB_INPUT_ARCHIVE"])
    expected_input_sha = os.environ["FLOWLAB_INPUT_SHA256"]
    expected_contract_sha = os.environ["FLOWLAB_CONTRACT_SHA256"]
    expected_cells = int(os.environ["FLOWLAB_EXPECTED_CELLS"])
    job_id = os.environ["JOB_ID"]
    api = HfApi(token=os.environ["HF_TOKEN"])
    result_target = f"{prefix}/{job_id}/result.json"
    if api.file_exists(repo_id, result_target, repo_type="dataset"):
        raise RuntimeError(f"refusing to overwrite existing remote evidence: {result_target}")
    if sha256(input_archive) != expected_input_sha:
        raise ValueError("qualification input archive hash mismatch")

    work = Path("/tmp/flowlab-hf-coarse")
    if work.exists():
        raise ValueError(f"refusing nonempty remote work path: {work}")
    safe_extract(input_archive, work)
    case = work / "case"
    contract = work / "qualification-contract.json"
    if sha256(contract) != expected_contract_sha:
        raise ValueError("mounted qualification contract hash mismatch")

    logs = work / "logs"
    telemetry_path = logs / "resource-telemetry.jsonl"
    stop = threading.Event()
    monitor = threading.Thread(target=telemetry, args=(telemetry_path, stop), daemon=True)
    monitor.start()
    started_at = now()
    started = time.monotonic()
    codes: dict[str, int] = {}
    try:
        for name, command in (
            ("blockMesh", "blockMesh"),
            ("checkMesh", "checkMesh -allTopology -allGeometry"),
            ("foamRun", "foamRun -solver incompressibleFluid"),
        ):
            codes[name] = run(command, case, logs / f"{name}.log")
            if codes[name] != 0:
                break
    finally:
        stop.set()
        monitor.join(timeout=10)
    wall_seconds = time.monotonic() - started

    mesh = parse_check_mesh(logs / "checkMesh.log")
    solver = parse_solver(logs / "foamRun.log")
    final_time = case / "800"
    fields: dict[str, Any] = {}
    if final_time.is_dir() and (final_time / "U").is_file() and (final_time / "p").is_file():
        fields = {
            "U": parse_internal(final_time / "U", True),
            "p": parse_internal(final_time / "p", False),
        }
    identity = image_info()
    checks = {
        "commandsComplete": codes == {"blockMesh": 0, "checkMesh": 0, "foamRun": 0},
        "expectedArchitecture": identity["machine"] in {"x86_64", "amd64"},
        "openFoam11": identity["openFoamVersion"] in {"11", "OpenFOAM-11"},
        "gmsh4152": identity["gmshVersion"] == "4.15.2",
        "meshOk": mesh["meshOk"],
        "strictAllHex": mesh["strictAllHex"],
        "expectedCells": mesh["cells"] == expected_cells,
        "time800": solver["latestLoggedTime"] == 800 and final_time.is_dir(),
        "terminalEnd": solver["terminalEnd"],
        "finalResidual": solver["maximumFinalResidual"] is not None
        and solver["maximumFinalResidual"] <= 1.0e-10,
        "continuity": solver["finalAbsoluteContinuitySumLocal"] is not None
        and solver["finalAbsoluteContinuitySumLocal"] <= 1.0e-9,
        "finalFieldsParsed": set(fields) == {"U", "p"},
        "telemetryPresent": telemetry_path.is_file() and telemetry_path.stat().st_size > 0,
    }
    result = {
        "schema": "flowlab.fda-nozzle-re500-hf-coarse-pilot.v1",
        "contractSha256": expected_contract_sha,
        "jobId": job_id,
        "flavor": os.environ.get("FLOWLAB_FLAVOR"),
        "startedAt": started_at,
        "completedAt": now(),
        "wallSeconds": wall_seconds,
        "image": identity,
        "environment": {
            "cpuCores": os.environ.get("CPU_CORES"),
            "memory": os.environ.get("MEMORY"),
            "accelerator": os.environ.get("ACCELERATOR"),
        },
        "exitCodes": codes,
        "mesh": mesh,
        "solver": solver,
        "fields": fields,
        "checks": checks,
        "passesQualificationLane": all(checks.values()),
        "pressureRole": "mandatory-diagnostic-nonpromotional",
        "scientificPromotionAuthorized": False,
        "desktopPromotionAuthorized": False,
        "promotionAuthorized": False,
    }
    write_json(work / "result.json", result)
    manifest = {
        "schema": "flowlab.fda-nozzle-re500-hf-artifact-manifest.v1",
        "jobId": job_id,
        "files": file_manifest(work),
        "promotionAuthorized": False,
    }
    write_json(work / "artifact-manifest.json", manifest)
    archive = Path("/tmp") / f"flowlab-{job_id}-artifacts.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(work, arcname="qualification")
    archive_sha = sha256(archive)
    result["artifactArchiveSha256"] = archive_sha
    write_json(work / "result.json", result)
    api.upload_file(archive, f"{prefix}/{job_id}/artifacts.tar.gz", repo_id, repo_type="dataset")
    api.upload_file(
        (archive_sha + "  artifacts.tar.gz\n").encode(),
        f"{prefix}/{job_id}/artifacts.sha256",
        repo_id,
        repo_type="dataset",
    )
    commit = api.upload_file(
        (json.dumps(result, indent=2, sort_keys=True) + "\n").encode(),
        result_target,
        repo_id,
        repo_type="dataset",
    )
    result["hubCommit"] = commit.oid
    print("FLOWLAB_COARSE_RESULT=" + json.dumps(result, sort_keys=True))
    return 0 if result["passesQualificationLane"] else 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "image-info", "coarse"))
    args = parser.parse_args()
    if args.command == "serve":
        return subprocess.call(["python3", "-m", "http.server", "7860"])
    if args.command == "image-info":
        print(json.dumps(image_info(), indent=2, sort_keys=True))
        return 0
    return run_coarse()


if __name__ == "__main__":
    raise SystemExit(main())
