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
| The solver image | CFD only. Refer to section 9.1. |

**Do not install Python for the packaged application.** It contains its own
copy. To build from source, you need Node.js 24 and CPython 3.12. Refer to
[the installation guide](INSTALLATION.md).

## 3. Start the application

**There is no release to download.** FlowLab has no signed installer and no
published release. Build FlowLab from source. Refer to
[the installation guide](INSTALLATION.md).

**Every build available today is unsigned.** This includes a build you make
yourself and a candidate artifact from continuous integration. Your operating
system shows a warning the first time you open an unsigned build. The warning
is correct: the project has no signing certificate. The application still
runs. Do these steps one time.

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

FlowLab starts its own local service when it opens. Wait a few seconds. To see
the state of that service, click **03 CFD**. The **Run status** panel shows
**Backend** **Online**.

FlowLab opens on the **Laminar Starter Pipe (Experimental)** preset. That
preset is oil in a 20 mm pipe, 2 m long, at a Reynolds number of 200.

## 4. The four steps

The bar on the left shows four steps. Do them in this sequence.

| Step | What you do |
|---|---|
| **01 Define** | Build the system. Add components and set dimensions. |
| **02 Estimate** | Read the instant 1D results. |
| **03 CFD** | Run a CFD case. |
| **04 Inspect** | Look at the result fields. |

Each step shows its own bar above the views. That bar names the job of the step
and holds the one button that moves the work forward.

## 5. The workspace

### 5.1 The two views

The centre of the window holds two linked views. The left view is the
**Schematic**. The right view is the 3D view. A selection in one view also
shows in the other.

The buttons at the top of the centre area control which views you see:

| Button | Result |
|---|---|
| **Schematic** | The schematic only |
| **Split** | Both views. This is the default. |
| **3D view** | The 3D view only |

Drag the divider between the two views to change their widths.

The 3D view shows the diameter of each pipe, the length of each pipe, and the
elevation of each component.

### 5.2 Turn the 3D view

The tools above the 3D view set the camera:

| Button | Result |
|---|---|
| **Iso** | An isometric view |
| **XY** | A plan view, looking down the Z axis |
| **XZ** | A front elevation, looking along the Y axis |
| **YZ** | A side elevation, looking along the X axis |

The button before those four resets the camera.

**Simplified** is not a camera angle. It replaces the 3D graphics with a flat
drawing. Use it for export, or if your machine cannot show 3D graphics. FlowLab
also turns it on without your command if the machine has no 3D graphics, and
then says which of the two happened.

### 5.3 Hide the panels

Click **Hide panels** below the four steps to hide the side panels. Click
**Hide panel** at the end of the tab strip at the bottom to hide the bottom
panel. Both buttons then read **Show panels** and **Show panel**. Use them to
give the width and the height to the two views.

The side panels change with the step:

| Step | Side panels |
|---|---|
| **01 Define** | Components, Project and layers |
| **02 Estimate** | None. **Hide panels** is grey. |
| **03 CFD** | Run status |
| **04 Inspect** | Reference cases, Run status |

### 5.4 The Inspector

The **Inspector** is on the right. Click **Inspector** at the top right to open
it or to close it. It holds these groups. Click a title to open a group or to
close it.

| Group | Content | Shows in |
|---|---|---|
| **Component properties** | The dimensions of the selected component | 01 Define |
| **Solver settings** | Solver, physics, mesh and run controls | 03 CFD |
| **Guided first case** | A comparison of the estimate, the CFD run and the analytic law | All steps |
| **Project preset** | The **Preset** list | All steps |

### 5.5 The reveal groups

Some controls sit behind a title with a chevron. Click the title to open the
group. The title also says how much is behind it.

| Reveal group | Content | Where |
|---|---|---|
| **Advanced mesh tuning** | Boundary layer, quality and adaptation controls | **Mesh controls**, in **Solver settings** |
| **Your own geometry (STL)** | Import of your own surface | **Solver settings** |
| **Solver service and installed solvers** | The state of the local service and of each solver | **Solver settings** |
| **Camera angles** | Numeric yaw, pitch and zoom | **Field viewer** |
| **Seeding and colour** | Streamline seeds and their colour | **Field viewer** |
| **Distribution and coverage** | Result field statistics | **Field viewer** |

A reveal group opens without your command when it holds something you must act
on. **Solver service and installed solvers** opens if the service is offline.
**Your own geometry (STL)** opens if a surface is loaded. You can close a group
again after you read it.

