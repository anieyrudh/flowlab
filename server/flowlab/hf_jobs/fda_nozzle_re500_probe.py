# /// script
# dependencies = ["huggingface-hub==1.8.0"]
# ///
"""Tiny architecture and durable-artifact probe for FlowLab HF qualification."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
from huggingface_hub import HfApi


repo_id = os.environ["FLOWLAB_ARTIFACT_REPO"]
contract_id = os.environ["FLOWLAB_CONTRACT_ID"]
job_id = os.environ["JOB_ID"]
prefix = f"probes/{contract_id}/{job_id}"
api = HfApi(token=os.environ["HF_TOKEN"])
api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
target = f"{prefix}/probe.json"
if api.file_exists(repo_id, target, repo_type="dataset"):
    raise RuntimeError(f"refusing to overwrite existing probe artifact: {target}")

payload = b"flowlab-hf-volume-probe-v1\n"
payload_sha = hashlib.sha256(payload).hexdigest()
disk = shutil.disk_usage("/")
record = {
    "schema": "flowlab.fda-nozzle-re500-hf-probe.v1",
    "contractId": contract_id,
    "jobId": job_id,
    "machine": platform.machine(),
    "platform": platform.platform(),
    "python": platform.python_version(),
    "cpuCount": os.cpu_count(),
    "declaredCpuCores": os.environ.get("CPU_CORES"),
    "declaredMemory": os.environ.get("MEMORY"),
    "accelerator": os.environ.get("ACCELERATOR"),
    "diskTotalBytes": disk.total,
    "diskFreeBytes": disk.free,
    "payloadPath": f"{prefix}/payload.txt",
    "payloadSha256": payload_sha,
    "nonpromotional": True,
    "solverInvoked": False,
    "promotionAuthorized": False,
}
body = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
api.upload_file(payload, record["payloadPath"], repo_id, repo_type="dataset")
commit = api.upload_file(body, target, repo_id, repo_type="dataset")
record["hubCommit"] = commit.oid
print("FLOWLAB_PROBE=" + json.dumps(record, sort_keys=True))
