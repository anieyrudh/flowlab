import { describe, expect, it } from "vitest";
import type { VtkResultDataset } from "../types";
import {
  datasetBounds,
  datasetFromPreview,
  fieldDescriptiveStats,
  fieldHistogramForValues,
  fieldCoverageForSnapshots,
  fieldStatsForOverlay,
  fieldValuesForSelection,
  inferResultFieldUnit,
  listResultFields,
  parseAsciiVtuResult,
  parseLegacyVtkResult,
  projectDatasetToCanvas,
  sampleDatasetAtCanvasPoint,
  sampleDatasetAtWorldPoint,
  timelineStatsForSnapshots
} from "./vtk";

const fixture = `# vtk DataFile Version 3.0
fixture
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
9
POINT_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
1
2
3
4
VECTORS velocity float
1 0 0
2 0 0
3 0 0
4 0 0
`;

describe("VTK result parsing", () => {
  it("parses legacy ASCII point scalars and vectors", () => {
    const parsed = parseLegacyVtkResult(fixture, "fixture.vtk");

    expect(parsed.sourceName).toBe("fixture.vtk");
    expect(parsed.points).toHaveLength(4);
    expect(parsed.cells).toEqual([[0, 1, 2, 3]]);
    expect(parsed.sourceCellIndices).toBeUndefined();
    expect(parsed.sourceCellCount).toBeUndefined();
    expect(parsed.pointData.scalars.pressure).toEqual([1, 2, 3, 4]);
    expect(parsed.pointData.vectors.velocity[2]).toEqual([3, 0, 0]);
  });

  it("retains original source-cell indices in bounded previews", () => {
    const parsed = datasetFromPreview({
      path: "postProcessing/flowlabNative/time_1.vtk",
      size: 120,
      schema: "flowlab.result_preview.v1",
      sourcePointCount: 12,
      sourceCellCount: 8,
      pointIndices: [0, 1, 2, 3],
      cellIndices: [6],
      sourceCellIdentity: {
        schema: "flowlab.openfoam-source-cell-identity.v1",
        field: "flowlabSourceCellId",
        sourceCellCount: 8,
        unique: true,
        complete: true,
        verified: true
      },
      points: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
      cells: [[0, 1, 2, 3]],
      cellTypes: [9],
      fieldSamples: {
        point: [],
        cell: [{ name: "p", kind: "scalar", values: [101325] }]
      }
    });

    expect(parsed.sourceCellIndices).toEqual([6]);
    expect(parsed.sourceCellCount).toBe(8);
  });

  it("loads explicit source cell identity from a generated VTK field", () => {
    const parsed = parseLegacyVtkResult(`# vtk DataFile Version 3.0
explicit identity
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0
1 0 0
1 1 0
0 1 0
2 0 0
2 1 0
3 0 0
3 1 0
CELLS 2 10
4 0 1 2 3
4 4 5 7 6
CELL_TYPES 2
9
9
CELL_DATA 2
SCALARS flowlabSourceCellId float 1
LOOKUP_TABLE default
1
0
`);

    expect(parsed.sourceCellIndices).toEqual([1, 0]);
    expect(parsed.sourceCellIdentity?.verified).toBe(true);
    expect(parsed.fields).not.toContain("flowlabSourceCellId");
  });

  it("fails closed for unsupported legacy cell types", () => {
    expect(() => parseLegacyVtkResult(fixture.replace("CELL_TYPES 1\n9", "CELL_TYPES 1\n99"))).toThrow(/Unsupported VTK cell types/);
  });

  it("accepts common linear legacy VTK cell types", () => {
    const parsed = parseLegacyVtkResult(`# vtk DataFile Version 3.0
mixed-linear-cells
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0
1 0 0
1 1 0
0 1 0
0 0 1
1 0 1
1 1 1
0 1 1
CELLS 4 22
3 0 1 2
4 0 1 2 4
6 0 1 2 4 5 6
5 0 1 2 3 7
CELL_TYPES 4
5 10 13 14
CELL_DATA 4
SCALARS pressure float 1
LOOKUP_TABLE default
1 2 3 4
`);

    expect(parsed.cellTypes).toEqual([5, 10, 13, 14]);
    expect(parsed.cells).toEqual([
      [0, 1, 2],
      [0, 1, 2, 4],
      [0, 1, 2, 4, 5, 6],
      [0, 1, 2, 3, 7]
    ]);
    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "pressure", min: 1, max: 4, location: "cell" });
  });

  it("parses OpenFOAM legacy POLYDATA patch files", () => {
    const parsed = parseLegacyVtkResult(`# vtk DataFile Version 2.0
inlet
ASCII
DATASET POLYDATA
POINTS 4 float
0 0 0
1 0 0
1 1 0
0 1 0
POLYGONS 1 5
4 0 1 2 3
CELL_DATA 1
FIELD attributes 2
p 1 1 float
101325
U 3 1 float
2 0 0
POINT_DATA 4
FIELD attributes 2
p 1 4 float
101325 101300 101250 101200
U 3 4 float
2 0 0 2.5 0 0 3 0 0 3.5 0 0
`, "VTK/inlet/inlet_50.vtk");

    expect(parsed.format).toBe("legacy-vtk-polydata-ascii-v1");
    expect(parsed.cellTypes).toEqual([7]);
    expect(parsed.cells).toEqual([[0, 1, 2, 3]]);
    expect(parsed.pointData.scalars.p).toEqual([101325, 101300, 101250, 101200]);
    expect(parsed.cellData.vectors.U[0]).toEqual([2, 0, 0]);
    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "p", min: 101200, max: 101325, location: "point" });
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "U", min: 2, max: 3.5, location: "point" });
  });

  it("parses ASCII VTU point fields", () => {
    const parsed = parseAsciiVtuResult(`<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <PointData>
        <DataArray type="Float32" Name="pressure" format="ascii">1 2 3 4</DataArray>
        <DataArray type="Float32" Name="velocity" NumberOfComponents="3" format="ascii">1 0 0 2 0 0 3 0 0 4 0 0</DataArray>
      </PointData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>`);

    expect(parsed.format).toBe("vtu-ascii-v1");
    expect(parsed.fields).toEqual(["pressure", "velocity"]);
    expect(parsed.pointData.scalars.pressure[3]).toBe(4);
  });

  it("accepts common linear VTU cell types", () => {
    const parsed = parseAsciiVtuResult(`<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="8" NumberOfCells="4">
      <CellData>
        <DataArray type="Float32" Name="pressure" format="ascii">4 3 2 1</DataArray>
      </CellData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0 0 0 1 1 0 1 1 1 1 0 1 1</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 0 1 2 4 0 1 2 4 5 6 0 1 2 3 7</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">3 7 13 18</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">5 10 13 14</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>`);

    expect(parsed.cellTypes).toEqual([5, 10, 13, 14]);
    expect(parsed.cells[2]).toEqual([0, 1, 2, 4, 5, 6]);
    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "pressure", min: 1, max: 4, location: "cell" });
  });

  it("computes overlay statistics and samples nearest projected points", () => {
    const parsed = parseLegacyVtkResult(fixture, "fixture.vtk");

    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "pressure", min: 1, max: 4, kind: "scalar" });
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "velocity", min: 1, max: 4, kind: "vector-magnitude" });
    expect(sampleDatasetAtCanvasPoint(parsed, "pressure", { x: 1, y: 1 }, { width: 960, height: 640 })).toMatchObject({
      field: "pressure",
      value: 3,
      pointIndex: 2
    });
  });

  it("builds timeline statistics across loaded result snapshots", () => {
    const first = parseLegacyVtkResult(fixture, "case_1.vtk");
    const second = parseLegacyVtkResult(fixture.replace("4 0 0\n", "40 0 0\n"), "case_2.vtk");
    const missingPinnedField = parseLegacyVtkResult(fixture.replace("VECTORS velocity", "VECTORS U"), "case_3.vtk");

    const velocityTimeline = timelineStatsForSnapshots(
      [
        { id: "one", label: "case_1.vtk", time: 0.001, dataset: first },
        { id: "two", label: "case_2.vtk", time: 0.002, dataset: second }
      ],
      "velocity"
    );

    expect(velocityTimeline).toEqual([
      expect.objectContaining({ id: "one", field: "velocity", location: "point", kind: "vector-magnitude", min: 1, max: 4, mean: 2.5, unit: { symbol: "m/s", label: "velocity" } }),
      expect.objectContaining({ id: "two", field: "velocity", location: "point", kind: "vector-magnitude", min: 1, max: 40, mean: 11.5, unit: { symbol: "m/s", label: "velocity" } })
    ]);

    expect(
      timelineStatsForSnapshots(
        [
          { id: "one", label: "case_1.vtk", time: 0.001, dataset: first },
          { id: "missing", label: "case_3.vtk", time: 0.003, dataset: missingPinnedField }
        ],
        "velocity",
        { field: "velocity", location: "point", kind: "vector" }
      )
    ).toEqual([
      expect.objectContaining({ id: "one", field: "velocity", mean: 2.5 }),
      expect.objectContaining({ id: "missing", field: null, location: null, kind: null, min: null, max: null, mean: null, unit: null })
    ]);

    expect(
      fieldCoverageForSnapshots(
        [
          { id: "one", label: "case_1.vtk", time: 0.001, dataset: first },
          { id: "missing", label: "case_3.vtk", time: 0.003, dataset: missingPinnedField }
        ],
        "velocity",
        { field: "velocity", location: "point", kind: "vector" },
        "x"
      )
    ).toEqual({
      totalSnapshots: 2,
      presentSnapshots: 1,
      missingSnapshots: 1,
      missingLabels: ["case_3.vtk"],
      fields: ["velocity"],
      locations: ["point"],
      kinds: ["vector-x"],
      units: [{ symbol: "m/s", label: "velocity" }]
    });
  });

  it("builds bounded field histograms for analysis tooling", () => {
    expect(fieldHistogramForValues([1, 2, 3, 4], 2)).toEqual([
      { min: 1, max: 2.5, count: 2 },
      { min: 2.5, max: 4, count: 2 }
    ]);
    expect(fieldHistogramForValues([5, 5, Number.NaN, Number.POSITIVE_INFINITY], 12)).toEqual([{ min: 5, max: 5, count: 2 }]);
    expect(fieldHistogramForValues([], 12)).toEqual([]);
    expect(fieldHistogramForValues([1, 2, 3], 99)).toHaveLength(32);
  });

  it("builds descriptive statistics for active field analysis", () => {
    expect(fieldDescriptiveStats([1, 2, 3, 4])).toEqual({
      count: 4,
      min: 1,
      max: 4,
      mean: 2.5,
      stdDev: Math.sqrt(1.25),
      p50: 2.5,
      p95: 3.8499999999999996
    });
    expect(fieldDescriptiveStats([Number.NaN, Number.POSITIVE_INFINITY])).toBeNull();
  });

  it("parses OpenFOAM legacy FIELD attributes and maps p/U to pressure/velocity overlays", () => {
    const parsed = parseLegacyVtkResult(
      `# vtk DataFile Version 2.0
case
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0 1 0 0 1 1 0 0 1 0 0 0 1 1 0 1 1 1 1 0 1 1
CELLS 1 9
8 0 1 2 3 4 5 6 7
CELL_TYPES 1
12
POINT_DATA 8
FIELD attributes 2
p 1 8 float
0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8
U 3 8 float
1 0 0 2 0 0 3 0 0 4 0 0 5 0 0 6 0 0 7 0 0 8 0 0
`,
      "case_50.vtk"
    );

    expect(parsed.fields).toEqual(["U", "p"]);
    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "p", min: 0.1, max: 0.8, kind: "scalar" });
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "U", min: 1, max: 8, kind: "vector-magnitude" });
  });

  it("parses legacy CELL_DATA FIELD attributes and uses them for overlays", () => {
    const parsed = parseLegacyVtkResult(
      `# vtk DataFile Version 2.0
case
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0 1 0 0 1 1 0 0 1 0 0 0 1 1 0 1 1 1 1 0 1 1
CELLS 1 9
8 0 1 2 3 4 5 6 7
CELL_TYPES 1
12
CELL_DATA 1
FIELD attributes 2
p 1 1 float
2.5
U 3 1 float
3 4 0
`,
      "cell.vtk"
    );

    expect(parsed.cellData.scalars.p).toEqual([2.5]);
    expect(parsed.cellData.vectors.U[0]).toEqual([3, 4, 0]);
    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "p", min: 2.5, max: 2.5, location: "cell" });
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "U", min: 5, max: 5, kind: "vector-magnitude", location: "cell" });
    expect(sampleDatasetAtCanvasPoint(parsed, "pressure", { x: 0.5, y: 0.5 }, { width: 10, height: 10 })).toMatchObject({
      field: "p",
      value: 2.5,
      pointIndex: 0,
      location: "cell"
    });
  });

  it("lists duplicate point and cell fields with explicit location and kind", () => {
    const parsed = parseLegacyVtkResult(
      `${fixture}
CELL_DATA 1
FIELD attributes 2
p 1 1 float
8
U 3 1 float
0 6 8
`,
      "mixed.vtk"
    );

    expect(parsed.fields).toEqual(["U", "p", "pressure", "velocity"]);
    expect(listResultFields(parsed)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: "pressure", location: "point", kind: "scalar", tupleCount: 4, overlay: "pressure", unit: { symbol: "Pa", label: "pressure" } }),
        expect.objectContaining({ field: "velocity", location: "point", kind: "vector", tupleCount: 4, overlay: "velocity", unit: { symbol: "m/s", label: "velocity" } }),
        expect.objectContaining({ field: "p", location: "cell", kind: "scalar", tupleCount: 1, overlay: "pressure", unit: { symbol: "Pa", label: "pressure" } }),
        expect.objectContaining({ field: "U", location: "cell", kind: "vector", tupleCount: 1, overlay: "velocity", min: 10, max: 10, unit: { symbol: "m/s", label: "velocity" } })
      ])
    );
    expect(fieldValuesForSelection(parsed, { field: "U", location: "cell", kind: "vector" })).toMatchObject({
      field: "U",
      values: [10],
      kind: "vector-magnitude",
      location: "cell",
      unit: { symbol: "m/s", label: "velocity" }
    });
    expect(fieldValuesForSelection(parsed, { field: "U", location: "cell", kind: "vector" }, "y")).toMatchObject({
      field: "U",
      values: [6],
      kind: "vector-y",
      location: "cell"
    });
    expect(fieldValuesForSelection(parsed, { field: "velocity", location: "point", kind: "vector" }, "x")?.values).toEqual([1, 2, 3, 4]);
    expect(sampleDatasetAtCanvasPoint(parsed, "pressure", { x: 0.5, y: 0.5 }, { width: 10, height: 10 }, { field: "p", location: "cell", kind: "scalar" })).toMatchObject({
      field: "p",
      value: 8,
      location: "cell"
    });
    expect(sampleDatasetAtCanvasPoint(parsed, "pressure", { x: 0.5, y: 0.5 }, { width: 10, height: 10 }, { field: "U", location: "cell", kind: "vector" }, "z")).toMatchObject({
      field: "U",
      value: 8,
      location: "cell",
      unit: { symbol: "m/s", label: "velocity" }
    });
    expect(sampleDatasetAtCanvasPoint(parsed, "pressure", { x: 0.5, y: 0.5 }, { width: 10, height: 10 }, { field: "missing", location: "cell", kind: "scalar" })).toBeNull();
    expect(
      sampleDatasetAtWorldPoint(
        parsed,
        "pressure",
        [0.9, 0.8, 0],
        { field: "p", location: "cell", kind: "scalar" },
        "magnitude",
        0,
        2
      )
    ).toMatchObject({
      field: "p",
      value: 8,
      point: [0.9, 0.8, 0],
      pointIndex: 0,
      location: "cell"
    });
    expect(
      sampleDatasetAtWorldPoint(
        parsed,
        "velocity",
        [0.02, 0.95, 0],
        { field: "velocity", location: "point", kind: "vector" },
        "x",
        0,
        3,
        {
          pointIndices: [0, 1, 3],
          weights: [0.2, 0.3, 0.5]
        }
      )
    ).toMatchObject({
      field: "velocity",
      value: 2.8,
      pointIndex: 3,
      location: "point"
    });
  });

  it("infers conservative units for common CFD result fields", () => {
    expect(inferResultFieldUnit("pressure")).toEqual({ symbol: "Pa", label: "pressure" });
    expect(inferResultFieldUnit("U", "vector")).toEqual({ symbol: "m/s", label: "velocity" });
    expect(inferResultFieldUnit("temperature")).toEqual({ symbol: "K", label: "temperature" });
    expect(inferResultFieldUnit("phase_fraction")).toEqual({ symbol: "1", label: "dimensionless" });
    expect(inferResultFieldUnit("rho")).toEqual({ symbol: "kg/m3", label: "density" });
    expect(inferResultFieldUnit("wallForce")).toEqual({ symbol: "N", label: "force" });
  });

  it("parses ASCII VTU cell fields", () => {
    const parsed = parseAsciiVtuResult(`<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="0.1" byte_order="LittleEndian">
  <UnstructuredGrid>
    <Piece NumberOfPoints="4" NumberOfCells="1">
      <CellData>
        <DataArray type="Float32" Name="Pressure" format="ascii">8</DataArray>
        <DataArray type="Float32" Name="Velocity" NumberOfComponents="3" format="ascii">0 6 8</DataArray>
      </CellData>
      <Points><DataArray type="Float32" NumberOfComponents="3" format="ascii">0 0 0 1 0 0 1 1 0 0 1 0</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">0 1 2 3</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">4</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">9</DataArray>
      </Cells>
    </Piece>
  </UnstructuredGrid>
</VTKFile>`);

    expect(parsed.fields).toEqual(["Pressure", "Velocity"]);
    expect(parsed.cellData.scalars.Pressure).toEqual([8]);
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "Velocity", min: 10, max: 10, location: "cell" });
  });

  it("maps SU2 capitalized VTK fields to pressure, velocity, and temperature overlays", () => {
    const parsed = parseLegacyVtkResult(
      fixture
        .replace("pressure", "Pressure")
        .replace("velocity", "Velocity")
        .replace("VECTORS Velocity float", "SCALARS Temperature float 1\nLOOKUP_TABLE default\n10\n20\n30\n40\nVECTORS Velocity float"),
      "flowlab_su2.vtk"
    );

    expect(fieldStatsForOverlay(parsed, "pressure")).toMatchObject({ field: "Pressure", min: 1, max: 4 });
    expect(fieldStatsForOverlay(parsed, "velocity")).toMatchObject({ field: "Velocity", min: 1, max: 4 });
    expect(fieldStatsForOverlay(parsed, "temperature")).toMatchObject({ field: "Temperature", min: 10, max: 40 });
  });
});

