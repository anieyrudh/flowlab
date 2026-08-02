import type { JobArtifactPreview, VtkResultDataset } from "../types";
import { maximumOf, minimumOf, pointExtent } from "../numeric";

const VTK_POLYGON = 7;
const supportedCellTypes = new Set([5, VTK_POLYGON, 9, 10, 12, 13, 14]);
const SOURCE_CELL_ID_FIELD = "flowlabSourceCellId";

export type ResultFieldLocation = "point" | "cell";
export type ResultFieldKind = "scalar" | "vector";
export type ResultFieldSelection = {
  field: string;
  location: ResultFieldLocation;
  kind: ResultFieldKind;
};
export type ResultVectorComponent = "magnitude" | "x" | "y" | "z";
export type ResultFieldValueKind = "scalar" | "vector-magnitude" | "vector-x" | "vector-y" | "vector-z";
export type ResultFieldUnit = {
  symbol: string;
  label: string;
};

export type ResultFieldInventoryItem = ResultFieldSelection & {
  tupleCount: number;
  min: number;
  max: number;
  overlay: string | null;
  unit: ResultFieldUnit;
};

export type ResultFieldTimelineSample = {
  id: string;
  label: string;
  time: number;
  field: string | null;
  location: ResultFieldLocation | null;
  kind: ResultFieldValueKind | null;
  min: number | null;
  max: number | null;
  mean: number | null;
  unit: ResultFieldUnit | null;
};

export type ResultFieldCoverage = {
  totalSnapshots: number;
  presentSnapshots: number;
  missingSnapshots: number;
  missingLabels: string[];
  fields: string[];
  locations: ResultFieldLocation[];
  kinds: ResultFieldValueKind[];
  units: ResultFieldUnit[];
};

export type ResultFieldHistogramBin = {
  min: number;
  max: number;
  count: number;
};

export type ResultFieldDescriptiveStats = {
  count: number;
  min: number;
  max: number;
  mean: number;
  stdDev: number;
  p50: number;
  p95: number;
};

const solverUnit: ResultFieldUnit = { symbol: "solver units", label: "solver-native units" };
const unitless: ResultFieldUnit = { symbol: "1", label: "dimensionless" };

export function inferResultFieldUnit(field: string, kind: ResultFieldKind | ResultFieldValueKind = "scalar", overlay: string | null = overlayForField(field)): ResultFieldUnit {
  const normalized = field.toLowerCase();
  if (overlay === "pressure" || ["p", "p_rgh", "pressure", "static_pressure", "total_pressure"].includes(normalized)) return { symbol: "Pa", label: "pressure" };
  if (overlay === "velocity" || ["u", "velocity", "vel"].includes(normalized)) return { symbol: "m/s", label: "velocity" };
  if (overlay === "temperature" || ["t", "temperature", "temp"].includes(normalized)) return { symbol: "K", label: "temperature" };
  if (overlay === "phase" || normalized.startsWith("alpha") || normalized.includes("fraction")) return unitless;
  if (overlay === "residuals" || normalized.includes("residual")) return unitless;
  if (normalized === "rho" || normalized.includes("density")) return { symbol: "kg/m3", label: "density" };
  if (normalized.includes("mach")) return unitless;
  if (normalized.includes("force")) return { symbol: "N", label: "force" };
  if (normalized.includes("moment")) return { symbol: "N m", label: "moment" };
  if (normalized.includes("power")) return { symbol: "W", label: "power" };
  if (normalized.includes("heatflux") || normalized.includes("heat_flux")) return { symbol: "W/m2", label: "heat flux" };
  if (normalized.endsWith("id") || normalized.includes("cellid") || normalized.includes("patchid")) return { symbol: "id", label: "identifier" };
  if (kind !== "scalar") return solverUnit;
  return solverUnit;
}

function vectorMagnitude(vector: [number, number, number]) {
  return Math.hypot(vector[0], vector[1], vector[2]);
}

function vectorComponentValue(vector: [number, number, number], component: ResultVectorComponent) {
  if (component === "x") return vector[0];
  if (component === "y") return vector[1];
  if (component === "z") return vector[2];
  return vectorMagnitude(vector);
}

export function vectorComponentKind(component: ResultVectorComponent): ResultFieldValueKind {
  return component === "magnitude" ? "vector-magnitude" : `vector-${component}`;
}

export function formatFieldValueKind(kind: ResultFieldValueKind) {
  if (kind === "vector-magnitude") return "magnitude";
  if (kind === "vector-x") return "x component";
  if (kind === "vector-y") return "y component";
  if (kind === "vector-z") return "z component";
  return "scalar";
}

export function parseVtkResult(text: string, sourceName?: string): VtkResultDataset {
  if (text.trimStart().startsWith("<")) return parseAsciiVtuResult(text, sourceName);
  return parseLegacyVtkResult(text, sourceName);
}

