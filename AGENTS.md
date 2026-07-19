# FlowLab agent instructions

FlowLab is a desktop-only local fluid-simulation workstation. It combines an
instant browser-side 1D hydraulics model with local orchestration for OpenFOAM,
SU2, Code_Saturne, and MuJoCo. Software completeness, numerical verification,
independent experimental validation, product promotion, and external release
are separate states. Never collapse them into one readiness claim.

## Read first

Before changing scientific or product behavior, read:

1. `README.md`;
2. this file;
3. `docs/validation/laminar-all-hex/FINAL_ASSESSMENT.md`;
4. `docs/validation/laminar-all-hex/RESOLUTION_BACKLOG.md`;
5. `docs/evidence-retention-and-git-boundary.md`;
6. the runbook and frozen contract for the campaign being touched.

## Non-negotiable scientific boundaries

- Ordinary generated CFD cases are experimental. Passing software tests,
  `checkMesh`, or a solver exit code does not make them validated.
- The bounded desktop preset may be exposed only when the digest-checked final
  campaign report has every required gate true and sets
  `promotionAuthorized=true`. Otherwise the UI action stays hidden and the API
  fails closed with HTTP 409.
- Never weaken, remove, reinterpret, or tune a frozen gate after observing a
  result. Do not add post-hoc offsets or uncertainty allowances.
- Never edit retained evidence in place. New hypotheses require a new,
  prospectively declared experiment and separate output directory.
- Do not promote from a partial campaign, mesh preflight, a subset of cases, or
  an attractive aggregate metric. Every mandatory case and gate is required.
- Keep analytical/manufactured verification distinct from independent empirical
  validation. Keep emulated-container correctness distinct from native
  performance evidence.
- Do not claim general production CFD, arbitrary-geometry validation, turbulent,
  transient, multiphase, compressible, CAD, or hybrid-mesh validation from the
  current bounded laminar evidence.
- Mobile is out of scope. The current macOS build is an internal development
  artifact, not a notarized or portable external release.

## Active campaign safety

- Treat a running or unresolved campaign directory as immutable except for the
  process that owns it.
- Before assuming a run is stalled, inspect log modification time, container
  state, and CPU activity. A long pressure solve is not evidence of a hang.
- Do not stop, restart, overwrite, compact, or delete an active campaign without
  explicit user authorization and a checked restart/recovery contract.
- Record infrastructure failure separately from scientific gate failure.

## Source and evidence discipline

- Follow `docs/evidence-retention-and-git-boundary.md` and `.gitignore`.
- Normal Git commits contain source, tests, docs, schemas, benchmark definitions,
  audit-tool source, and explicitly allowlisted compact evidence.
- Raw runs and campaigns remain local or in a separately governed archive.
- `benchmarks/tracked-evidence.txt` is the only normal allowlist for evidence
  force-added from ignored run/campaign trees. Never force-add a whole evidence
  directory.
- When a code artifact is named in a decision or evidence record, include its
  current SHA-256. Preserve image tags and immutable image digests separately.
- Do not add a Git remote until its exact private owner/name has been verified.

## Verification

Run from the repository root:

```bash
env PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider server/tests
npm test -- --run
npm run lint
```

Run focused scientific contract tests for any campaign module changed. Run the
production build or packaged-app QA only when the corresponding source or
release surface changed. Report test results separately from scientific and
deployment status.

## Documentation and promotion consistency

- README, API behavior, UI labels, the validated benchmark registry, campaign
  pointer, final assessment, and desktop-release documentation must agree.
- Historical runtime success may remain documented, but must be labelled as
  historical when the current promotion gate is blocked.
- A pointer or label change that broadens a claim requires explicit review of
  the final assessment and all product-contract tests.
