# Independent review — HF AMD64 image recovery r3

Verdict: **pass for the narrow nonpromotional image recovery; no P0 issue**.

The reviewer independently rehashed the frozen contract and source, recovered
the exact private Space commit, rechecked the raw local and remote records and
build log, resolved the commit-prefixed registry tag, reran local image
identity checks, and reinspected both failed r2 Jobs. The evidence supports
only the conclusion that the UID/GID 1000 image hypothesis fixed the rootless
builder failure while retaining the declared OpenFOAM, Gmsh, AMD64, and runner
identities.

The new read-only validator closes the current evidence-rehash gap and rejects
tampered raw records. The runbook records the exact build context. Future Jobs
must bind
`registry.hf.space/anieyrudh-flowlab-openfoam11-gmsh415-amd64-r2` directly to
digest
`sha256:874672331dbaf7107d1f37903b2e0652f451dafaf37bc9471b1149e1579a615a`;
neither `latest` nor `cpu-4c8572a` alone is an execution identity.

HF infrastructure, durable artifact and volume recovery, both coarse pilots,
six-case contract freeze or launch, and every scientific or desktop promotion
remain blocked.
