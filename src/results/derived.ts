import * as THREE from "three";
import type { ResultComponentMap } from "../types";

export const DERIVED_REQUEST_SCHEMA = "flowlab.derived_visualization_request.v1" as const;
export const DERIVED_MANIFEST_SCHEMA = "flowlab.derived_visualization_manifest.v1" as const;
export const MAX_BROWSER_RESIDENCY_BYTES = 96 * 1024 * 1024;
export const MAX_ISO_TRIANGLES = 500_000;
export const INVALID_SOURCE_CELL = 0xffffffff;

export type DerivedArtifactRef = { path: string; time?: number | null };
export type DerivedFieldRequest = {
  name: string;
  location: "point" | "cell";
  kind: "scalar" | "vector";
  unit: string;
};
export type DerivedVisualizationRequest = {
  schema: typeof DERIVED_REQUEST_SCHEMA;
  operation: "volume" | "pathlines";
  artifacts: DerivedArtifactRef[];
  fields: DerivedFieldRequest[];
  grid?: {
    dimensions: [number, number, number];
    gradients: Array<"pressure" | "speed">;
  };
  pathlines?: {
    seeds: [number, number, number][];
    stepSeconds: number;
    maxVertices: number;
  };
};
export type DerivedBlobDescriptor = {
  schema: "flowlab.derived_visualization_blob.v1";
  name: string;
  dtype: "float32" | "uint32" | "uint8";
  components: number;
  count: number;
  byteOrder: "little-endian";
  byteLength: number;
  sha256: string;
};
export type DerivedFieldManifest = DerivedFieldRequest & {
  values: DerivedBlobDescriptor;
  validity: DerivedBlobDescriptor;
};
export type DerivedVisualizationManifest = {
  schema: typeof DERIVED_MANIFEST_SCHEMA;
  requestSchema: typeof DERIVED_REQUEST_SCHEMA;
  requestSha256: string;
  manifestSha256: string;
  operation: "volume" | "pathlines";
  visualizationOnly: true;
  scientificStateEffect: "none";
  releaseStateEffect: "none";
  unitAuthority: "case-contract" | "user-declared";
  sourceArtifacts: Array<{
    path: string;
    time: number | null;
    size: number;
    sha256: string;
    geometryDigest: string;
    cellOrderDigest: string;
  }>;
  componentResolution: {
    status: "source-cell-map" | "probe-only";
    reason: string;
    map: ResultComponentMap | null;
  };
  limits: {
    defaultGridDimension: number;
    maxGridDimension: number;
    artifactSetBytes: number;
    browserResidencyBytes: number;
    derivedCacheBytesPerJob: number;
    maxSeeds: number;
    maxPathlineVertices: number;
    maxIsoTriangles: number;
    overflowBehavior: "reject";
  };
  grid?: {
    dimensions: [number, number, number];
    voxelCount: number;
    bounds: { min: [number, number, number]; max: [number, number, number] };
    spacing: [number, number, number];
    sampleLocation: "voxel-center";
  };
  fields?: DerivedFieldManifest[];
  gradients?: Array<{
    name: string;
    source: string;
    unit: string;
    values: DerivedBlobDescriptor;
    validity: DerivedBlobDescriptor;
  }>;
  pathlines?: {
    integration: "deterministic-rk4-time-linear-v1";
    seedCount: number;
    vertexCount: number;
    stepSeconds: number;
    startTime: number;
    endTime: number;
    terminations: string[];
    positions: DerivedBlobDescriptor;
    times: DerivedBlobDescriptor;
    offsets: DerivedBlobDescriptor;
  };
  provenance: {
    validity?: DerivedBlobDescriptor;
    sourceCellIds: DerivedBlobDescriptor;
    subcellIds: DerivedBlobDescriptor;
    ambiguity: DerivedBlobDescriptor;
    spatialWeights: DerivedBlobDescriptor;
    invalidSourceCellId?: number;
    ambiguousSelections: "probe-only";
  };
  blobs: DerivedBlobDescriptor[];
  browserResidencyBytes: number;
};

export type DerivedTypedArray = Float32Array | Uint32Array | Uint8Array;

