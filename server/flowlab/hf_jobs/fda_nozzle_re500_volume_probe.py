# /// script
# dependencies = ["huggingface-hub==1.8.0"]
# ///
"""Verify a revision-pinned private dataset mount and durable write-back."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from huggingface_hub import HfApi


repo_id = os.environ["FLOWLAB_ARTIFACT_REPO"]
source = Path(os.environ["FLOWLAB_MOUNTED_PROBE"])
expected = os.environ["FLOWLAB_EXPECTED_PAYLOAD_SHA256"]
contract_id = os.environ["FLOWLAB_CONTRACT_ID"]
job_id = os.environ["JOB_ID"]
if not source.is_file():
    raise RuntimeError(f"mounted probe payload missing: {source}")
actual = hashlib.sha256(source.read_bytes()).hexdigest()
if actual != expected:
    raise RuntimeError(f"mounted payload hash mismatch: {actual}")
record = {
    "schema": "flowlab.fda-nozzle-re500-hf-volume-probe.v1",
    "contractId": contract_id,
    "jobId": job_id,
    "mountedPath": str(source),
    "expectedSha256": expected,
    "actualSha256": actual,
    "readOnlyRevisionPinnedMountPassed": True,
    "nonpromotional": True,
    "solverInvoked": False,
    "promotionAuthorized": False,
}
target = f"volume-probes/{contract_id}/{job_id}/volume-probe.json"
api = HfApi(token=os.environ["HF_TOKEN"])
if api.file_exists(repo_id, target, repo_type="dataset"):
    raise RuntimeError(f"refusing to overwrite existing volume probe: {target}")
commit = api.upload_file(
    (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(),
    target,
    repo_id,
    repo_type="dataset",
)
record["hubCommit"] = commit.oid
print("FLOWLAB_VOLUME_PROBE=" + json.dumps(record, sort_keys=True))