function explicitSourceCellIdentity(cellScalars: Record<string, number[]>, cellCount: number) {
  const raw = cellScalars[SOURCE_CELL_ID_FIELD];
  if (!raw) return null;
  delete cellScalars[SOURCE_CELL_ID_FIELD];
  if (
    raw.length !== cellCount
    || raw.some((value) => !Number.isFinite(value) || !Number.isInteger(value) || value < 0)
    || new Set(raw).size !== cellCount
    || [...raw].sort((left, right) => left - right).some((value, index) => value !== index)
  ) {
    throw new Error(`${SOURCE_CELL_ID_FIELD} must be a unique complete source-cell permutation.`);
  }
  return {
    sourceCellIndices: raw,
    sourceCellCount: cellCount,
    sourceCellIdentity: {
      schema: "flowlab.openfoam-source-cell-identity.v1" as const,
      field: SOURCE_CELL_ID_FIELD as "flowlabSourceCellId",
      sourceCellCount: cellCount,
      unique: true as const,
      complete: true as const,
      verified: true as const
    }
  };
}

export function datasetFromPreview(preview: JobArtifactPreview, sourceName = preview.path): VtkResultDataset {
  if (preview.skipped) throw new Error(`Preview skipped: ${preview.skipped}`);
  if (preview.schema !== "flowlab.result_preview.v1") throw new Error("Unsupported result preview schema.");
  if (!preview.points || !preview.cells || !preview.cellTypes || !preview.fieldSamples) {
    throw new Error("Result preview is missing geometry or field samples.");
  }
  const hasExplicitIdentity = preview.sourceCellIdentity?.verified === true;
  if (
    hasExplicitIdentity
    && (
      !preview.cellIndices
      || preview.cellIndices.length !== preview.cells.length
      || !Number.isInteger(preview.sourceCellCount)
      || (preview.sourceCellCount ?? 0) < preview.cells.length
    )
  ) throw new Error("Result preview has an incomplete explicit source-cell identity.");
  const pointScalars: Record<string, number[]> = {};
  const pointVectors: Record<string, [number, number, number][]> = {};
  const cellScalars: Record<string, number[]> = {};
  const cellVectors: Record<string, [number, number, number][]> = {};

  for (const sample of preview.fieldSamples.point) {
    if (sample.kind === "scalar") {
      pointScalars[sample.name] = sample.values;
    } else {
      pointVectors[sample.name] = sample.values;
    }
  }
  for (const sample of preview.fieldSamples.cell) {
    if (sample.kind === "scalar") {
      cellScalars[sample.name] = sample.values;
    } else {
      cellVectors[sample.name] = sample.values;
    }
  }

  return {
    format: preview.format ?? "legacy-vtk-ascii-v1",
    points: preview.points,
    cells: preview.cells,
    cellTypes: preview.cellTypes,
    pointData: { scalars: pointScalars, vectors: pointVectors },
    cellData: { scalars: cellScalars, vectors: cellVectors },
    fields: Array.from(new Set([...Object.keys(pointScalars), ...Object.keys(pointVectors), ...Object.keys(cellScalars), ...Object.keys(cellVectors)])).sort(),
    ...(hasExplicitIdentity
      ? {
          sourceCellIndices: [...(preview.cellIndices ?? [])],
          sourceCellCount: preview.sourceCellCount,
          sourceCellIdentity: preview.sourceCellIdentity ?? undefined
        }
      : {}),
    sourceName,
    sourceText: undefined
  };
}

