import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  DERIVED_MANIFEST_SCHEMA,
  DERIVED_REQUEST_SCHEMA,
  MAX_BROWSER_RESIDENCY_BYTES,
  createDerivedVolumeTextures,
  decodeDerivedVisualization,
  extractDerivedCutPlane,
  extractDerivedIsoSurface,
  pathlinePositionAt,
  resolveDerivedSelection,
  type DecodedDerivedVisualization,
  type DerivedBlobDescriptor,
  type DerivedTypedArray,
  type DerivedVisualizationManifest
} from "./derived";
import { buildDerivedPresentation } from "../components/derivedRenderer";

async function digest(buffer: ArrayBuffer) {
  const value = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(value), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function descriptor(
  name: string,
  dtype: DerivedBlobDescriptor["dtype"],
  components: number,
  count: number,
  buffer: ArrayBuffer
): Promise<DerivedBlobDescriptor> {
  return {
    schema: "flowlab.derived_visualization_blob.v1",
    name,
    dtype,
    components,
    count,
    byteOrder: "little-endian",
    byteLength: buffer.byteLength,
    sha256: await digest(buffer)
  };
}

async function volumeFixture() {
  const arrays = {
    "values-000.bin": new Float32Array([0, 1, 0, 1, 0, 1, 0, 1]),
    "validity.bin": new Uint8Array(8).fill(1),
    "source-cell-ids.bin": new Uint32Array(8).fill(4),
    "subcell-ids.bin": new Uint8Array(8),
    "ambiguity.bin": new Uint8Array(8),
    "spatial-weights.bin": new Float32Array(32).fill(0.25)
  };
  const blobs = Object.fromEntries(
    await Promise.all(
      Object.entries(arrays).map(async ([name, value]) => [
        name,
        await descriptor(
          name,
          value instanceof Uint8Array ? "uint8" : value instanceof Uint32Array ? "uint32" : "float32",
          name === "spatial-weights.bin" ? 4 : 1,
          name === "spatial-weights.bin" ? 8 : value.length,
          value.buffer
        )
      ])
    )
  ) as Record<string, DerivedBlobDescriptor>;
  const manifest: DerivedVisualizationManifest = {
    schema: DERIVED_MANIFEST_SCHEMA,
    requestSchema: DERIVED_REQUEST_SCHEMA,
    requestSha256: "a".repeat(64),
    manifestSha256: "b".repeat(64),
    operation: "volume",
    visualizationOnly: true,
    scientificStateEffect: "none",
    releaseStateEffect: "none",
    unitAuthority: "case-contract",
    sourceArtifacts: [
      {
        path: "VTK/result.vtk",
        time: 0,
        size: 1,
        sha256: "c".repeat(64),
        geometryDigest: "d".repeat(64),
        cellOrderDigest: "e".repeat(64)
      }
    ],
    componentResolution: {
      status: "source-cell-map",
      reason: "test",
      map: {
        version: 2,
        projectSha256: "f".repeat(64),
        artifactBindings: [
          {
            artifactName: "VTK/*.vtk",
            scope: "cell-ranges",
            sourceCellCount: 8,
            identitySchema: "flowlab.openfoam-source-cell-identity.v1",
            identityField: "flowlabSourceCellId",
            identityContractSha256: "a".repeat(64),
            cellRanges: [{ edgeId: "edge-1", cellStart: 4, cellCount: 1 }]
          }
        ]
      }
    },
    limits: {
      defaultGridDimension: 64,
      maxGridDimension: 96,
      artifactSetBytes: 48 * 1024 * 1024,
      browserResidencyBytes: MAX_BROWSER_RESIDENCY_BYTES,
      derivedCacheBytesPerJob: 256 * 1024 * 1024,
      maxSeeds: 512,
      maxPathlineVertices: 250000,
      maxIsoTriangles: 500000,
      overflowBehavior: "reject"
    },
    grid: {
      dimensions: [2, 2, 2],
      voxelCount: 8,
      bounds: { min: [0, 0, 0], max: [2, 2, 2] },
      spacing: [1, 1, 1],
      sampleLocation: "voxel-center"
    },
    fields: [
      {
        name: "p",
        location: "point",
        kind: "scalar",
        unit: "Pa",
        values: blobs["values-000.bin"],
        validity: blobs["validity.bin"]
      }
    ],
    gradients: [],
    provenance: {
      validity: blobs["validity.bin"],
      sourceCellIds: blobs["source-cell-ids.bin"],
      subcellIds: blobs["subcell-ids.bin"],
      ambiguity: blobs["ambiguity.bin"],
      spatialWeights: blobs["spatial-weights.bin"],
      invalidSourceCellId: 0xffffffff,
      ambiguousSelections: "probe-only"
    },
    blobs: Object.values(blobs),
    browserResidencyBytes: Object.values(arrays).reduce((sum, value) => sum + value.byteLength, 0)
  };
  return { arrays, manifest };
}

describe("derived visualization binary decoding", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("decodes bounded typed blobs, creates 3D textures, and disposes all residency", async () => {
    const { arrays, manifest } = await volumeFixture();
    const fetchImpl = vi.fn(async (url: string | URL | Request) => {
      const name = String(url).split("/").at(-1)! as keyof typeof arrays;
      return new Response(arrays[name].buffer.slice(0), { status: 200 });
    }) as typeof fetch;
    const decoded = await decodeDerivedVisualization(manifest, (name) => `/blob/${name}`, fetchImpl);

    expect(decoded.blobs.get("values-000.bin")).toBeInstanceOf(Float32Array);
    expect(fetchImpl).toHaveBeenCalledTimes(manifest.blobs.length);
    const textures = createDerivedVolumeTextures(decoded);
    expect(textures.values.image.width).toBe(2);
    expect(textures.sourceCellIds.image.depth).toBe(2);
    decoded.dispose();
    expect(decoded.disposed).toBe(true);
    expect(decoded.blobs.size).toBe(0);
    expect(decoded.textures.size).toBe(0);
    expect(() => createDerivedVolumeTextures(decoded)).toThrow(/disposed/);
  });

  it("rejects malformed residency and blob hashes instead of partially decoding", async () => {
    const { arrays, manifest } = await volumeFixture();
    const tooLarge = { ...manifest, browserResidencyBytes: MAX_BROWSER_RESIDENCY_BYTES + 1 };
    await expect(
      decodeDerivedVisualization(tooLarge, (name) => `/blob/${name}`, vi.fn() as typeof fetch)
    ).rejects.toThrow(/residency budget/);

    const fetchImpl = vi.fn(async (url: string | URL | Request) => {
      const name = String(url).split("/").at(-1)! as keyof typeof arrays;
      const copy = arrays[name].buffer.slice(0);
      if (name === "values-000.bin") new Uint8Array(copy)[0] ^= 1;
      return new Response(copy, { status: 200 });
    }) as typeof fetch;
    await expect(decodeDerivedVisualization(manifest, (name) => `/blob/${name}`, fetchImpl)).rejects.toThrow(
      /hash mismatch/
    );
  });
});