export type DecodedDerivedVisualization = {
  manifest: DerivedVisualizationManifest;
  blobs: Map<string, DerivedTypedArray>;
  disposed: boolean;
  textures: Set<THREE.Texture>;
  dispose: () => void;
};

function assertManifest(manifest: DerivedVisualizationManifest) {
  if (manifest.schema !== DERIVED_MANIFEST_SCHEMA || manifest.requestSchema !== DERIVED_REQUEST_SCHEMA) {
    throw new Error("Unsupported derived visualization contract.");
  }
  if (!manifest.visualizationOnly || manifest.scientificStateEffect !== "none" || manifest.releaseStateEffect !== "none") {
    throw new Error("Derived visualization manifest attempts to change scientific or release state.");
  }
  if (!/^[a-f0-9]{64}$/.test(manifest.requestSha256) || !/^[a-f0-9]{64}$/.test(manifest.manifestSha256)) {
    throw new Error("Derived visualization hashes are malformed.");
  }
  if (
    !Number.isInteger(manifest.browserResidencyBytes)
    || manifest.browserResidencyBytes < 0
    || manifest.browserResidencyBytes > MAX_BROWSER_RESIDENCY_BYTES
    || manifest.browserResidencyBytes > manifest.limits.browserResidencyBytes
  ) {
    throw new Error(`Derived visualization exceeds the ${MAX_BROWSER_RESIDENCY_BYTES} byte browser residency budget.`);
  }
  const names = new Set<string>();
  let byteLength = 0;
  for (const blob of manifest.blobs) {
    if (
      blob.schema !== "flowlab.derived_visualization_blob.v1"
      || !/^[a-z0-9][a-z0-9._-]*\.bin$/.test(blob.name)
      || names.has(blob.name)
      || blob.byteOrder !== "little-endian"
      || !/^[a-f0-9]{64}$/.test(blob.sha256)
      || !Number.isInteger(blob.components)
      || blob.components <= 0
      || !Number.isInteger(blob.count)
      || blob.count < 0
    ) {
      throw new Error("Derived visualization contains an invalid binary blob descriptor.");
    }
    const width = blob.dtype === "uint8" ? 1 : 4;
    if (blob.byteLength !== blob.count * blob.components * width) {
      throw new Error(`Derived blob byte length does not match its typed shape: ${blob.name}`);
    }
    names.add(blob.name);
    byteLength += blob.byteLength;
  }
  if (byteLength !== manifest.browserResidencyBytes) {
    throw new Error("Derived manifest browser residency does not match its declared blobs.");
  }
}

async function sha256Hex(buffer: ArrayBuffer): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function typedArray(descriptor: DerivedBlobDescriptor, buffer: ArrayBuffer): DerivedTypedArray {
  if (buffer.byteLength !== descriptor.byteLength) {
    throw new Error(`Derived blob size mismatch: ${descriptor.name}`);
  }
  if (descriptor.dtype === "float32") return new Float32Array(buffer);
  if (descriptor.dtype === "uint32") return new Uint32Array(buffer);
  return new Uint8Array(buffer);
}

export async function decodeDerivedVisualization(
  manifest: DerivedVisualizationManifest,
  blobUrl: (name: string) => string,
  fetchImpl: typeof fetch = fetch
): Promise<DecodedDerivedVisualization> {
  assertManifest(manifest);
  const blobs = new Map<string, DerivedTypedArray>();
  for (const descriptor of manifest.blobs) {
    const response = await fetchImpl(blobUrl(descriptor.name));
    if (!response.ok) throw new Error(`Could not load derived blob ${descriptor.name}: ${response.status}`);
    const buffer = await response.arrayBuffer();
    const digest = await sha256Hex(buffer);
    if (digest !== null && digest !== descriptor.sha256) {
      throw new Error(`Derived blob hash mismatch: ${descriptor.name}`);
    }
    blobs.set(descriptor.name, typedArray(descriptor, buffer));
  }
  const textures = new Set<THREE.Texture>();
  const decoded: DecodedDerivedVisualization = {
    manifest,
    blobs,
    disposed: false,
    textures,
    dispose() {
      if (decoded.disposed) return;
      textures.forEach((texture) => texture.dispose());
      textures.clear();
      blobs.clear();
      decoded.disposed = true;
    }
  };
  return decoded;
}

