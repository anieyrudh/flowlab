# Evidence retention and Git boundary

Status: **source-controlled code; local raw evidence; explicit compact evidence**

This policy separates reproducible source truth from large scientific execution
evidence. Git is not the raw solver archive and the raw solver archive is not a
substitute for Git history.

## 1. Normal Git content

Track these with ordinary `git add`:

- application and solver source under `src/`, `server/`, `desktop/`, and
  `scripts/`;
- tests, schemas, configuration, Docker build recipes, and dependency lockfiles;
- documentation, runbooks, decision logs, and claim boundaries;
- benchmark registry files and declarative `benchmark.json` fixtures;
- source for retained scientific audit utilities under `benchmarks/tools/`;
- compact evidence explicitly listed in `benchmarks/tracked-evidence.txt`.

Do not commit secrets, machine-local paths, installed dependencies, build
products, app bundles, caches, transient logs, raw external archives, or bulk
solver output.

## 2. Local scientific evidence

Directories matching `benchmarks/cases/**/runs/` and
`benchmarks/cases/**/campaigns/` are ignored by default. They may contain raw
meshes, time directories, fields, post-processing output, logs, external data,
and campaign state. Ignored does not mean disposable.

Retention and deletion decisions remain governed by the campaign-specific
runbook and `docs/validation/laminar-all-hex/DATA_RETENTION.md`. In particular:

- never edit evidence in place to make a gate pass;
- never compact or delete an active or unresolved campaign;
- preserve frozen contracts, source hashes, image digests, manifests, issue
  records, assessment reports, and required reproduction recipes;
- archive before deleting material raw evidence, and record what was removed,
  why it was safe to remove, and how it can be reproduced;
- keep external experimental source files immutable and record their original
  hashes and provenance.

## 3. Compact evidence admitted to Git

`benchmarks/tracked-evidence.txt` is the allowlist for files intentionally
tracked from otherwise ignored run/campaign trees. Admission requires all of:

1. a bounded claim and accepted schema;
2. a recorded SHA-256 and provenance chain;
3. no secrets, personal data, or unlicensed redistributable source data;
4. a small, reviewable artifact rather than raw fields or meshes;
5. a demonstrated product, test, or audit need;
6. an explicit review that the artifact does not overstate scientific status.

The allowlist is not a promotion mechanism. Adding a report to Git cannot turn
a failed or incomplete campaign into accepted validation.

## 4. Promotion workflow

1. Freeze code, contract, thresholds, observation operators, uncertainty rules,
   and runtime provenance before execution.
2. Run in an ignored campaign directory. Treat partial output as work in
   progress, never as accepted evidence.
3. Complete every required case, post-processing step, uncertainty calculation,
   negative control, reproducibility check, and product contract.
4. Generate the fail-closed final assessment. A nonzero scientific-decision
   exit code is a scientific result, not automatically an infrastructure error.
5. Independently review hashes, gates, claim scope, and retained inputs.
6. Only if every required gate is true and `promotionAuthorized` is explicitly
   true may the validated pointer and product surface be changed.
7. Add only the reviewed compact artifacts to `tracked-evidence.txt`; retain or
   archive the raw campaign separately.

## 5. Remote publication

The local repository starts without a remote. Before adding one, verify the
exact private repository owner/name and review the complete staged file list and
largest blobs. Never infer a remote from the folder name and never publish the
raw evidence tree by using `git add -f` broadly.
