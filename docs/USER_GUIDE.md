# FlowLab user guide

This guide shows you how to start FlowLab and do useful work with it. It is
written in ASD-STE100 Simplified Technical English.

## 1. What FlowLab does

FlowLab is a desktop application for fluid systems. It has two solvers.

| Solver | Speed | Use it for |
|---|---|---|
| **Instant 1D** | Immediate | Flow rate, pressure loss, Reynolds number, cavitation risk |
| **CFD** | Minutes to hours | Full velocity and pressure fields from OpenFOAM |

The instant solver needs nothing but the application. The CFD solver needs
Docker.

Your project data stays on your computer. FlowLab does not send it anywhere.

## 2. Before you start

| You need | For |
|---|---|
| macOS 13 or later, Apple Silicon | The application |
| Windows 11, x64 | The application |
| Docker Desktop | CFD only. Not needed for instant estimates. |

**Do not install Python.** The application contains its own copy.

## 3. Start the application

1. Open the installer you downloaded.
2. Move FlowLab to your Applications folder.
3. Open FlowLab.

The application starts its own local service. Wait some seconds. The **Backend**
indicator changes to **Online**.

If you build from source, refer to [the installation guide](INSTALLATION.md).

## 4. The four steps

The bar on the left shows four steps. Do them in this sequence.

| Step | What you do |
|---|---|
| **01 Define** | Build the system. Add components and set dimensions. |
| **02 Estimate** | Read the instant 1D results. |
| **03 CFD** | Generate and run a CFD case. |
| **04 Inspect** | Look at the result fields. |

## 5. Do your first estimate

FlowLab opens with an example project.

1. Select **01 Define**.
2. Click a component in the schematic.
3. The **Inspector** opens on the right. Change a dimension, for example the
   pipe diameter.
4. Select **02 Estimate**.

The metrics change immediately. You do not start a solver.

The metrics are:

- **Total flow** in cubic metres each second;
- **Pressure loss** in kilopascals;
- **Max Reynolds**, which tells you if the flow is laminar or turbulent;
- **Cavitation**, which counts the components at risk.

**Note:** a Reynolds number above 4000 is turbulent. FlowLab's accuracy
evidence covers laminar flow only. Refer to section 8.

## 6. Build your own system

1. Select **01 Define**.
2. Click a component in the **Components** list to add it. Start with a
   **Source**.
3. Add a **Pipe**, then add a **Sink**.
4. Drag the ends of a pipe to connect the components.
5. Click each component and set its dimensions in the Inspector.

The **Warnings** tab shows problems, for example a component that is not
connected. Correct all warnings before you continue.

## 7. Run a CFD case

**You must have Docker Desktop open before you start.**

1. Select **03 CFD**.
2. Open the **Inspector** with the button at the top right.
3. Find **Advanced solvers**.
4. Change **Solver** from `Instant 1D` to `OpenFOAM`.
5. Set **Mesh mode** for your geometry:

| Mesh mode | Use it for |
|---|---|
| Planar 2D | A quick check. It is a flat channel, not a round pipe. |
| Axisymmetric (3D pipe) | A round, straight pipe |
| Full 360 O-grid | A round, straight pipe with better wall cells |
| Canonical 90 degree elbow | The example elbow only |

6. Set **Run mode**:

| Run mode | Result |
|---|---|
| Transient (quick starter) | A fast check. It does **not** give a converged pressure drop. |
| Steady (converged) | A converged pressure drop. It takes longer. |

7. Click **Generate and queue experimental CFD case**.

**If the button is grey, the Solver is still set to Instant 1D.** Do step 4
again.

Look at **Run status** for the progress. When the run is complete, select
**04 Inspect**.

## 8. What FlowLab cannot do

Read this before you trust a result.

- **No result is validated against a physical experiment.** All CFD output is
  experimental.
- The accuracy evidence covers **steady, incompressible, laminar** flow only.
- FlowLab does not support turbulence, transient flow, multiphase flow, or
  compressible flow with evidence.
- Complex geometry is limited. FlowLab **fails closed**: if it cannot make an
  honest case, it refuses and tells you why. It does not guess.

Refer to [the benchmark page](BENCHMARKS.md) for the measured accuracy.

## 9. Where your files are

| Item | Location |
|---|---|
| Projects | Where you save them |
| Runs and logs | macOS: `~/Library/Application Support/FlowLab/` |
| | Windows: `%APPDATA%\FlowLab\` |

Use the buttons at the top right to export a project, export results, or import
a project.

## 10. Problems and solutions

| Problem | Solution |
|---|---|
| **Backend** stays Offline | Close FlowLab and open it again. Then look at the log folder in section 9. |
| The CFD button is grey | Change **Solver** to OpenFOAM. Refer to section 7. |
| OpenFOAM shows as missing | Open Docker Desktop, then start FlowLab again. |
| A CFD run fails immediately | Read the message. FlowLab fails closed and gives the cause. |
| No pressure drop after a run | Set **Run mode** to Steady. The transient mode does not converge. |
| macOS refuses to open the application | The installer is not signed yet. Refer to the release notes. |
