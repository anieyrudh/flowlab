# Result Large-File Reporting - 2026-06-15

FlowLab result collection remains bounded so solver jobs do not embed
uncontrolled VTK/VTU payloads into API responses. This update makes skipped
field outputs visible instead of silently stopping at the collection limit.

## Behavior

- Embedded VTK/VTU result payloads are still limited to the configured small
  result-file budget.
- Oversized VTK/VTU files are returned as `resultFiles` entries with
  `skipped: "file too large"`.
- Non-UTF-8 VTK/VTU files are returned with `skipped: "not UTF-8 text"`.
- If more VTK/VTU files exist after the bounded collection limit, FlowLab now
  adds one overflow metadata entry:

```json
{
  "path": "<additional-result-files>",
  "size": 2048,
  "skipped": "2 additional VTK/VTU result file(s) omitted after collection limit 8"
}
```

The frontend case summary counts embedded fields separately from skipped field
metadata, for example:

```text
Final artifacts: 2 field, 1 diagnostic · skipped 1 field, 1 diagnostic
```

Skipped field entries are not parsed into visualization snapshots. They exist
to show that solver output was present but intentionally not embedded.

## Bounded Artifact Fetch

The backend also exposes a bounded artifact index and readers:

```text
GET /api/jobs/{jobId}/artifacts?kind=result&limit=200
```

Example response:

```json
{
  "artifacts": [
    {
      "path": "VTK/case_0001.vtk",
      "size": 152000,
      "kind": "result",
      "fieldSummary": {
        "schema": "flowlab.result_field_summary.v1",
        "format": "legacy-vtk-ascii-v1",
        "pointCount": 4,
        "cellCount": 1,
        "fields": [{ "name": "p", "location": "point", "kind": "scalar", "tupleCount": 4, "min": 72000, "max": 260000, "mean": 154581.25, "stdDev": 70622.12, "p50": 143162.5, "p95": 247000 }]
      }
    },
    { "path": "VTK/case_0009.vtk", "size": 2190000, "kind": "result" }
  ],
  "count": 12,
  "truncated": false
}
```

The index is bounded, excludes `mesh/` inspection exports, and can list concrete
VTK/VTU paths that were omitted from `resultFiles` behind the
`<additional-result-files>` overflow placeholder. Result entries under the
normal result-file size cap also include `fieldSummary` when they parse through
the supported ASCII VTK/VTU subset, so the desktop can show available fields
before loading the full artifact.

`fieldSummary.fields[]` uses the same descriptive-stat convention for scalar
values and vector magnitudes: population standard deviation plus interpolated
P50/P95 percentiles.

```text
GET /api/jobs/{jobId}/artifact?path=VTK/case_0001.vtk
```

The endpoint only resolves paths inside the materialized case directory for
that job. It rejects path traversal, mesh inspection exports under `mesh/`, and
unsupported file types. It returns the same shape as embedded result metadata:

```json
{
  "path": "VTK/case_0001.vtk",
  "size": 32,
  "text": "# vtk DataFile Version 3.0\n...",
  "fieldSummary": {
    "schema": "flowlab.result_field_summary.v1",
    "format": "legacy-vtk-ascii-v1",
    "pointCount": 4,
    "cellCount": 1,
    "fields": [
      {
        "name": "p",
        "location": "point",
        "kind": "scalar",
        "tupleCount": 4,
        "min": 72000,
        "max": 260000,
        "mean": 154581.25,
        "stdDev": 70622.12,
        "p50": 143162.5,
        "p95": 247000
      }
    ]
  }
}
```

When a result artifact is small enough to read and uses the supported ASCII
VTK/VTU subset, normal `resultFiles` collection and full artifact fetches
include this `fieldSummary` object. Vector fields are summarized by magnitude
and keep their original point or cell location. Unsupported or partial VTK/VTU
files still return as artifacts with `fieldSummaryError` instead of blocking
job collection.

If the requested file is too large or not UTF-8 text, the endpoint returns
bounded skipped metadata instead of embedding content:

```json
{
  "path": "VTK/large_case.vtk",
  "size": 3500000,
  "skipped": "file too large"
}
```

For bounded server-side geometry and field thinning, a preview reader is
available:

```text
GET /api/jobs/{jobId}/artifact/preview?path=VTK/case_0001.vtk&pointLimit=500&cellLimit=500
```

The preview endpoint parses supported ASCII VTK/VTU result artifacts under the
server preview byte cap and returns bounded geometry plus field samples:

```json
{
  "path": "VTK/case_0001.vtk",
  "size": 152000,
  "schema": "flowlab.result_preview.v1",
  "sourcePointCount": 12000,
  "sourceCellCount": 8600,
  "pointCount": 498,
  "cellCount": 120,
  "truncated": true,
  "pointIndices": [0, 1, 2],
  "cellIndices": [0],
  "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
  "cells": [[0, 1, 2]],
  "fieldSamples": {
    "point": [{ "name": "p", "kind": "scalar", "values": [101325, 101300, 101250] }],
    "cell": []
  }
}
```

Cells are sampled first so returned cell connectivity remains valid after
point thinning. The endpoint also includes the same `fieldSummary` object used
by artifact collection. Oversized, non-text, or unsupported VTK/VTU files fail
closed with skipped metadata or a 400 error.

For large UTF-8 solver outputs, a chunk reader is available:

```text
GET /api/jobs/{jobId}/artifact/chunk?path=VTK/large_case.vtk&offset=0&limit=262144
```

The chunk endpoint uses the same path containment and supported-artifact checks
as the full artifact endpoint. It caps each response to the server chunk limit
and returns the next offset:

```json
{
  "path": "VTK/large_case.vtk",
  "size": 3500000,
  "offset": 0,
  "limit": 262144,
  "text": "# vtk DataFile Version 3.0\n...",
  "nextOffset": 262144,
  "complete": false
}
```

The desktop case summary shows `Index field files` for completed jobs with a
case directory. It renders the bounded result index as concrete VTK/VTU file
buttons plus compact preview actions. `Preview` calls the server-side thinning
endpoint for one artifact and displays the returned point/cell counts and field
sample metadata without loading the whole artifact into the browser parser.
`Preview sequence` runs the same bounded preview path over indexed VTK/VTU
artifacts in 24-file pages, deduplicates stable preview snapshots, sorts them
by inferred solver time, and feeds them into the existing playback, scrubber,
field timeline, and pinned-field handling. The next preview action appends the
next indexed page until all indexed previews are loaded. It is intentionally a
preview sequence, not a full-fidelity large-result stream. Selecting a file, or using
`Load skipped field` for a skipped concrete entry, assembles chunks up to the
browser parse cap, then passes the complete UTF-8 text to the existing VTK/VTU
parser and adds the result as a normal full-artifact timeline snapshot.
Overflow summary placeholders such as
`<additional-result-files>` remain metadata in `resultFiles`, but users can now
discover the concrete omitted paths through the artifact index.

## Remaining Limit

This is bounded indexing, field-summary metadata, capped preview-sequence
playback, preview thinning, and chunk access, not full large-result
visualization. Progressive large VTK/VTU parsing, adaptive server-side field
thinning for very large files, and canvas updates from partial chunks are still
pending.
