# FlowLab user guide

This guide shows you how to start FlowLab and do useful work with it. It is
written in ASD-STE100 Simplified Technical English.

## 1. What FlowLab does

FlowLab is a desktop application for fluid systems. It has two solvers.

| Solver | Speed | Gives you |
|---|---|---|
| **Instant 1D** | Immediate | Flow rate, pressure loss, Reynolds number, cavitation risk |
| **CFD** | Minutes to hours | Full velocity and pressure fields from OpenFOAM |

The instant solver needs nothing but the application. The CFD solver needs
Docker Desktop.

Your project data stays on your computer. FlowLab does not send it anywhere.

## 2. Before you start

| You need | For |
|---|---|
| macOS 13 or later, Apple Silicon | The application |
| Windows 11, x64 | The application |
| Docker Desktop | CFD only. Not needed for instant estimates. |
| The solver image | CFD only. Refer to section 8. |

**Do not install Python for the packaged application.** It contains its own
copy. To build from source, you need Node.js 24 and CPython 3.12. Refer to
[the installation guide](INSTALLATION.md).

## 3. Start the application

Download FlowLab from the
[releases page](https://github.com/anieyrudh/flowlab/releases).

**The application is not signed.** Your operating system shows a warning the
first time you open it. The warning is correct: the project has no signing
certificate. The application still runs. Do these steps one time.

**On macOS**

1. Move FlowLab to your Applications folder.
2. Open FlowLab. macOS stops it and shows a warning.
3. Open **System Settings**.
4. Click **Privacy & Security**.
5. Find the message about FlowLab. Then click **Open Anyway**.
6. Open FlowLab again. Then click **Open**.

**On Windows**

1. Open the file you downloaded. Windows shows "Windows protected your PC".
2. Click **More info**.
3. Click **Run anyway**.

FlowLab starts its own local service when it opens. Wait a few seconds. The
**Backend** indicator changes to **Online**.

To build from source instead, refer to
[the installation guide](INSTALLATION.md).

## 4. The four steps

The bar on the left shows four steps. Do them in this sequence.

| Step | What you do |
|---|---|
| **01 Define** | Build the system. Add components and set dimensions. |
| **02 Estimate** | Read the instant 1D results. |
| **03 CFD** | Generate and run a CFD case. |
| **04 Inspect** | Look at the result fields. |

## 5. Do your first estimate

Start with the laminar example. Its flow is inside the only regime that FlowLab
has accuracy evidence for.

1. Find **Preset** at the top of the **Inspector**.
2. Select **Laminar Starter Pipe (Experimental)**.
3. Click **02 Estimate**.

The metrics change immediately. You do not start a solver.

The metrics are:

- **Total flow** in cubic metres per second;
- **Pressure loss** in kilopascals;
- **Max Reynolds**, which tells you if the flow is laminar or turbulent;
- **Cavitation**, which counts the components at risk.

**Max Reynolds** shows 200 for this example. A Reynolds number below 2300 is
laminar.

Now change the example:

1. Click **01 Define**.
2. Click the pipe in the schematic. The **Inspector** shows its dimensions.
3. Change the diameter to 0.01 m.
4. Click **02 Estimate**.

The metrics change again. The Reynolds number changes with the diameter, the
speed, and the fluid. Look at **Max Reynolds** after each change.

**Warning: FlowLab's accuracy evidence covers laminar flow only. If
Max Reynolds is 2300 or more, the result has no evidence behind it. Refer to
section 9.**

The other presets show more components. The **Venturi Cavitation Lab** preset
runs at a Reynolds number of more than 3 million. It is a demonstration of the
drawing tools. It is not an accurate result.

## 6. Compare a CFD run with theory

This procedure shows you how near a FlowLab run is to the textbook answer. Do
it one time. Then you know what a FlowLab number is worth.

The example uses these values:

| Item | Value |
|---|---|
| Fluid | Light mineral oil at 20 degrees Celsius |
| Density | 870 kg/m3 |
| Dynamic viscosity | 0.087 Pa s |
| Diameter | 0.02 m |
| Length | 2.0 m |
| Mean speed | 1.0 m/s |
| Reynolds number | 200 |

### 6.1 Know which law to use

**Warning: the two laminar laws are not the same. Use the law that agrees with
your mesh mode. If you use the other law, your error is more than 100%.**

| Mesh mode | Shape it builds | Law | Formula |
|---|---|---|---|
| Planar 2D (default) | A flat channel of gap H | Plane-Poiseuille | `12 * mu * U * L / H^2` |
| Axisymmetric (3D pipe) | A round pipe of diameter D | Hagen-Poiseuille | `32 * mu * U * L / D^2` |
| Full 360 O-grid (straight pipe) | A round pipe of diameter D | Hagen-Poiseuille | `32 * mu * U * L / D^2` |

Planar 2D is the default mesh mode. It is a flat channel that is one cell
thick. It is **not** a round pipe. For a circular component, FlowLab makes the
gap H equal to the diameter D.

For the example, H and D are both 0.02 m. Thus:

- plane-Poiseuille gives `12 * 0.087 * 1.0 * 2.0 / 0.02^2` = 5220 Pa;
- Hagen-Poiseuille gives `32 * 0.087 * 1.0 * 2.0 / 0.02^2` = 13920 Pa.

The two values differ by the factor 32/12.

In the formulas:

- `mu` is the dynamic viscosity in Pa s;
- `U` is the mean speed in m/s;
- `L` is the pipe length in m;
- `H` is the channel gap in m;
- `D` is the pipe diameter in m.

### 6.2 Run the case

**You must have Docker Desktop open before you start.**

1. Select **Laminar Starter Pipe (Experimental)** in the **Preset** list.
2. Click **03 CFD**.
3. Set **Solver** to `OpenFOAM`.
4. Keep **Mesh mode** at `Planar 2D (default)`.
5. Keep **Run mode** at `Steady (converged Δp)`.
6. Click **Generate and queue experimental CFD case**.
7. Wait for **Run status** to show that the run is complete.

### 6.3 Read the comparison

The **Guided first case** panel puts three values together:

- the instant 1D estimate;
- the CFD pressure drop;
- the analytic pressure drop for your mesh mode.

The panel also shows the error in percent. The error is the difference between
the CFD value and the analytic value.

**Note:** the panel corrects the units for you. FlowLab reports the pressure
drop of an incompressible run as a kinematic value in m2/s2. Multiply that
value by the density to get pascals. Refer to section 11.

Two effects make the CFD value larger than the analytic value:

- **The entrance.** The analytic value is for a fully developed flow. The CFD
  case starts with a flat speed profile at the inlet. The flow needs a length
  to become parabolic. That length adds pressure loss.
- **The mesh.** A coarse mesh gives a less accurate pressure gradient. Find
  **Mesh controls** in the Inspector. Then set **Resolution** to `Fine`.

**Warning: a small error against theory is not experimental validation.
FlowLab compares the solver against a formula. It does not compare the solver
against a physical experiment. Refer to section 9.**

## 7. Build your own system

1. Click **01 Define**.
2. Click **Source** in the **Components** list. This adds a source.
3. Click **Pipe** in the same list.
4. Click **Sink** in the same list.
5. Drag the ends of the pipe to connect the components.
6. Click each component. Then set its dimensions in the Inspector.

The **Warnings** tab shows problems, for example a component that is not
connected. Correct all warnings before you continue.

## 8. Run a CFD case

**You must have Docker Desktop open before you start.**

### 8.1 Build the solver image, one time

FlowLab runs OpenFOAM in a container. Build that container one time. Get the
source first, then run this command in the source folder:

```bash
docker build -t flowlab/openfoam11-gmsh:2026-07-13 docker/openfoam11-gmsh
```

The image is the OpenFOAM Foundation image with `gmsh` added. Its base is
pinned by digest, so the build gives the same image each time. The download is
approximately 3 GB, and the build takes some minutes.

The image is built for x86-64. On Apple Silicon it runs through emulation.
The results are the same, but the run is slower.

If the image is absent, FlowLab shows OpenFOAM as not available and gives the
reason. It does not fail quietly.

### 8.2 Set up and start the run

1. Click **03 CFD**.
2. Open the **Inspector** with the button at the top right.
3. Find **Advanced solvers**.
4. Set **Solver** to `OpenFOAM`. The default is `Instant 1D`.
5. Set **Mesh mode** for your geometry:

   | Mesh mode | Use it for |
   |---|---|
   | Planar 2D (default) | A quick check. It is a flat channel, not a round pipe. |
   | Axisymmetric (3D pipe) | A round, straight pipe |
   | Full 360 O-grid (straight pipe) | A round, straight pipe with better wall cells |
   | Canonical 90° elbow O-grid | The example elbow only |

6. Set **Run mode**:

   | Run mode | Result |
   |---|---|
   | Transient (quick starter) | A fast check. It does **not** give a converged pressure drop. |
   | Steady (converged Δp) | A converged pressure drop. It takes longer. |

7. Click **Generate and queue experimental CFD case**.

**If the button is grey, the Solver is still set to Instant 1D.** Do step 4
again.

Look at **Run status** for the progress. When the run is complete, click
**04 Inspect**.

## 9. What FlowLab cannot do

Read this before you trust a result.

- **No result is validated against a physical experiment.** All CFD output is
  experimental.
- The accuracy evidence covers **steady, incompressible, laminar** flow only.
  Laminar means a Reynolds number below 2300.
- FlowLab has no accuracy evidence for turbulence, transient flow, multiphase
  flow, or compressible flow.
- A comparison against a formula is **not** experimental validation. Section 6
  compares the solver against laminar theory only.
- FlowLab supports a limited range of geometry.
- FlowLab **fails closed**. If it cannot make an honest case, it refuses and
  tells you why. It does not guess.

Refer to [the benchmark page](BENCHMARKS.md) for the measured accuracy.

## 10. Where your files are

| Item | Location |
|---|---|
| Projects | Where you save them |
| Runs and logs | macOS: `~/Library/Application Support/FlowLab/` |
| | Windows: `%APPDATA%\FlowLab\` |

Use the buttons at the top right to export a project, export results, or import
a project.

## 11. Problems and solutions

| Problem | Solution |
|---|---|
| **Backend** stays Offline | Close FlowLab and open it again. Then look at the log folder in section 10. |
| The CFD button is grey | Set **Solver** to OpenFOAM. Refer to section 8. |
| OpenFOAM shows as missing | Open Docker Desktop, then start FlowLab again. |
| A CFD run fails immediately | Read the message. FlowLab fails closed and gives the cause. |
| No pressure drop after a run | Set **Run mode** to Steady. The transient mode does not converge. |
| The CFD pressure drop looks much too small | The **Patch Metrics** panel gives the raw solver value. An incompressible run reports a kinematic pressure in m2/s2. Multiply it by the density to get pascals. The **Guided first case** panel does this for you. |
| The CFD pressure drop is near 3 times the analytic value | You used Hagen-Poiseuille with the Planar 2D mesh. Planar 2D is a flat channel. Use plane-Poiseuille. Refer to section 6. |
| macOS or Windows stops the application | The build is not signed. Refer to section 3 for the one-time steps. |
| OpenFOAM shows as not available | Build the solver image. Refer to section 8.1. |
