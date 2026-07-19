"""Fail-closed continuation gate for the frozen-v2 64/96/128 family."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .open_boundary_campaign import SCHEMA as OPEN_BOUNDARY_SCHEMA

SCHEMA = "flowlab.frozen-v2-open-boundary-continuation.v1"
SCHEDULE = (64, 96, 128)


def continuation_decision(open_boundary_report: Path) -> dict[str, Any]:
    try:
        report = json.loads(open_boundary_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema": SCHEMA, "status": "blocked", "schedule": list(SCHEDULE), "reasons": [f"open-boundary campaign report is unreadable: {exc}"], "next": []}
    accepted = report.get("schema") == OPEN_BOUNDARY_SCHEMA and report.get("status") == "accepted"
    return {
        "schema": SCHEMA,
        "status": "unlocked" if accepted else "blocked",
        "schedule": list(SCHEDULE),
        "reasons": [] if accepted else ["The forced MMS and open-pipe BC stages have not both passed."],
        "next": [
            "Run each 64/96/128 level through immutable-surface hash and patch checks, checkMesh, and unchanged open-pipe exact-init gates.",
            "Run V&V/GCI only when every exact-init gate passes.",
            "Keep MPI timing blocked until V&V/GCI passes; use native AMD64 for any performance conclusion.",
        ] if accepted else ["Retain rejected BC evidence and resolve the named operator/source, BC-coupling, or open-pipe formulation defect."],
    }
