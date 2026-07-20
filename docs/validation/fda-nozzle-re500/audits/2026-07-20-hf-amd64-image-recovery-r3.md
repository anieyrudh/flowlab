# Hugging Face AMD64 image recovery r3

Status: **passed, nonpromotional image recovery only**.

The prospective R3 hypothesis replaced the official OpenFOAM container base,
whose UID/GID 98765 the Hugging Face rootless builder could not materialize,
with immutable Ubuntu 20.04 AMD64 plus the exact OpenFOAM 11 package version
`20240612`. It retained Gmsh 4.15.2 and the frozen R2 runner.

The local image passed Linux/AMD64, UID/GID 1000, package, `foamVersion`, Gmsh,
and embedded-runner checks. Its OCI digest is
`sha256:3f0566e6e2f471789088631f93623315f608299fda3868bfb2c0a925ef1b4517`.

The exact sources were recovered and rehashed from private Space commit
`4c8572ab13354f21bb8d5b182b01be99a6a49c62`. Hugging Face built, pushed, and
started it successfully. The commit-prefixed registry tag `cpu-4c8572a`
resolves to
`sha256:874672331dbaf7107d1f37903b2e0652f451dafaf37bc9471b1149e1579a615a`.
The mutable `latest` tag was observed to match but is not evidence.

This does not qualify Hugging Face Jobs, volumes, artifact recovery, either
coarse pilot, or scientific execution. No solver ran. A six-case execution
contract still may not be frozen or launched.