export function parseLegacyVtkResult(text: string, sourceName?: string): VtkResultDataset {
  const tokens = text.replace(/\r/g, " ").split(/\s+/).filter(Boolean);
  if (tokens.length < 8 || tokens[0] !== "#" || tokens[1]?.toLowerCase() !== "vtk") {
    throw new Error("Only ASCII legacy VTK result files are supported.");
  }
  if (tokens.slice(0, 20).includes("BINARY")) {
    throw new Error("Binary VTK result files are not supported.");
  }
  const datasetIndex = tokens.indexOf("DATASET");
  if (datasetIndex < 0) throw new Error("VTK DATASET declaration is missing.");
  const datasetType = tokens[datasetIndex + 1];
  if (datasetType !== "UNSTRUCTURED_GRID" && datasetType !== "POLYDATA") {
    throw new Error(`Unsupported VTK dataset type: ${datasetType}`);
  }

  const pointsIndex = tokens.indexOf("POINTS");
  if (pointsIndex < 0) throw new Error("VTK POINTS section is missing.");
  const pointCount = Number(tokens[pointsIndex + 1]);
  let cursor = pointsIndex + 3;
  const points: VtkResultDataset["points"] = [];
  for (let index = 0; index < pointCount; index += 1) {
    points.push([Number(tokens[cursor]), Number(tokens[cursor + 1]), Number(tokens[cursor + 2])]);
    cursor += 3;
  }

  const cellSection = datasetType === "POLYDATA" ? "POLYGONS" : "CELLS";
  const cellsIndex = tokens.indexOf(cellSection, cursor);
  if (cellsIndex < 0) throw new Error(`VTK ${cellSection} section is missing.`);
  const cellCount = Number(tokens[cellsIndex + 1]);
  cursor = cellsIndex + 3;
  const cells: number[][] = [];
  for (let index = 0; index < cellCount; index += 1) {
    const size = Number(tokens[cursor]);
    cursor += 1;
    cells.push(tokens.slice(cursor, cursor + size).map(Number));
    cursor += size;
  }
  validateCells(cells, pointCount);

  const cellTypes: number[] = [];
  if (datasetType === "POLYDATA") {
    cellTypes.push(...Array.from({ length: cellCount }, () => VTK_POLYGON));
  } else {
    const cellTypesIndex = tokens.indexOf("CELL_TYPES", cursor);
    if (cellTypesIndex >= 0) {
    const typeCount = Number(tokens[cellTypesIndex + 1]);
    if (typeCount !== cellCount) throw new Error("CELL_TYPES count must match CELLS count.");
    cursor = cellTypesIndex + 2;
    cellTypes.push(...tokens.slice(cursor, cursor + typeCount).map(Number));
    const unsupported = cellTypes.filter((type) => !supportedCellTypes.has(type));
    if (unsupported.length) throw new Error(`Unsupported VTK cell types: ${Array.from(new Set(unsupported)).join(", ")}`);
    cursor += typeCount;
    } else {
      throw new Error("VTK CELL_TYPES section is missing.");
    }
  }

  const scalars: Record<string, number[]> = {};
  const vectors: Record<string, [number, number, number][]> = {};
  const cellScalars: Record<string, number[]> = {};
  const cellVectors: Record<string, [number, number, number][]> = {};
  while (cursor < tokens.length) {
    const section = tokens[cursor];
    if (section === "POINT_DATA") {
      const dataCount = Number(tokens[cursor + 1]);
      if (dataCount !== pointCount) throw new Error("POINT_DATA count must match POINTS count.");
      cursor = parseLegacyDataArrays(tokens, cursor + 2, pointCount, "POINT_DATA", scalars, vectors);
    } else if (section === "CELL_DATA") {
      const dataCount = Number(tokens[cursor + 1]);
      if (dataCount !== cellCount) throw new Error("CELL_DATA count must match CELLS count.");
      cursor = parseLegacyDataArrays(tokens, cursor + 2, cellCount, "CELL_DATA", cellScalars, cellVectors);
    } else {
      throw new Error(`Unsupported VTK data section: ${section}`);
    }
  }
  const sourceIdentity = explicitSourceCellIdentity(cellScalars, cellCount);

  return {
    format: datasetType === "POLYDATA" ? "legacy-vtk-polydata-ascii-v1" : "legacy-vtk-ascii-v1",
    points,
    cells,
    cellTypes,
    pointData: { scalars, vectors },
    cellData: { scalars: cellScalars, vectors: cellVectors },
    fields: Array.from(new Set([...Object.keys(scalars), ...Object.keys(vectors), ...Object.keys(cellScalars), ...Object.keys(cellVectors)])).sort(),
    ...(sourceIdentity ?? {}),
    sourceName,
    sourceText: text
  };
}

