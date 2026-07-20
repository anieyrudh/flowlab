# /// script
# dependencies = ["huggingface-hub==1.8.0"]
# ///
"""Atomic architecture and durable-artifact probe for FlowLab HF r2."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil

from huggingface_hub import CommitOperationAdd, HfApi


repo_id = os.environ["FLOWLAB_ARTIFACT_REPO"]
contract_id = os.environ["FLOWLAB_CONTRACT_ID"]
job_id = os.environ["JOB_ID"]
prefix = f"probes/{contract_id}/{job_id}"
api = HfApi(token=os.environ["HF_TOKEN"])
api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
targets = {
    "payload": f"{prefix}/payload.txt",
    "record": f"{prefix}/probe.json",
}
existing = [name for name, target in targets.items() if api.file_exists(repo_id, target, repo_type="dataset")]
if existing:
    raise RuntimeError(f"refusing partial or complete probe overwrite: {existing}")

payload = b"flowlab-hf-volume-probe-v2\n"
disk = shutil.disk_usage("/")
record = {
    "schema": "flowlab.fda-nozzle-re500-hf-probe.v2",
    "contractId": contract_id,
    "artifactRepository": repo_id,
    "artifactPrefix": prefix,
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
    "payloadPath": targets["payload"],
    "payloadSha256": hashlib.sha256(payload).hexdigest(),
    "nonpromotional": True,
    "solverInvoked": False,
    "promotionAuthorized": False,
}
body = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
commit = api.create_commit(
    repo_id=repo_id,
    repo_type="dataset",
    commit_message=f"Add FlowLab HF architecture probe r2 {job_id}",
    operations=[
        CommitOperationAdd(path_in_repo=targets["payload"], path_or_fileobj=payload),
        CommitOperationAdd(path_in_repo=targets["record"], path_or_fileobj=body),
    ],
)
record["hubCommit"] = commit.oid
print("FLOWLAB_PROBE=" + json.dumps(record, sort_keys=True))