describe("derived presentation provenance", () => {
  it("uses an explicit WebGL2 fallback instead of attempting unsupported 3D textures", async () => {
    const { arrays, manifest } = await volumeFixture();
    const decoded: DecodedDerivedVisualization = {
      manifest,
      blobs: new Map(Object.entries(arrays)),
      disposed: false,
      textures: new Set(),
      dispose: vi.fn()
    };
    const presentation = buildDerivedPresentation(
      { capabilities: { isWebGL2: false } } as never,
      decoded
    );
    expect(presentation.fallback).toBe("webgl2-required");
    expect(presentation.group.children).toHaveLength(0);
    expect(() => presentation.dispose()).not.toThrow();
  });

  it("extracts cut planes and resolves only unique explicit source-cell owners", async () => {
    const { arrays, manifest } = await volumeFixture();
    const decoded: DecodedDerivedVisualization = {
      manifest,
      blobs: new Map(Object.entries(arrays)),
      disposed: false,
      textures: new Set(),
      dispose: vi.fn()
    };
    const plane = extractDerivedCutPlane(decoded, 0, 1);
    expect(Array.from(plane.values)).toEqual([1, 1, 1, 1]);
    expect(Array.from(plane.sourceCellIds)).toEqual([4, 4, 4, 4]);
    expect(resolveDerivedSelection(decoded, 0)).toEqual({ state: "linked", sourceCellId: 4, edgeId: "edge-1" });

    (arrays["ambiguity.bin"] as Uint8Array)[0] = 1;
    expect(resolveDerivedSelection(decoded, 0)).toMatchObject({ state: "probe-only", sourceCellId: 4 });
  });

  it("extracts presentation-only iso triangles with contributor provenance and rejects overflow", async () => {
    const { arrays, manifest } = await volumeFixture();
    const decoded: DecodedDerivedVisualization = {
      manifest,
      blobs: new Map(Object.entries(arrays)),
      disposed: false,
      textures: new Set(),
      dispose: vi.fn()
    };
    const triangles = extractDerivedIsoSurface(decoded, 0.5);
    expect(triangles.length).toBeGreaterThan(0);
    expect(triangles.every((triangle) => triangle.sourceCellIds.join(",") === "4")).toBe(true);
    expect(triangles.every((triangle) => triangle.probeOnly === false)).toBe(true);
    expect(() => extractDerivedIsoSurface(decoded, 0.5, 0, 0, 1)).toThrow(/rejected instead of truncated/);
  });
});