export function parseAsciiVtuResult(text: string, sourceName?: string): VtkResultDataset {
  const document = new DOMParser().parseFromString(text, "application/xml");
  const parserError = document.querySelector("parsererror");
  if (parserError) throw new Error("Invalid VTU XML.");
  const root = document.documentElement;
  if (root.tagName !== "VTKFile" || root.getAttribute("type") !== "UnstructuredGrid") {
    throw new Error("Only VTK XML UnstructuredGrid (.vtu) result files are supported.");
  }
  const piece = root.querySelector("UnstructuredGrid > Piece");
  if (!piece) throw new Error("VTU Piece is missing.");
  const declaredPoints = Number(piece.getAttribute("NumberOfPoints") ?? 0);
  const declaredCells = Number(piece.getAttribute("NumberOfCells") ?? 0);

  const pointsArray = piece.querySelector("Points > DataArray");
  if (!pointsArray || (pointsArray.getAttribute("format") ?? "ascii") !== "ascii") {
    throw new Error("VTU parser only supports ASCII point DataArray values.");
  }
  if (Number(pointsArray.getAttribute("NumberOfComponents") ?? 3) !== 3) {
    throw new Error("VTU points must have three components.");
  }
  const pointTokens = numbers(pointsArray.textContent ?? "");
  if (pointTokens.length !== declaredPoints * 3) {
    throw new Error("VTU point count does not match NumberOfPoints.");
  }
  const points: VtkResultDataset["points"] = [];
  for (let index = 0; index < pointTokens.length; index += 3) {
    points.push([pointTokens[index], pointTokens[index + 1], pointTokens[index + 2]]);
  }

  const cellsNode = piece.querySelector("Cells");
  if (!cellsNode) throw new Error("VTU Cells section is missing.");
  const connectivity = namedArray(cellsNode, "connectivity").map(Number);
  const offsets = namedArray(cellsNode, "offsets").map(Number);
  const cellTypes = namedArray(cellsNode, "types").map(Number);
  if (offsets.length !== declaredCells || cellTypes.length !== declaredCells) {
    throw new Error("VTU cell arrays do not match NumberOfCells.");
  }
  const unsupported = cellTypes.filter((type) => !supportedCellTypes.has(type));
  if (unsupported.length) throw new Error(`Unsupported VTU cell types: ${Array.from(new Set(unsupported)).join(", ")}`);
  const cells: number[][] = [];
  let previous = 0;
  for (const offset of offsets) {
    if (offset <= previous || offset > connectivity.length) throw new Error("VTU cell offsets are invalid.");
    const cell = connectivity.slice(previous, offset);
    if (cell.some((index) => index < 0 || index >= declaredPoints)) throw new Error("VTU cell connectivity is out of range.");
    cells.push(cell);
    previous = offset;
  }
  if (previous !== connectivity.length) throw new Error("VTU final cell offset does not consume connectivity.");

  const scalars: Record<string, number[]> = {};
  const vectors: Record<string, [number, number, number][]> = {};
  const pointData = piece.querySelector("PointData");
  if (pointData) {
    for (const array of Array.from(pointData.querySelectorAll("DataArray"))) {
      if ((array.getAttribute("format") ?? "ascii") !== "ascii") throw new Error("VTU parser only supports ASCII PointData arrays.");
      const name = array.getAttribute("Name");
      if (!name) throw new Error("VTU PointData arrays must have a Name.");
      const values = numbers(array.textContent ?? "");
      const components = Number(array.getAttribute("NumberOfComponents") ?? 1);
      if (components === 1) {
        if (values.length !== declaredPoints) throw new Error(`VTU scalar ${name} count does not match NumberOfPoints.`);
        scalars[name] = values;
      } else if (components === 3) {
        if (values.length !== declaredPoints * 3) throw new Error(`VTU vector ${name} count does not match NumberOfPoints.`);
        vectors[name] = [];
        for (let index = 0; index < values.length; index += 3) {
          vectors[name].push([values[index], values[index + 1], values[index + 2]]);
        }
      } else {
        throw new Error(`Unsupported VTU PointData component count for ${name}: ${components}`);
      }
    }
  }

  const cellScalars: Record<string, number[]> = {};
  const cellVectors: Record<string, [number, number, number][]> = {};
  const cellData = piece.querySelector("CellData");
  if (cellData) {
    for (const array of Array.from(cellData.querySelectorAll("DataArray"))) {
      if ((array.getAttribute("format") ?? "ascii") !== "ascii") throw new Error("VTU parser only supports ASCII CellData arrays.");
      const name = array.getAttribute("Name");
      if (!name) throw new Error("VTU CellData arrays must have a Name.");
      const values = numbers(array.textContent ?? "");
      const components = Number(array.getAttribute("NumberOfComponents") ?? 1);
      if (components === 1) {
        if (values.length !== declaredCells) throw new Error(`VTU cell scalar ${name} count does not match NumberOfCells.`);
        cellScalars[name] = values;
      } else if (components === 3) {
        if (values.length !== declaredCells * 3) throw new Error(`VTU cell vector ${name} count does not match NumberOfCells.`);
        cellVectors[name] = [];
        for (let index = 0; index < values.length; index += 3) {
          cellVectors[name].push([values[index], values[index + 1], values[index + 2]]);
        }
      } else {
        throw new Error(`Unsupported VTU CellData component count for ${name}: ${components}`);
      }
    }
  }
  const sourceIdentity = explicitSourceCellIdentity(cellScalars, declaredCells);

  return {
    format: "vtu-ascii-v1",
    points,
    cells,
    cellTypes,
    pointData: { scalars, vectors },
    cellData: { scalars: cellScalars, vectors: cellVectors },
    fields: Array.from(new Set([...Object.keys(scalars), ...Object.keys(vectors), ...Object.keys(cellScalars), ...Object.keys(cellVectors)])).sort(),
    ...(sourceIdentity ?? {}),
    sourceName,
    sourceText: text
  };
}

