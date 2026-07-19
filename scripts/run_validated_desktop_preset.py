#!/usr/bin/env python3
"""Run the exact desktop validated preset through FlowLab's public API surface."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import app as app_module
from server.flowlab.execution import JobManager, TERMINAL_STATUSES
from server.flowlab.validated_preset import OPEN_BOUNDARY_BENCHMARK_ID


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(runtime_root: Path, output: Path, timeout_seconds: float) -> dict:
    if output.exists():
        raise ValueError(f"refusing to overwrite {output}")
    app_module.CASES.clear()
    app_module.JOB_MANAGER = JobManager(runtime_root=runtime_root)
    client = TestClient(app_module.app)
    started = time.monotonic()
    response = client.post(f"/api/benchmarks/validated/{OPEN_BOUNDARY_BENCHMARK_ID}/jobs")
    response.raise_for_status()
    launched = response.json()
    job = launched["job"]
    while job["status"] not in TERMINAL_STATUSES:
        if time.monotonic() - started > timeout_seconds:
            client.post(f"/api/jobs/{job['id']}/cancel")
            raise TimeoutError(f"validated preset exceeded {timeout_seconds:.0f}s")
        time.sleep(0.25)
        polled = client.get(f"/api/jobs/{job['id']}")
        polled.raise_for_status()
        job = polled.json()

    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    validated = result.get("validatedBenchmark") if isinstance(result, dict) else None
    report = {
        "schema": "flowlab.desktop_validated_preset_smoke.v1",
        "status": "accepted" if job["status"] == "complete" and isinstance(validated, dict) and validated.get("allChecksPassed") is True else "rejected",
        "elapsedSeconds": time.monotonic() - started,
        "case": {
            "id": launched["case"]["id"],
            "projectName": launched["case"]["projectName"],
            "fileCount": len(launched["case"]["files"]),
            "manifestSha256": hashlib.sha256(launched["case"]["files"]["flowlab_case_manifest.json"].encode("utf-8")).hexdigest(),
            "evidenceCapability": launched["case"]["evidenceCapability"],
        },
        "job": {
            "id": job["id"],
            "status": job["status"],
            "execution": job["execution"],
            "exitCode": job.get("exitCode"),
            "error": job.get("error"),
            "caseDir": job.get("caseDir"),
            "resultFileCount": len(result.get("resultFiles", [])) if isinstance(result, dict) else 0,
            "diagnosticFileCount": len(result.get("diagnosticFiles", [])) if isinstance(result, dict) else 0,
            "lastLogs": job.get("logs", [])[-20:],
        },
        "validatedBenchmark": validated,
    }
    _write(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    report = run(args.runtime_root.resolve(), args.output.resolve(), args.timeout_seconds)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