function requiredBlob<T extends DerivedTypedArray>(
  decoded: DecodedDerivedVisualization,
  descriptor: DerivedBlobDescriptor,
  constructor: { new (buffer: ArrayBufferLike): T }
): T {
  if (decoded.disposed) throw new Error("Derived visualization has been disposed.");
  const blob = decoded.blobs.get(descriptor.name);
  if (!blob || !(blob instanceof constructor)) throw new Error(`Derived blob has the wrong decoded type: ${descriptor.name}`);
  return blob as T;
}

export type DerivedVolumeTextures = {
  values: THREE.Data3DTexture;
  validity: THREE.Data3DTexture;
  sourceCellIds: THREE.Data3DTexture;
  ambiguity: THREE.Data3DTexture;
  dispose: () => void;
};

export function createDerivedVolumeTextures(
  decoded: DecodedDerivedVisualization,
  fieldIndex = 0
): DerivedVolumeTextures {
  const { manifest } = decoded;
  if (manifest.operation !== "volume" || !manifest.grid || !manifest.fields?.[fieldIndex] || !manifest.provenance.validity) {
    throw new Error("Derived volume textures require a decoded volume field.");
  }
  const [width, height, depth] = manifest.grid.dimensions;
  const field = manifest.fields[fieldIndex];
  const valuesArray = requiredBlob(decoded, field.values, Float32Array);
  const validityArray = requiredBlob(decoded, manifest.provenance.validity, Uint8Array);
  const sourceArray = requiredBlob(decoded, manifest.provenance.sourceCellIds, Uint32Array);
  const ambiguityArray = requiredBlob(decoded, manifest.provenance.ambiguity, Uint8Array);
  const values = new THREE.Data3DTexture(valuesArray, width, height, depth);
  values.format = field.kind === "vector" ? THREE.RGBFormat : THREE.RedFormat;
  values.type = THREE.FloatType;
  values.minFilter = THREE.LinearFilter;
  values.magFilter = THREE.LinearFilter;
  values.unpackAlignment = 1;
  values.needsUpdate = true;
  const validity = new THREE.Data3DTexture(validityArray, width, height, depth);
  validity.format = THREE.RedIntegerFormat;
  validity.type = THREE.UnsignedByteType;
  validity.minFilter = THREE.NearestFilter;
  validity.magFilter = THREE.NearestFilter;
  validity.unpackAlignment = 1;
  validity.needsUpdate = true;
  const sourceCellIds = new THREE.Data3DTexture(sourceArray, width, height, depth);
  sourceCellIds.format = THREE.RedIntegerFormat;
  sourceCellIds.type = THREE.UnsignedIntType;
  sourceCellIds.minFilter = THREE.NearestFilter;
  sourceCellIds.magFilter = THREE.NearestFilter;
  sourceCellIds.unpackAlignment = 1;
  sourceCellIds.needsUpdate = true;
  const ambiguity = new THREE.Data3DTexture(ambiguityArray, width, height, depth);
  ambiguity.format = THREE.RedIntegerFormat;
  ambiguity.type = THREE.UnsignedByteType;
  ambiguity.minFilter = THREE.NearestFilter;
  ambiguity.magFilter = THREE.NearestFilter;
  ambiguity.unpackAlignment = 1;
  ambiguity.needsUpdate = true;
  [values, validity, sourceCellIds, ambiguity].forEach((texture) => decoded.textures.add(texture));
  const textures = { values, validity, sourceCellIds, ambiguity };
  return {
    ...textures,
    dispose() {
      Object.values(textures).forEach((texture) => {
        texture.dispose();
        decoded.textures.delete(texture);
      });
    }
  };
}

export type DerivedSelection =
  | { state: "linked"; sourceCellId: number; edgeId: string }
  | { state: "probe-only"; sourceCellId: number | null; reason: string };