### 5.6 The bottom panel

The tabs at the bottom change with the step:

| Step | Tabs |
|---|---|
| **01 Define** | Metrics, Warnings |
| **02 Estimate** | Sweep, Metrics, Warnings |
| **03 CFD** | Diagnostics, Warnings |
| **04 Inspect** | Field viewer, Diagnostics, Warnings |

The **Warnings** tab shows a count if the model has problems.

## 6. Do your first estimate

FlowLab opens on the laminar example. Its flow is inside the only regime that
FlowLab has accuracy evidence for.

1. Click **02 Estimate**.
2. Read the metrics in the **Metrics** tab at the bottom.

The metrics change immediately. You do not start a solver.

The metrics are:

- **Total flow** in cubic metres per second;
- **Pressure loss** in kilopascals;
- **Max Reynolds**, which tells you if the flow is laminar or turbulent;
- **Cavitation**, which counts the components at risk.

**Max Reynolds** shows 200 for this example. A Reynolds number below 2300 is
laminar. Above 2300, FlowLab marks the value **outside laminar evidence**.

Now change the example:

1. Click **01 Define**.
2. Click the pipe in the schematic. The **Component properties** group of the
   **Inspector** shows its dimensions.
3. Change the diameter to 0.01 m.
4. Click **Estimate this system**.

The metrics change again. The Reynolds number changes with the diameter, the
speed, and the fluid. Look at **Max Reynolds** after each change.

**Warning: FlowLab's accuracy evidence covers laminar flow only. If
Max Reynolds is 2300 or more, the result has no evidence behind it. Refer to
section 11.**

To select a different example, open the **Project preset** group of the
**Inspector**. Then use the **Preset** list. The other presets show more
components. The **Venturi Cavitation Lab** preset runs at a Reynolds number of
more than 3 million. It is a demonstration of the drawing tools. It is not an
accurate result.

## 7. Compare a CFD run with theory

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

### 7.1 Know which law to use

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

### 7.2 Run the case

**You must have Docker Desktop open before you start.**

1. Keep the **Laminar Starter Pipe (Experimental)** preset.
2. Click **03 CFD**.
3. Click **Solver settings**. The **Inspector** opens at the solver group.
4. Keep **Mesh mode** at `Planar 2D (default)`.
5. Keep **Run mode** at `Steady (converged Δp)`.
6. Click **Run CFD case**.
7. Wait for the run progress to show that the run is complete.

You do not set the solver first. **Run CFD case** selects a CFD solver if
**Instant 1D** is still selected, then runs. The line below the button tells you
what the button will do.

The run progress shows the state, the elapsed time, and the latest time or
iteration. When the run stops, FlowLab shows what it produced. Click **View
fields in Inspect** to look at the fields. Click **Open solver diagnostics** to
look at the log.

### 7.3 Read the comparison

Open the **Guided first case** group of the **Inspector**. The panel puts three
values together:

- the instant 1D estimate;
- the CFD pressure drop;
- the analytic pressure drop for your mesh mode.

The panel also shows the error in percent. The error is the difference between
the CFD value and the analytic value.

**Note:** the panel corrects the units for you. FlowLab reports the pressure
drop of an incompressible run as a kinematic value in m2/s2. Multiply that
value by the density to get pascals. Refer to section 13.

Two effects make the CFD value larger than the analytic value:

- **The entrance.** The analytic value is for a fully developed flow. The CFD
  case starts with a flat speed profile at the inlet. The flow needs a length
  to become parabolic. That length adds pressure loss.
- **The mesh.** A coarse mesh gives a less accurate pressure gradient. Find
  **Mesh controls** in the **Solver settings** group. Then set **Resolution**
  to `Fine`.

**Warning: a small error against theory is not experimental validation.
FlowLab compares the solver against a formula. It does not compare the solver
against a physical experiment. Refer to section 11.**

## 8. Build your own system

1. Click **01 Define**.
2. Click **Source** in the **Components** list. This adds a source.
3. Click **Sink** in the same list. This adds a sink.
4. Drag from a port of the source to a port of the sink. This adds a pipe.
5. Click each component. Then set its dimensions in the **Component
   properties** group of the **Inspector**.

The **Components** list holds **Source**, **Pump**, **Venturi**, **Bend**,
**Valve**, **Mixer**, and **Sink**. There is no **Pipe** button. To add a pipe,
drag between two ports.

