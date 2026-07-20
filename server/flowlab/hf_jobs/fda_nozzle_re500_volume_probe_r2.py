# /// script
# dependencies = ["huggingface-hub==1.8.0"]
# ///
"""Verify an immutable mounted payload and atomic write-back for FlowLab HF r2."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi


repo_id = os.environ["FLOWLAB_ARTIFACT_REPO"]
source = Path(os.environ["FLOWLAB_MOUNTED_PROBE"])
expected = os.environ["FLOWLAB_EXPECTED_PAYLOAD_SHA256"]
mounted_revision = os.environ["FLOWLAB_MOUNTED_REVISION"]
contract_id = os.environ["FLOWLAB_CONTRACT_ID"]
source_job_id = os.environ["FLOWLAB_SOURCE_JOB_ID"]
job_id = os.environ["JOB_ID"]
if not source.is_file():
    raise RuntimeError(f"mounted probe payload missing: {source}")
actual = hashlib.sha256(source.read_bytes()).hexdigest()
if actual != expected:
    raise RuntimeError(f"mounted payload hash mismatch: {actual}")
record = {
    "schema": "flowlab.fda-nozzle-re500-hf-volume-probe.v2",
    "contractId": contract_id,
    "artifactRepository": repo_id,
    "sourceJobId": source_job_id,
    "jobId": job_id,
    "mountedPath": str(source),
    "mountedRevision": mounted_revision,
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
body = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
commit = api.create_commit(
    repo_id=repo_id,
    repo_type="dataset",
    commit_message=f"Add FlowLab HF volume probe r2 {job_id}",
    operations=[CommitOperationAdd(path_in_repo=target, path_or_fileobj=body)],
)
record["hubCommit"] = commit.oid
print("FLOWLAB_VOLUME_PROBE=" + json.dumps(record, sort_keys=True))
