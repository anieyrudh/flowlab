# FDA Hugging Face infrastructure qualification attempt r1

Status: **blocked before any solver execution**.

The `cpu-basic` architecture probe reached Hugging Face Jobs and reported
`amd64`. It then failed after three running seconds with HTTP 403 while trying
to create the prospectively named private artifact dataset. The connected token
can launch Jobs but cannot create that dataset under `Anieyrudh`. No solver ran
and no remote artifact was written.

The equivalent local image did build and pass its identity probe:

- Linux `amd64`, OpenFOAM 11, Gmsh 4.15.2;
- OCI digest `sha256:c95a0e413cb06422c6e5b4c8810b87279131944b5dc2d1ddcf65d2fb0dfe93bb`;
- runner SHA-256 `8ce992bc4011be4b5b41f8cb7560a5be4fe211dbe91d50cc875d0946a3b1004d`.

The mesh-only local preparation also completed at 44,256 cells. Its immutable
input archive SHA-256 is
`caf7e5e8105cd10fce7cdffb5165900106251cd095f31c1b91cd3a07921899c9`.

The fail-closed boundary worked: without durable write permission, the
revision-pinned volume probe, private Space publication, `cpu-upgrade` and
`cpu-xl` coarse pilots, artifact recovery, and numerical comparison were not
started. HF infrastructure is not qualified, and no six-case execution
contract may be frozen from this attempt.

Required remediation: reconnect the Hugging Face integration with permission to
create and write private dataset and private Space repositories under
`Anieyrudh`, while retaining Jobs access. A resumed qualification must use a new
unique Job ID and must not overwrite this evidence.