export function resolveDerivedSelection(
  decoded: DecodedDerivedVisualization,
  sampleIndex: number,
  componentMap: ResultComponentMap | null = decoded.manifest.componentResolution.map
): DerivedSelection {
  const sourceIds = requiredBlob(decoded, decoded.manifest.provenance.sourceCellIds, Uint32Array);
  const ambiguity = requiredBlob(decoded, decoded.manifest.provenance.ambiguity, Uint8Array);
  if (!Number.isInteger(sampleIndex) || sampleIndex < 0 || sampleIndex >= sourceIds.length) {
    return { state: "probe-only", sourceCellId: null, reason: "Derived sample index is out of range." };
  }
  const sourceCellId = sourceIds[sampleIndex];
  if (sourceCellId === INVALID_SOURCE_CELL) {
    return { state: "probe-only", sourceCellId: null, reason: "Derived sample is outside the valid source volume." };
  }
  if (ambiguity[sampleIndex]) {
    return { state: "probe-only", sourceCellId, reason: "Derived sample has ambiguous source-cell contributors." };
  }
  if (!componentMap) {
    return { state: "probe-only", sourceCellId, reason: "No explicit generated-case component map is available." };
  }
  const owners = new Set<string>();
  for (const binding of componentMap.artifactBindings) {
    if (binding.scope === "all-cells") {
      owners.add(binding.edgeId);
    } else if (sourceCellId < binding.sourceCellCount) {
      binding.cellRanges.forEach((range) => {
        if (sourceCellId >= range.cellStart && sourceCellId < range.cellStart + range.cellCount) owners.add(range.edgeId);
      });
    }
  }
  if (owners.size !== 1) {
    return { state: "probe-only", sourceCellId, reason: "Derived sample has no unique explicit schematic owner." };
  }
  return { state: "linked", sourceCellId, edgeId: Array.from(owners)[0] };
}

export type DerivedCutPlane = {
  axis: 0 | 1 | 2;
  index: number;
  dimensions: [number, number];
  values: Float32Array;
  validity: Uint8Array;
  sourceCellIds: Uint32Array;
  ambiguity: Uint8Array;
};

export function extractDerivedCutPlane(
  decoded: DecodedDerivedVisualization,
  axis: 0 | 1 | 2,
  planeIndex: number,
  fieldIndex = 0,
  component = 0
): DerivedCutPlane {
  const grid = decoded.manifest.grid;
  const field = decoded.manifest.fields?.[fieldIndex];
  const validityDescriptor = decoded.manifest.provenance.validity;
  if (!grid || !field || !validityDescriptor) throw new Error("Cut planes require a decoded volume.");
  const dimensions = grid.dimensions;
  if (!Number.isInteger(planeIndex) || planeIndex < 0 || planeIndex >= dimensions[axis]) {
    throw new Error("Cut-plane index is outside the derived grid.");
  }
  const components = field.kind === "vector" ? 3 : 1;
  if (!Number.isInteger(component) || component < 0 || component >= components) throw new Error("Cut-plane field component is invalid.");
  const sourceValues = requiredBlob(decoded, field.values, Float32Array);
  const sourceValidity = requiredBlob(decoded, validityDescriptor, Uint8Array);
  const sourceIds = requiredBlob(decoded, decoded.manifest.provenance.sourceCellIds, Uint32Array);
  const sourceAmbiguity = requiredBlob(decoded, decoded.manifest.provenance.ambiguity, Uint8Array);
  const freeAxes = ([0, 1, 2] as const).filter((candidate) => candidate !== axis);
  const width = dimensions[freeAxes[0]];
  const height = dimensions[freeAxes[1]];
  const values = new Float32Array(width * height);
  const validity = new Uint8Array(width * height);
  const sourceCellIds = new Uint32Array(width * height);
  const ambiguity = new Uint8Array(width * height);
  const flatIndex = (x: number, y: number, z: number) => (z * dimensions[1] + y) * dimensions[0] + x;
  for (let row = 0; row < height; row += 1) {
    for (let column = 0; column < width; column += 1) {
      const coordinates = [0, 0, 0];
      coordinates[axis] = planeIndex;
      coordinates[freeAxes[0]] = column;
      coordinates[freeAxes[1]] = row;
      const sourceIndex = flatIndex(coordinates[0], coordinates[1], coordinates[2]);
      const targetIndex = row * width + column;
      values[targetIndex] = sourceValues[sourceIndex * components + component];
      validity[targetIndex] = sourceValidity[sourceIndex];
      sourceCellIds[targetIndex] = sourceIds[sourceIndex];
      ambiguity[targetIndex] = sourceAmbiguity[sourceIndex];
    }
  }
  return { axis, index: planeIndex, dimensions: [width, height], values, validity, sourceCellIds, ambiguity };
}

