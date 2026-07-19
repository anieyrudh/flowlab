# OpenFOAM 11 / Gmsh 4.15.2 ARM64 functional runtime

This Dockerfile builds a native `linux/arm64` runtime for local functional
meshing and exact-initialization evidence.  It is not a substitute for the
pinned AMD64 image or for native-AMD64 timing evidence.

It pins the Ubuntu ARM64 base manifest and SHA-256 hashes of the official
OpenFOAM 11 and Gmsh 4.15.2 source archives.  A run must record the built
image ID and architecture in its campaign report.