The **Warnings** tab shows problems, for example a component that is not
connected. Correct all warnings before you continue.

## 9. Run a CFD case

**You must have Docker Desktop open before you start.**

### 9.1 Build the solver image, one time

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

### 9.2 Set up and start the run

1. Click **03 CFD**.
2. Click **Solver settings**. The **Inspector** opens at the solver group.
3. Set **Mesh mode** for your geometry:

   | Mesh mode | Use it for |
   |---|---|
   | Planar 2D (default) | A quick check. It is a flat channel, not a round pipe. |
   | Axisymmetric (3D pipe) | A round, straight pipe |
   | Full 360 O-grid (straight pipe) | A round, straight pipe with better wall cells |
   | Canonical 90° elbow O-grid | The **Canonical 90° Elbow (Experimental)** preset only |

4. Set **Run mode**:

   | Run mode | Result |
   |---|---|
   | Transient (quick starter) | A fast check. It does **not** give a converged pressure drop. |
   | Steady (converged Δp) | A converged pressure drop. It takes longer. |

5. Click **Run CFD case**.

**Mesh mode** and **Run mode** show only if **Physics mode** is
`Incompressible`.

**If the button is grey, the run cannot start.** The line below the button
gives the reason. The causes are a blocking model warning, a local service that
is offline, or a solver runtime that cannot run on this machine. Correct that
cause. Then click the button again.

Look at the run progress for the state of the run. When the run is complete,
click **04 Inspect**.

## 10. Sweep one parameter

The **Sweep** tab of the bottom panel varies one parameter of the project.

1. Click **02 Estimate**.
2. Click the **Sweep** tab.
3. Read the title. It names the parameter it varies, for example
   **Sweep: diameter (mm)**.
4. Read the line below the title. It names the component and the range.
5. Click **Run sweep**.

The sweep uses the instant 1D solver. It does not start a CFD run.

## 11. What FlowLab cannot do

Read this before you trust a result.

- **No result is validated against a physical experiment.** All CFD output is
  experimental.
- The accuracy evidence covers **steady, incompressible, laminar** flow only.
  Laminar means a Reynolds number below 2300.
- FlowLab has no accuracy evidence for turbulence, transient flow, multiphase
  flow, or compressible flow.
- A comparison against a formula is **not** experimental validation. Section 7
  compares the solver against laminar theory only.
- FlowLab supports a limited range of geometry.
- FlowLab **fails closed**. If it cannot make an honest case, it refuses and
  tells you why. It does not guess.

Refer to [the benchmark page](BENCHMARKS.md) for the measured accuracy.

## 12. Where your files are

| Item | Location |
|---|---|
| Projects | Where you save them |
| Runs and logs | macOS: `~/Library/Application Support/FlowLab/` |
| | Windows: `%APPDATA%\FlowLab\` |

Use these buttons at the top right:

| Button | Result |
|---|---|
| **Save** | Saves a copy of the project |
| **Save + results** | Saves the project together with its results |
| **Open** | Opens a project file |

## 13. Problems and solutions

| Problem | Solution |
|---|---|
| **Backend** stays Offline | Close FlowLab and open it again. Then look at the log folder in section 12. |
| **Run CFD case** is grey | Read the line below the button. It gives the reason. Refer to section 9.2. |
| OpenFOAM shows as missing | Open Docker Desktop, then start FlowLab again. |
| A CFD run fails immediately | Read the message. FlowLab fails closed and gives the cause. |
| No pressure drop after a run | Set **Run mode** to Steady. The transient mode does not converge. |
| The CFD pressure drop looks much too small | The **Patch Metrics** panel in **Diagnostics** gives the raw solver value. An incompressible run reports a kinematic pressure in m2/s2. Multiply it by the density to get pascals. The **Guided first case** panel does this for you. |
| The CFD pressure drop is near 3 times the analytic value | You used Hagen-Poiseuille with the Planar 2D mesh. Planar 2D is a flat channel. Use plane-Poiseuille. Refer to section 7. |
| The 3D view shows a concept drawing, not a mesh | FlowLab shows **Not this case's mesh** and gives the reason. Planar 2D has no 3D preview. Set **Mesh mode** to a 3D mode in **Solver settings**. |
| macOS or Windows stops the application | The build is not signed. Refer to section 3 for the one-time steps. |
| OpenFOAM shows as not available | Build the solver image. Refer to section 9.1. |
| A control you used before is not there | Look behind a reveal group. Refer to section 5.5. |