type IsoVertex = {
  position: [number, number, number];
  sourceCellIds: number[];
  probeOnly: boolean;
};
export type DerivedIsoTriangle = {
  vertices: [IsoVertex, IsoVertex, IsoVertex];
  sourceCellIds: number[];
  probeOnly: boolean;
};

const cubeTetrahedra = [
  [0, 1, 2, 6],
  [0, 2, 3, 6],
  [0, 3, 7, 6],
  [0, 7, 4, 6],
  [0, 4, 5, 6],
  [0, 5, 1, 6]
] as const;
const cubeOffsets = [
  [0, 0, 0],
  [1, 0, 0],
  [1, 1, 0],
  [0, 1, 0],
  [0, 0, 1],
  [1, 0, 1],
  [1, 1, 1],
  [0, 1, 1]
] as const;
const tetraEdges = [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]] as const;

function sortPolygon(vertices: IsoVertex[]): IsoVertex[] {
  const center = vertices.reduce(
    (sum, vertex) => [sum[0] + vertex.position[0] / vertices.length, sum[1] + vertex.position[1] / vertices.length, sum[2] + vertex.position[2] / vertices.length] as [number, number, number],
    [0, 0, 0] as [number, number, number]
  );
  const first = new THREE.Vector3(...vertices[0].position).sub(new THREE.Vector3(...center)).normalize();
  let normal = new THREE.Vector3();
  for (let index = 1; index < vertices.length && normal.lengthSq() < 1e-12; index += 1) {
    normal = first.clone().cross(new THREE.Vector3(...vertices[index].position).sub(new THREE.Vector3(...center))).normalize();
  }
  const second = normal.clone().cross(first).normalize();
  return [...vertices].sort((left, right) => {
    const l = new THREE.Vector3(...left.position).sub(new THREE.Vector3(...center));
    const r = new THREE.Vector3(...right.position).sub(new THREE.Vector3(...center));
    return Math.atan2(l.dot(second), l.dot(first)) - Math.atan2(r.dot(second), r.dot(first));
  });
}