function boundsFixture(points: [number, number, number][]): VtkResultDataset {
  return {
    format: "legacy-vtk-ascii-v1",
    points,
    cells: [],
    cellTypes: [],
    pointData: { scalars: {}, vectors: {} },
    cellData: { scalars: {}, vectors: {} },
    fields: []
  };
}

/**
 * The pre-fix implementation, kept as an oracle. It cannot run on a real mesh
 * — it threw RangeError past ~100k points — but on small inputs the fold that
 * replaced it has to agree exactly.
 */
function spreadBounds(points: [number, number, number][]) {
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const zs = points.map((point) => point[2]);
  const min: [number, number, number] = [Math.min(...xs), Math.min(...ys), Math.min(...zs)];
  const max: [number, number, number] = [Math.max(...xs), Math.max(...ys), Math.max(...zs)];
  return {
    min,
    max,
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2] as [number, number, number],
    span: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1e-9)
  };
}

describe("Dataset bounds", () => {
  it("measures a solver-sized mesh instead of overflowing the stack", () => {
    // 200k is well past the ~100k arguments a spread managed before throwing.
    // Extremes sit mid-array, so the fold has to carry them.
    const count = 200_000;
    const points: [number, number, number][] = new Array(count);
    for (let index = 0; index < count; index += 1) {
      points[index] = [(index % 97) / 97, (index % 89) / 89, (index % 83) / 83];
    }
    points[Math.floor(count / 3)] = [-2, -7, -4];
    points[Math.floor((count * 2) / 3)] = [5, 3, 11];
    const volume = boundsFixture(points);

    expect(() => datasetBounds(volume)).not.toThrow();

    const bounds = datasetBounds(volume);
    expect(bounds.min).toEqual([-2, -7, -4]);
    expect(bounds.max).toEqual([5, 3, 11]);
    expect(bounds.center).toEqual([1.5, -2, 3.5]);
    expect(bounds.span).toBe(15);
  });

  it.each([
    {
      name: "a unit cube",
      points: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]] as [number, number, number][]
    },
    { name: "a single point", points: [[3, -4, 5]] as [number, number, number][] },
    { name: "coincident points", points: [[2, 2, 2], [2, 2, 2]] as [number, number, number][] },
    {
      name: "an off-origin box",
      points: [[-3.5, 10, -0.25], [1.5, -2, 8.75], [0, 4, 3]] as [number, number, number][]
    }
  ])("returns exactly what the spread returned for $name", ({ points }) => {
    expect(datasetBounds(boundsFixture(points))).toEqual(spreadBounds(points));
  });

  it("floors the span, so a flat mesh cannot scale the surface by infinity", () => {
    // Renderers divide by span, so a zero extent would divide by zero.
    expect(datasetBounds(boundsFixture([[2, 2, 2]])).span).toBe(1e-9);
  });

  it("reproduces the spread's empty-dataset answer rather than inventing one", () => {
    const empty = datasetBounds(boundsFixture([]));

    expect(empty).toEqual(spreadBounds([]));
    expect(empty.min).toEqual([Infinity, Infinity, Infinity]);
    expect(empty.max).toEqual([-Infinity, -Infinity, -Infinity]);
    expect(empty.span).toBe(1e-9);
  });
});

