"""Hardened fail-closed runner for the nonpromotional FDA HF coarse pilot."""

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
import subprocess
import tarfile
import threading
import time
from typing import Any, Optional

from huggingface_hub import CommitOperationAdd, HfApi


REQUIRED_RESIDUAL_FIELDS = {"Ux", "Uy", "Uz", "p"}
EVIDENCE_NAMES = ("artifacts.tar.gz", "artifacts.sha256", "result.json")


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
        names: set[str] = set()
        for member in tar.getmembers():
            if member.name in names:
                raise ValueError(f"duplicate archive member: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise ValueError(f"unsupported archive member type: {member.name}")
            names.add(member.name)
            resolved = (destination / member.name).resolve()
            if root not in resolved.parents and resolved != root:
                raise ValueError(f"unsafe archive member: {member.name}")
        tar.extractall(destination)


def read_int(path: Path) -> Optional[int]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return None if value == "max" else int(value)


def key_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values


def process_tree_memory() -> dict[str, int]:
    parent_by_pid: dict[int, int] = {}
    status_by_pid: dict[int, dict[str, int]] = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            status = (proc / "status").read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        values: dict[str, int] = {}
        parent = None
        for line in status:
            ppid = re.match(r"PPid:\s+(\d+)", line)
            memory = re.match(r"(VmRSS|VmHWM):\s+(\d+)\s+kB", line)
            if ppid:
                parent = int(ppid.group(1))
            elif memory:
                values[memory.group(1)] = int(memory.group(2)) * 1024
        if parent is not None:
            pid = int(proc.name)
            parent_by_pid[pid] = parent
            status_by_pid[pid] = values
    descendants = {os.getpid()}
    changed = True
    while changed:
        changed = False
        for pid, parent in parent_by_pid.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return {
        "processCount": len(descendants),
        "rssBytes": sum(status_by_pid.get(pid, {}).get("VmRSS", 0) for pid in descendants),
        "highWaterBytes": sum(status_by_pid.get(pid, {}).get("VmHWM", 0) for pid in descendants),
    }


def cgroup_memory() -> dict[str, Any]:
    root = Path("/sys/fs/cgroup")
    if (root / "cgroup.controllers").is_file():
        return {
            "version": 2,
            "currentBytes": read_int(root / "memory.current"),
            "peakBytes": read_int(root / "memory.peak"),
            "limitBytes": read_int(root / "memory.max"),
            "swapCurrentBytes": read_int(root / "memory.swap.current"),
            "swapLimitBytes": read_int(root / "memory.swap.max"),
            "events": key_values(root / "memory.events"),
        }
    legacy = root / "memory"
    return {
        "version": 1,
        "currentBytes": read_int(legacy / "memory.usage_in_bytes"),
        "peakBytes": read_int(legacy / "memory.max_usage_in_bytes"),
        "limitBytes": read_int(legacy / "memory.limit_in_bytes"),
        "oomControl": key_values(legacy / "memory.oom_control"),
    }


def telemetry(path: Path, stop: threading.Event, interval: float = 2.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        row = {
            "capturedAt": now(),
            "loadAverage": os.getloadavg(),
            "processTreeMemory": process_tree_memory(),
            "cgroupMemory": cgroup_memory(),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        if stop.wait(interval):
            break


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
    time_matches = list(re.finditer(r"^Time = (\d+)\s*$", text, re.MULTILINE))
    times = [int(match.group(1)) for match in time_matches]
    final_block = ""
    if time_matches:
        start = time_matches[-1].end()
        final_block = text[start:]
    residuals: dict[str, float] = {}
    for field, value in re.findall(
        r"Solving for (Ux|Uy|Uz|p),.*?Final residual = ([-+0-9.eE]+)", final_block
    ):
        residuals[field] = float(value)
    continuity = [
        abs(float(value))
        for value in re.findall(
            r"time step continuity errors\s*:\s*sum local = ([-+0-9.eE]+)", final_block
        )
    ]
    complete = set(residuals) == REQUIRED_RESIDUAL_FIELDS
    return {
        "latestLoggedTime": max(times) if times else None,
        "terminalEnd": bool(re.search(r"^End\s*$", text, re.MULTILINE)),
        "finalResiduals": residuals,
        "completeFinalResidualSet": complete,
        "maximumFinalResidual": max(residuals.values()) if complete else None,
        "finalAbsoluteContinuitySumLocal": continuity[-1] if continuity else None,
    }


def parse_internal(path: Path, kind: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf"internalField\s+nonuniform\s+List<{kind}>\s+\d+\s*\((.*?)\)\s*;",
        text,
        re.DOTALL,
    )
    if not match:
        raise ValueError(f"cannot parse {kind} internalField: {path}")
    if kind == "vector":
        rows = re.findall(
            r"\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)",
            match.group(1),
        )
        flat = [float(item) for row in rows for item in row]
    else:
        flat = [float(value) for value in re.findall(r"[-+0-9.eE]+", match.group(1))]
    if not flat or not all(math.isfinite(value) for value in flat):
        raise ValueError(f"invalid {kind} internalField values: {path}")
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
        capture_output=True, text=True, check=False,
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
        "declaredSpaceId": os.environ.get("FLOWLAB_IMAGE_SPACE_ID"),
        "declaredSpaceCommit": os.environ.get("FLOWLAB_IMAGE_SPACE_COMMIT"),
        "declaredRegistryDigest": os.environ.get("FLOWLAB_IMAGE_REGISTRY_DIGEST"),
    }


def no_oom_events(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        cgroup = row.get("cgroupMemory", {})
        events = cgroup.get("events", {})
        oom_control = cgroup.get("oomControl", {})
        if not events and not oom_control:
            return False
        if any(int(events.get(name, 0)) > 0 for name in ("oom", "oom_kill", "oom_group_kill")):
            return False
        if int(oom_control.get("oom_kill", 0)) > 0:
            return False
    return True


def telemetry_complete(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    for row in rows:
        cgroup = row.get("cgroupMemory", {})
        process = row.get("processTreeMemory", {})
        if any(cgroup.get(name) is None for name in ("currentBytes", "peakBytes", "limitBytes")):
            return False
        if not cgroup.get("events") and not cgroup.get("oomControl"):
            return False
        if any(int(process.get(name, 0)) <= 0 for name in ("processCount", "rssBytes", "highWaterBytes")):
            return False
    return True


def memory_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "samples": len(rows),
        "maximumCgroupCurrentBytes": max((row["cgroupMemory"]["currentBytes"] for row in rows), default=None),
        "maximumCgroupPeakBytes": max((row["cgroupMemory"]["peakBytes"] for row in rows), default=None),
        "cgroupLimitBytes": rows[-1]["cgroupMemory"]["limitBytes"] if rows else None,
        "maximumProcessTreeRssBytes": max((row["processTreeMemory"]["rssBytes"] for row in rows), default=None),
        "maximumProcessTreeHighWaterBytes": max((row["processTreeMemory"]["highWaterBytes"] for row in rows), default=None),
        "noOomEvents": no_oom_events(rows),
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
    targets = {name: f"{prefix}/{job_id}/{name}" for name in EVIDENCE_NAMES}
    existing = [name for name, target in targets.items() if api.file_exists(repo_id, target, repo_type="dataset")]
    if existing:
        raise RuntimeError(f"refusing partial or complete remote evidence overwrite: {existing}")
    input_archive_sha = sha256(input_archive)
    if input_archive_sha != expected_input_sha:
        raise ValueError("qualification input archive hash mismatch")

    work = Path("/tmp/flowlab-hf-coarse-r2")
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
        fields = {"U": parse_internal(final_time / "U", "vector"), "p": parse_internal(final_time / "p", "scalar")}
    rows = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines()]
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
        "completeFinalResidualSet": solver["completeFinalResidualSet"],
        "finalResidual": solver["maximumFinalResidual"] is not None and solver["maximumFinalResidual"] <= 1.0e-10,
        "continuity": solver["finalAbsoluteContinuitySumLocal"] is not None and solver["finalAbsoluteContinuitySumLocal"] <= 1.0e-9,
        "finalFieldsParsed": fields.get("U", {}).get("count") == expected_cells * 3 and fields.get("p", {}).get("count") == expected_cells,
        "telemetryPresent": bool(rows),
        "completeMemoryTelemetry": telemetry_complete(rows),
        "noOomEvents": no_oom_events(rows),
    }
    result = {
        "schema": "flowlab.fda-nozzle-re500-hf-coarse-pilot.v2",
        "contractSha256": expected_contract_sha,
        "artifactRepository": repo_id,
        "artifactPrefix": prefix,
        "inputArchiveSha256": input_archive_sha,
        "jobId": job_id,
        "flavor": os.environ.get("FLOWLAB_FLAVOR"),
        "startedAt": started_at,
        "completedAt": now(),
        "wallSeconds": wall_seconds,
        "image": identity,
        "environment": {"cpuCores": os.environ.get("CPU_CORES"), "memory": os.environ.get("MEMORY"), "accelerator": os.environ.get("ACCELERATOR")},
        "memorySummary": memory_summary(rows),
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
    write_json(work / "artifact-manifest.json", {
        "schema": "flowlab.fda-nozzle-re500-hf-artifact-manifest.v2",
        "jobId": job_id,
        "files": file_manifest(work),
        "promotionAuthorized": False,
    })
    archive = Path("/tmp") / f"flowlab-{job_id}-artifacts.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(work, arcname="qualification", recursive=True)
    archive_sha = sha256(archive)
    result["artifactArchiveSha256"] = archive_sha
    result_body = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    commit = api.create_commit(
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Add FlowLab HF r2 evidence for {job_id}",
        operations=[
            CommitOperationAdd(path_in_repo=targets["artifacts.tar.gz"], path_or_fileobj=archive),
            CommitOperationAdd(path_in_repo=targets["artifacts.sha256"], path_or_fileobj=(archive_sha + "  artifacts.tar.gz\n").encode()),
            CommitOperationAdd(path_in_repo=targets["result.json"], path_or_fileobj=result_body),
        ],
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