export function extractDerivedIsoSurface(
  decoded: DecodedDerivedVisualization,
  isoValue: number,
  fieldIndex = 0,
  component = 0,
  triangleLimit = MAX_ISO_TRIANGLES
): DerivedIsoTriangle[] {
  const grid = decoded.manifest.grid;
  const field = decoded.manifest.fields?.[fieldIndex];
  const validityDescriptor = decoded.manifest.provenance.validity;
  if (!grid || !field || !validityDescriptor) throw new Error("Iso extraction requires a decoded volume.");
  if (!Number.isFinite(isoValue)) throw new Error("Iso value must be finite.");
  if (!Number.isInteger(triangleLimit) || triangleLimit < 1 || triangleLimit > MAX_ISO_TRIANGLES) {
    throw new Error(`Iso triangle limit must be between 1 and ${MAX_ISO_TRIANGLES}.`);
  }
  const components = field.kind === "vector" ? 3 : 1;
  if (!Number.isInteger(component) || component < 0 || component >= components) throw new Error("Iso field component is invalid.");
  const values = requiredBlob(decoded, field.values, Float32Array);
  const validity = requiredBlob(decoded, validityDescriptor, Uint8Array);
  const sourceIds = requiredBlob(decoded, decoded.manifest.provenance.sourceCellIds, Uint32Array);
  const ambiguity = requiredBlob(decoded, decoded.manifest.provenance.ambiguity, Uint8Array);
  const [nx, ny, nz] = grid.dimensions;
  const flatIndex = (x: number, y: number, z: number) => (z * ny + y) * nx + x;
  const physicalPoint = (x: number, y: number, z: number): [number, number, number] => [
    grid.bounds.min[0] + (x + 0.5) * grid.spacing[0],
    grid.bounds.min[1] + (y + 0.5) * grid.spacing[1],
    grid.bounds.min[2] + (z + 0.5) * grid.spacing[2]
  ];
  const triangles: DerivedIsoTriangle[] = [];
  for (let z = 0; z < nz - 1; z += 1) {
    for (let y = 0; y < ny - 1; y += 1) {
      for (let x = 0; x < nx - 1; x += 1) {
        const cube = cubeOffsets.map(([dx, dy, dz]) => {
          const sampleIndex = flatIndex(x + dx, y + dy, z + dz);
          return {
            sampleIndex,
            value: values[sampleIndex * components + component],
            point: physicalPoint(x + dx, y + dy, z + dz)
          };
        });
        if (cube.some((vertex) => !validity[vertex.sampleIndex])) continue;
        for (const tetrahedron of cubeTetrahedra) {
          const polygon: IsoVertex[] = [];
          for (const [leftIndex, rightIndex] of tetraEdges) {
            const left = cube[tetrahedron[leftIndex]];
            const right = cube[tetrahedron[rightIndex]];
            const leftSide = left.value >= isoValue;
            const rightSide = right.value >= isoValue;
            if (leftSide === rightSide || left.value === right.value) continue;
            const fraction = (isoValue - left.value) / (right.value - left.value);
            const contributors = Array.from(new Set([sourceIds[left.sampleIndex], sourceIds[right.sampleIndex]])).filter((value) => value !== INVALID_SOURCE_CELL);
            polygon.push({
              position: [
                left.point[0] + (right.point[0] - left.point[0]) * fraction,
                left.point[1] + (right.point[1] - left.point[1]) * fraction,
                left.point[2] + (right.point[2] - left.point[2]) * fraction
              ],
              sourceCellIds: contributors,
              probeOnly:
                Boolean(ambiguity[left.sampleIndex] || ambiguity[right.sampleIndex])
                || contributors.length !== 1
            });
          }
          const unique = Array.from(
            new Map(polygon.map((vertex) => [vertex.position.map((value) => value.toPrecision(12)).join(","), vertex])).values()
          );
          if (unique.length < 3) continue;
          const sorted = sortPolygon(unique);
          for (let index = 1; index < sorted.length - 1; index += 1) {
            const vertices = [sorted[0], sorted[index], sorted[index + 1]] as [IsoVertex, IsoVertex, IsoVertex];
            const contributors = Array.from(new Set(vertices.flatMap((vertex) => vertex.sourceCellIds))).sort((a, b) => a - b);
            triangles.push({
              vertices,
              sourceCellIds: contributors,
              probeOnly: contributors.length !== 1 || vertices.some((vertex) => vertex.probeOnly)
            });
            if (triangles.length > triangleLimit) {
              throw new Error(`Iso extraction exceeds ${triangleLimit} triangles; output was rejected instead of truncated.`);
            }
          }
        }
      }
    }
  }
  return triangles;
}

export function pathlinePositionAt(
  decoded: DecodedDerivedVisualization,
  pathlineIndex: number,
  time: number
): [number, number, number] | null {
  const pathlines = decoded.manifest.pathlines;
  if (!pathlines) throw new Error("Pathline animation requires a decoded pathline product.");
  const positions = requiredBlob(decoded, pathlines.positions, Float32Array);
  const times = requiredBlob(decoded, pathlines.times, Float32Array);
  const offsets = requiredBlob(decoded, pathlines.offsets, Uint32Array);
  if (!Number.isInteger(pathlineIndex) || pathlineIndex < 0 || pathlineIndex + 1 >= offsets.length) {
    throw new Error("Pathline index is out of range.");
  }
  const start = offsets[pathlineIndex];
  const stop = offsets[pathlineIndex + 1];
  if (start >= stop) return null;
  if (time <= times[start]) return [positions[start * 3], positions[start * 3 + 1], positions[start * 3 + 2]];
  if (time >= times[stop - 1]) return [positions[(stop - 1) * 3], positions[(stop - 1) * 3 + 1], positions[(stop - 1) * 3 + 2]];
  let upper = start + 1;
  while (upper < stop && times[upper] < time) upper += 1;
  const lower = upper - 1;
  const fraction = (time - times[lower]) / (times[upper] - times[lower]);
  return [0, 1, 2].map(
    (axis) => positions[lower * 3 + axis] * (1 - fraction) + positions[upper * 3 + axis] * fraction
  ) as [number, number, number];
}