function parseLegacyDataArrays(
  tokens: string[],
  cursor: number,
  tupleCount: number,
  context: "POINT_DATA" | "CELL_DATA",
  scalars: Record<string, number[]>,
  vectors: Record<string, [number, number, number][]>
) {
  while (cursor < tokens.length && tokens[cursor] !== "POINT_DATA" && tokens[cursor] !== "CELL_DATA") {
    const section = tokens[cursor];
    if (section === "SCALARS") {
      const name = tokens[cursor + 1];
      const maybeComponents = Number(tokens[cursor + 3]);
      const hasComponentCount = Number.isInteger(maybeComponents);
      if (hasComponentCount && maybeComponents !== 1) {
        throw new Error(`Only single-component SCALARS are supported, got ${name} with ${maybeComponents}.`);
      }
      cursor += hasComponentCount ? 4 : 3;
      if (tokens[cursor] !== "LOOKUP_TABLE") throw new Error(`SCALARS ${name} is missing LOOKUP_TABLE.`);
      cursor += 2;
      scalars[name] = tokens.slice(cursor, cursor + tupleCount).map(Number);
      cursor += tupleCount;
    } else if (section === "VECTORS") {
      const name = tokens[cursor + 1];
      cursor += 3;
      const values: [number, number, number][] = [];
      for (let index = 0; index < tupleCount; index += 1) {
        values.push([Number(tokens[cursor]), Number(tokens[cursor + 1]), Number(tokens[cursor + 2])]);
        cursor += 3;
      }
      vectors[name] = values;
    } else if (section === "FIELD") {
      cursor += 1;
      if (cursor >= tokens.length) throw new Error(`${context} FIELD section is missing a name.`);
      cursor += 1;
      const arrayCount = Number(tokens[cursor]);
      if (!Number.isInteger(arrayCount) || arrayCount < 0) throw new Error(`${context} FIELD section has an invalid array count.`);
      cursor += 1;
      for (let arrayIndex = 0; arrayIndex < arrayCount; arrayIndex += 1) {
        const name = tokens[cursor];
        const components = Number(tokens[cursor + 1]);
        const tuples = Number(tokens[cursor + 2]);
        if (!name || !Number.isInteger(components) || !Number.isInteger(tuples)) {
          throw new Error(`${context} FIELD array header is invalid.`);
        }
        cursor += 4;
        const valueCount = components * tuples;
        const values = tokens.slice(cursor, cursor + valueCount).map(Number);
        if (values.length !== valueCount || values.some((value) => Number.isNaN(value))) {
          throw new Error(`${context} FIELD array ${name} has invalid numeric values.`);
        }
        if (tuples === tupleCount && components === 1) {
          scalars[name] = values;
        } else if (tuples === tupleCount && components === 3) {
          vectors[name] = [];
          for (let index = 0; index < values.length; index += 3) {
            vectors[name].push([values[index], values[index + 1], values[index + 2]]);
          }
        } else if (tuples === tupleCount) {
          throw new Error(`Unsupported ${context} FIELD array component count for ${name}: ${components}`);
        }
        cursor += valueCount;
      }
    } else {
      throw new Error(`Unsupported ${context} section: ${section}`);
    }
  }
  return cursor;
}

function validateCells(cells: number[][], pointCount: number) {
  for (const cell of cells) {
    if (cell.length < 3) throw new Error("VTK cells must have at least three points.");
    if (cell.some((index) => index < 0 || index >= pointCount)) throw new Error("VTK cell connectivity is out of range.");
  }
}

function numbers(text: string): number[] {
  return text.split(/\s+/).filter(Boolean).map(Number);
}

function namedArray(node: Element, name: string): number[] {
  const array = Array.from(node.querySelectorAll("DataArray")).find((candidate) => candidate.getAttribute("Name") === name);
  if (!array) throw new Error(`VTU Cells array ${name} is missing.`);
  if ((array.getAttribute("format") ?? "ascii") !== "ascii") throw new Error(`VTU ${name} array must be ASCII.`);
  return numbers(array.textContent ?? "");
}

export function fieldNameForOverlay(overlay: string): string | null {
  if (overlay === "phase") return "phase_fraction";
  if (overlay === "geometry" || overlay === "reynolds") return null;
  return overlay;
}

export function fieldAvailable(dataset: VtkResultDataset | null, overlay: string): boolean {
  return Boolean(fieldValuesForOverlay(dataset, overlay));
}

function fieldCandidatesForOverlay(overlay: string): string[] {
  if (overlay === "pressure") return ["pressure", "Pressure", "p", "p_rgh"];
  if (overlay === "velocity") return ["velocity", "Velocity", "U"];
  if (overlay === "temperature") return ["temperature", "Temperature", "T"];
  if (overlay === "phase") return ["phase_fraction", "alpha.water", "alpha"];
  const field = fieldNameForOverlay(overlay);
  return field ? [field] : [];
}

export function fieldValuesForOverlay(
  dataset: VtkResultDataset | null,
  overlay: string
): { field: string; values: number[]; kind: ResultFieldValueKind; location: "point" | "cell"; unit: ResultFieldUnit } | null {
  if (!dataset) return null;
  for (const field of fieldCandidatesForOverlay(overlay)) {
    const scalars = dataset.pointData.scalars[field];
    if (scalars) return { field, values: scalars, kind: "scalar", location: "point", unit: inferResultFieldUnit(field, "scalar", overlay) };
    const vectors = dataset.pointData.vectors[field];
    if (vectors) return { field, values: vectors.map(vectorMagnitude), kind: "vector-magnitude", location: "point", unit: inferResultFieldUnit(field, "vector-magnitude", overlay) };
    const cellScalars = dataset.cellData.scalars[field];
    if (cellScalars) return { field, values: cellScalars, kind: "scalar", location: "cell", unit: inferResultFieldUnit(field, "scalar", overlay) };
    const cellVectors = dataset.cellData.vectors[field];
    if (cellVectors) return { field, values: cellVectors.map(vectorMagnitude), kind: "vector-magnitude", location: "cell", unit: inferResultFieldUnit(field, "vector-magnitude", overlay) };
  }
  return null;
}

