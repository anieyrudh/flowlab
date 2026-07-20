---
title: FlowLab OpenFOAM 11 AMD64 qualification r3
emoji: 🧪
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
private: true
---

Private, nonpromotional Linux/AMD64 image-build recovery for the FlowLab FDA
Re=500 Hugging Face infrastructure qualification. R3 replaces the official
container base only because its UID 98765 cannot be materialized by the
Hugging Face rootless builder. It installs the same OpenFOAM 11 package version
under a builder-safe UID. It is not scientific validation evidence.