describe("Canvas projection", () => {
  it("passes a dataset that already fits through unscaled", () => {
    const projection = projectDatasetToCanvas(boundsFixture([[10, 20, 0], [300, 400, 0]]), 800, 600);

    expect(projection.coordinates([10, 20, 0])).toEqual({ x: 10, y: 20 });
    expect(projection.point(1)).toEqual({ x: 300, y: 400 });
  });

  it("fits an oversized dataset to the padded canvas along its limiting axis", () => {
    const projection = projectDatasetToCanvas(boundsFixture([[0, 0, 0], [2000, 1000, 0]]), 800, 600);
    const near = projection.point(0);
    const far = projection.point(1);

    // x is the tighter axis here, so it fills the padded width exactly ...
    expect(far.x - near.x).toBeCloseTo(800 - 240, 6);
    // ... and y, scaled by the same factor, stays inside the padded height.
    expect(far.y - near.y).toBeLessThan(600 - 240);
  });

  it("stops shrinking at the 0.2 floor rather than vanishing", () => {
    const projection = projectDatasetToCanvas(boundsFixture([[0, 0, 0], [8000, 6000, 0]]), 800, 600);
    const spread = projection.point(1).x - projection.point(0).x;

    // The scale clamp means a hugely oversized mesh overflows the canvas by
    // design instead of collapsing to an unreadable speck.
    expect(spread).toBeCloseTo(8000 * 0.2, 6);
  });

  it("projects a solver-sized mesh instead of overflowing the stack", () => {
    const count = 200_000;
    const points: [number, number, number][] = new Array(count);
    for (let index = 0; index < count; index += 1) points[index] = [index % 500, index % 300, 0];

    expect(() => projectDatasetToCanvas(boundsFixture(points), 800, 600)).not.toThrow();
  });
});