export function fieldValuesForSelection(
  dataset: VtkResultDataset | null,
  selection: ResultFieldSelection | null,
  vectorComponent: ResultVectorComponent = "magnitude"
): { field: string; values: number[]; kind: ResultFieldValueKind; location: ResultFieldLocation; unit: ResultFieldUnit } | null {
  if (!dataset || !selection) return null;
  const group = selection.location === "point" ? dataset.pointData : dataset.cellData;
  if (selection.kind === "scalar") {
    const scalars = group.scalars[selection.field];
    return scalars ? { field: selection.field, values: scalars, kind: "scalar", location: selection.location, unit: inferResultFieldUnit(selection.field, "scalar") } : null;
  }
  const vectors = group.vectors[selection.field];
  const kind = vectorComponentKind(vectorComponent);
  return vectors
    ? {
        field: selection.field,
        values: vectors.map((vector) => vectorComponentValue(vector, vectorComponent)),
        kind,
        location: selection.location,
        unit: inferResultFieldUnit(selection.field, kind)
      }
    : null;
}

export function fieldHistogramForValues(values: number[], binCount = 12): ResultFieldHistogramBin[] {
  const finiteValues = values.filter(Number.isFinite);
  if (finiteValues.length === 0) return [];
  const bins = Math.max(1, Math.min(32, Math.floor(binCount)));
  const min = minimumOf(finiteValues);
  const max = maximumOf(finiteValues);
  if (min === max) return [{ min, max, count: finiteValues.length }];
  const width = (max - min) / bins;
  const histogram = Array.from({ length: bins }, (_, index) => ({
    min: min + width * index,
    max: index === bins - 1 ? max : min + width * (index + 1),
    count: 0
  }));
  for (const value of finiteValues) {
    const index = Math.min(bins - 1, Math.floor((value - min) / width));
    histogram[index].count += 1;
  }
  return histogram;
}

function percentile(sortedValues: number[], percentileRank: number) {
  if (sortedValues.length === 0) return Number.NaN;
  if (sortedValues.length === 1) return sortedValues[0];
  const rank = Math.max(0, Math.min(1, percentileRank)) * (sortedValues.length - 1);
  const lower = Math.floor(rank);
  const upper = Math.ceil(rank);
  if (lower === upper) return sortedValues[lower];
  const weight = rank - lower;
  return sortedValues[lower] * (1 - weight) + sortedValues[upper] * weight;
}

export function fieldDescriptiveStats(values: number[]): ResultFieldDescriptiveStats | null {
  const finiteValues = values.filter(Number.isFinite);
  if (finiteValues.length === 0) return null;
  const sorted = [...finiteValues].sort((a, b) => a - b);
  const sum = finiteValues.reduce((total, value) => total + value, 0);
  const mean = sum / finiteValues.length;
  const variance = finiteValues.reduce((total, value) => total + (value - mean) ** 2, 0) / finiteValues.length;
  return {
    count: finiteValues.length,
    min: sorted[0],
    max: sorted[sorted.length - 1],
    mean,
    stdDev: Math.sqrt(variance),
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95)
  };
}

export function listResultFields(dataset: VtkResultDataset | null): ResultFieldInventoryItem[] {
  if (!dataset) return [];
  return [
    ...fieldInventoryGroup(dataset.pointData.scalars, "point", "scalar"),
    ...fieldInventoryGroup(dataset.pointData.vectors, "point", "vector"),
    ...fieldInventoryGroup(dataset.cellData.scalars, "cell", "scalar"),
    ...fieldInventoryGroup(dataset.cellData.vectors, "cell", "vector")
  ].sort((left, right) => {
    if (left.location !== right.location) return left.location === "point" ? -1 : 1;
    if (left.overlay && !right.overlay) return -1;
    if (!left.overlay && right.overlay) return 1;
    return left.field.localeCompare(right.field);
  });
}

function fieldInventoryGroup(
  fields: Record<string, number[] | [number, number, number][]>,
  location: ResultFieldLocation,
  kind: ResultFieldKind
): ResultFieldInventoryItem[] {
  return Object.entries(fields).map(([field, tuples]) => {
    const values = kind === "vector" ? (tuples as [number, number, number][]).map(vectorMagnitude) : (tuples as number[]);
    return {
      field,
      location,
      kind,
      tupleCount: tuples.length,
      min: values.length ? minimumOf(values) : 0,
      max: values.length ? maximumOf(values) : 0,
      overlay: overlayForField(field),
      unit: inferResultFieldUnit(field, kind)
    };
  });
}

function overlayForField(field: string): string | null {
  for (const overlay of ["pressure", "velocity", "temperature", "phase", "residuals"]) {
    if (fieldCandidatesForOverlay(overlay).includes(field)) return overlay;
  }
  return null;
}

export function fieldStatsForOverlay(
  dataset: VtkResultDataset | null,
  overlay: string
): ({ field: string; kind: ResultFieldValueKind; location: "point" | "cell"; unit: ResultFieldUnit } & ResultFieldDescriptiveStats) | null {
  const fieldValues = fieldValuesForOverlay(dataset, overlay);
  if (!fieldValues || fieldValues.values.length === 0) return null;
  const stats = fieldDescriptiveStats(fieldValues.values);
  if (!stats) return null;
  return {
    field: fieldValues.field,
    kind: fieldValues.kind,
    location: fieldValues.location,
    unit: fieldValues.unit,
    ...stats
  };
}