describe("derived pathline animation", () => {
  it("interpolates deterministic pathline vertices in time", async () => {
    const positions = new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0]);
    const times = new Float32Array([0, 1, 2]);
    const offsets = new Uint32Array([0, 3]);
    const positionDescriptor = await descriptor("pathline-positions.bin", "float32", 3, 3, positions.buffer);
    const timeDescriptor = await descriptor("pathline-times.bin", "float32", 1, 3, times.buffer);
    const offsetDescriptor = await descriptor("pathline-offsets.bin", "uint32", 1, 2, offsets.buffer);
    const { arrays, manifest } = await volumeFixture();
    const pathlineManifest: DerivedVisualizationManifest = {
      ...manifest,
      operation: "pathlines",
      grid: undefined,
      fields: undefined,
      pathlines: {
        integration: "deterministic-rk4-time-linear-v1",
        seedCount: 1,
        vertexCount: 3,
        stepSeconds: 1,
        startTime: 0,
        endTime: 2,
        terminations: ["end-time"],
        positions: positionDescriptor,
        times: timeDescriptor,
        offsets: offsetDescriptor
      },
      blobs: [positionDescriptor, timeDescriptor, offsetDescriptor, ...manifest.blobs.filter((blob) =>
        ["source-cell-ids.bin", "subcell-ids.bin", "ambiguity.bin", "spatial-weights.bin"].includes(blob.name)
      )],
      browserResidencyBytes:
        positions.byteLength
        + times.byteLength
        + offsets.byteLength
        + arrays["source-cell-ids.bin"].byteLength
        + arrays["subcell-ids.bin"].byteLength
        + arrays["ambiguity.bin"].byteLength
        + arrays["spatial-weights.bin"].byteLength
    };
    const decoded: DecodedDerivedVisualization = {
      manifest: pathlineManifest,
      blobs: new Map<string, DerivedTypedArray>([
        ["pathline-positions.bin", positions],
        ["pathline-times.bin", times],
        ["pathline-offsets.bin", offsets],
        ["source-cell-ids.bin", arrays["source-cell-ids.bin"]],
        ["subcell-ids.bin", arrays["subcell-ids.bin"]],
        ["ambiguity.bin", arrays["ambiguity.bin"]],
        ["spatial-weights.bin", arrays["spatial-weights.bin"]]
      ]),
      disposed: false,
      textures: new Set(),
      dispose: vi.fn()
    };
    expect(pathlinePositionAt(decoded, 0, 0.5)).toEqual([0.5, 0, 0]);
    expect(pathlinePositionAt(decoded, 0, 3)).toEqual([2, 0, 0]);
  });
});