export function timelineStatsForSnapshots(
  snapshots: Array<{ id: string; label: string; time: number; dataset: VtkResultDataset }>,
  overlay: string,
  selection: ResultFieldSelection | null = null,
  vectorComponent: ResultVectorComponent = "magnitude"
): ResultFieldTimelineSample[] {
  return snapshots.map((snapshot) => {
    const fieldValues = selection ? fieldValuesForSelection(snapshot.dataset, selection, vectorComponent) : fieldValuesForOverlay(snapshot.dataset, overlay);
    if (!fieldValues || fieldValues.values.length === 0) {
      return {
        id: snapshot.id,
        label: snapshot.label,
        time: snapshot.time,
        field: null,
        location: null,
        kind: null,
        min: null,
        max: null,
        mean: null,
        unit: null
      };
    }
    const sum = fieldValues.values.reduce((total, value) => total + value, 0);
    return {
      id: snapshot.id,
      label: snapshot.label,
      time: snapshot.time,
      field: fieldValues.field,
      location: fieldValues.location,
      kind: fieldValues.kind,
      min: minimumOf(fieldValues.values),
      max: maximumOf(fieldValues.values),
      mean: sum / fieldValues.values.length,
      unit: fieldValues.unit
    };
  });
}

export function fieldCoverageForSnapshots(
  snapshots: Array<{ id: string; label: string; time: number; dataset: VtkResultDataset }>,
  overlay: string,
  selection: ResultFieldSelection | null = null,
  vectorComponent: ResultVectorComponent = "magnitude"
): ResultFieldCoverage {
  const samples = timelineStatsForSnapshots(snapshots, overlay, selection, vectorComponent);
  const present = samples.filter((sample) => sample.mean !== null);
  return {
    totalSnapshots: samples.length,
    presentSnapshots: present.length,
    missingSnapshots: samples.length - present.length,
    missingLabels: samples.filter((sample) => sample.mean === null).map((sample) => sample.label),
    fields: Array.from(new Set(present.map((sample) => sample.field).filter((field): field is string => Boolean(field)))).sort(),
    locations: Array.from(new Set(present.map((sample) => sample.location).filter((location): location is ResultFieldLocation => Boolean(location)))).sort(),
    kinds: Array.from(new Set(present.map((sample) => sample.kind).filter((kind): kind is ResultFieldValueKind => Boolean(kind)))).sort(),
    units: Array.from(new Map(present.map((sample) => sample.unit).filter((unit): unit is ResultFieldUnit => Boolean(unit)).map((unit) => [unit.symbol, unit])).values()).sort((a, b) => a.symbol.localeCompare(b.symbol))
  };
}

/**
 * Bounding box of a dataset's points, with the centre and overall extent the
 * renderers scale by.
 *
 * Both the 3D and the 2D canvas renderer used to carry their own byte-identical
 * copy of this, which is how one spread-overflow bug came to need fixing twice.
 * It lives here now, next to the dataset it measures.
 */
export function datasetBounds(dataset: VtkResultDataset) {
  const { min, max } = pointExtent(dataset.points);
  return {
    min,
    max,
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2] as [number, number, number],
    // Floored because callers divide by this: a flat mesh would otherwise
    // scale the surface by infinity.
    span: Math.max(max[0] - min[0], max[1] - min[1], max[2] - min[2], 1e-9)
  };
}

export function projectDatasetToCanvas(dataset: VtkResultDataset, canvasWidth: number, canvasHeight: number) {
  const { min, max } = pointExtent(dataset.points);
  const [minX, minY] = min;
  const [maxX, maxY] = max;
  const rawFitsCanvas = minX >= 0 && minY >= 0 && maxX <= canvasWidth && maxY <= canvasHeight;
  const padding = 120;
  const fitScale = Math.min(
    (canvasWidth - padding * 2) / Math.max(maxX - minX, 1),
    (canvasHeight - padding * 2) / Math.max(maxY - minY, 1)
  );
  const scale = rawFitsCanvas ? 1 : Math.max(0.2, Math.min(fitScale, 4));
  const offsetX = rawFitsCanvas ? 0 : (canvasWidth - (maxX - minX) * scale) / 2 - minX * scale;
  const offsetY = rawFitsCanvas ? 0 : (canvasHeight - (maxY - minY) * scale) / 2 - minY * scale;

  return {
    coordinates(point: [number, number, number]) {
      return { x: point[0] * scale + offsetX, y: point[1] * scale + offsetY };
    },
    point(index: number) {
      const point = dataset.points[index];
      return { x: point[0] * scale + offsetX, y: point[1] * scale + offsetY };
    }
  };
}

export function sampleDatasetAtCanvasPoint(
  dataset: VtkResultDataset | null,
  overlay: string,
  canvasPoint: { x: number; y: number },
  canvasSize: { width: number; height: number },
  selection: ResultFieldSelection | null = null,
  vectorComponent: ResultVectorComponent = "magnitude"
): { field: string; value: number; point: [number, number, number]; pointIndex: number; distancePx: number; location: "point" | "cell"; unit: ResultFieldUnit } | null {
  const fieldValues = selection ? fieldValuesForSelection(dataset, selection, vectorComponent) : fieldValuesForOverlay(dataset, overlay);
  if (!dataset || !fieldValues) return null;
  const projection = projectDatasetToCanvas(dataset, canvasSize.width, canvasSize.height);
  let bestIndex = -1;
  let bestDistance = Number.POSITIVE_INFINITY;
  const samplePoints =
    fieldValues.location === "cell"
      ? dataset.cells.map((cell) => {
          const center = cell.reduce(
            (sum, pointIndex) => {
              const point = dataset.points[pointIndex];
              return [sum[0] + point[0] / cell.length, sum[1] + point[1] / cell.length, sum[2] + point[2] / cell.length] as [number, number, number];
            },
            [0, 0, 0] as [number, number, number]
          );
          return center;
        })
      : dataset.points;
  samplePoints.forEach((point, index) => {
    const projected = fieldValues.location === "cell" ? projection.coordinates(point) : projection.point(index);
    const distance = Math.hypot(projected.x - canvasPoint.x, projected.y - canvasPoint.y);
    if (distance < bestDistance) {
      bestIndex = index;
      bestDistance = distance;
    }
  });
  if (bestIndex < 0) return null;
  return {
    field: fieldValues.field,
    value: fieldValues.values[bestIndex],
    point: samplePoints[bestIndex],
    pointIndex: bestIndex,
    distancePx: bestDistance,
    location: fieldValues.location,
    unit: fieldValues.unit
  };
}

export function sampleDatasetAtWorldPoint(
  dataset: VtkResultDataset | null,
  overlay: string,
  worldPoint: [number, number, number],
  selection: ResultFieldSelection | null = null,
  vectorComponent: ResultVectorComponent = "magnitude",
  preferredCellIndex?: number,
  preferredPointIndex?: number,
  pointInterpolation?: {
    pointIndices: [number, number, number];
    weights: [number, number, number];
  }
): {
  field: string;
  value: number;
  point: [number, number, number];
  pointIndex: number;
  distanceM: number;
  location: "point" | "cell";
  unit: ResultFieldUnit;
} | null {
  const fieldValues = selection
    ? fieldValuesForSelection(dataset, selection, vectorComponent)
    : fieldValuesForOverlay(dataset, overlay);
  if (!dataset || !fieldValues || dataset.points.length === 0) return null;

  const cellCenters = () =>
    dataset.cells.map((cell) =>
      cell.reduce(
        (sum, pointIndex) => {
          const point = dataset.points[pointIndex];
          return [
            sum[0] + point[0] / cell.length,
            sum[1] + point[1] / cell.length,
            sum[2] + point[2] / cell.length
          ] as [number, number, number];
        },
        [0, 0, 0] as [number, number, number]
      )
    );
  const candidates = fieldValues.location === "cell" ? cellCenters() : dataset.points;
  if (candidates.length === 0) return null;
  const preferred =
    fieldValues.location === "cell" ? preferredCellIndex : preferredPointIndex;
  let bestIndex =
    Number.isInteger(preferred) && Number(preferred) >= 0 && Number(preferred) < candidates.length
      ? Number(preferred)
      : -1;
  let bestDistance =
    bestIndex >= 0
      ? Math.hypot(
          candidates[bestIndex][0] - worldPoint[0],
          candidates[bestIndex][1] - worldPoint[1],
          candidates[bestIndex][2] - worldPoint[2]
        )
      : Number.POSITIVE_INFINITY;
  if (bestIndex < 0) {
    candidates.forEach((point, index) => {
      const distance = Math.hypot(
        point[0] - worldPoint[0],
        point[1] - worldPoint[1],
        point[2] - worldPoint[2]
      );
      if (distance < bestDistance) {
        bestIndex = index;
        bestDistance = distance;
      }
    });
  }
  if (bestIndex < 0 || !Number.isFinite(fieldValues.values[bestIndex])) return null;
  const interpolatedValue =
    fieldValues.location === "point" &&
    pointInterpolation &&
    pointInterpolation.pointIndices.every(
      (pointIndex) =>
        Number.isInteger(pointIndex) &&
        pointIndex >= 0 &&
        pointIndex < fieldValues.values.length &&
        Number.isFinite(fieldValues.values[pointIndex])
    ) &&
    pointInterpolation.weights.every((weight) => Number.isFinite(weight))
      ? pointInterpolation.pointIndices.reduce(
          (sum, pointIndex, index) =>
            sum + fieldValues.values[pointIndex] * pointInterpolation.weights[index],
          0
        )
      : null;
  return {
    field: fieldValues.field,
    value: interpolatedValue ?? fieldValues.values[bestIndex],
    point: [worldPoint[0], worldPoint[1], worldPoint[2]],
    pointIndex: bestIndex,
    distanceM: bestDistance,
    location: fieldValues.location,
    unit: fieldValues.unit
  };
}
