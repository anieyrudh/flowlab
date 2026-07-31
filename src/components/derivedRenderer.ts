import * as THREE from "three";
import {
  createDerivedVolumeTextures,
  extractDerivedCutPlane,
  extractDerivedIsoSurface,
  pathlinePositionAt,
  type DecodedDerivedVisualization,
  type DerivedIsoTriangle,
  type DerivedVolumeTextures
} from "../results/derived";

export type DerivedPresentationOptions = {
  fieldIndex?: number;
  opacity?: number;
  cutPlane?: { axis: 0 | 1 | 2; index: number } | null;
  isoValue?: number | null;
  showVolume?: boolean;
  showParticles?: boolean;
};

export type DerivedPresentation = {
  group: THREE.Group;
  fallback: "none" | "webgl2-required";
  render: (timeSeconds: number) => void;
  dispose: () => void;
};

const volumeVertexShader = `
  varying vec3 vLocalPosition;
  void main() {
    vLocalPosition = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const volumeFragmentShader = `
  precision highp float;
  precision highp sampler3D;
  precision highp usampler3D;
  uniform sampler3D uValues;
  uniform highp usampler3D uValidity;
  uniform vec3 uCameraPositionLocal;
  uniform float uOpacity;
  uniform float uValueMin;
  uniform float uValueRange;
  varying vec3 vLocalPosition;
  out vec4 outColor;

  vec3 transfer(float value) {
    float t = clamp((value - uValueMin) / max(uValueRange, 1e-20), 0.0, 1.0);
    return clamp(vec3(
      1.5 - abs(4.0 * t - 3.0),
      1.5 - abs(4.0 * t - 2.0),
      1.5 - abs(4.0 * t - 1.0)
    ), 0.0, 1.0);
  }

  void main() {
    vec3 direction = normalize(vLocalPosition - uCameraPositionLocal);
    vec3 samplePoint = vLocalPosition + vec3(0.5);
    vec4 accumulated = vec4(0.0);
    for (int stepIndex = 0; stepIndex < 128; stepIndex += 1) {
      if (any(lessThan(samplePoint, vec3(0.0))) || any(greaterThan(samplePoint, vec3(1.0)))) break;
      uint valid = texture(uValidity, samplePoint).r;
      if (valid > uint(0)) {
        float value = texture(uValues, samplePoint).r;
        float alpha = uOpacity / 128.0;
        vec3 color = transfer(value);
        accumulated.rgb += (1.0 - accumulated.a) * color * alpha;
        accumulated.a += (1.0 - accumulated.a) * alpha;
        if (accumulated.a > 0.97) break;
      }
      samplePoint += direction / 128.0;
    }
    if (accumulated.a <= 0.002) discard;
    outColor = accumulated;
  }
`;

function fieldRange(values: Float32Array, validity: Uint8Array, components: number) {
  let minimum = Number.POSITIVE_INFINITY;
  let maximum = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < validity.length; index += 1) {
    if (!validity[index]) continue;
    const value = values[index * components];
    if (!Number.isFinite(value)) continue;
    minimum = Math.min(minimum, value);
    maximum = Math.max(maximum, value);
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return { minimum: 0, maximum: 1 };
  return { minimum, maximum };
}

function colorFor(value: number, minimum: number, maximum: number) {
  const fraction = Math.max(0, Math.min(1, (value - minimum) / Math.max(maximum - minimum, 1e-20)));
  return new THREE.Color().setHSL((1 - fraction) * 0.66, 0.9, 0.55);
}

function addCutPlane(
  group: THREE.Group,
  decoded: DecodedDerivedVisualization,
  axis: 0 | 1 | 2,
  index: number,
  fieldIndex: number,
  minimum: number,
  maximum: number
) {
  const grid = decoded.manifest.grid!;
  const plane = extractDerivedCutPlane(decoded, axis, index, fieldIndex);
  const freeAxes = ([0, 1, 2] as const).filter((candidate) => candidate !== axis);
  const positions: number[] = [];
  const colors: number[] = [];
  const [width, height] = plane.dimensions;
  const point = (column: number, row: number): [number, number, number] => {
    const coordinates = [0, 0, 0] as [number, number, number];
    coordinates[axis] = grid.bounds.min[axis] + (index + 0.5) * grid.spacing[axis];
    coordinates[freeAxes[0]] = grid.bounds.min[freeAxes[0]] + (column + 0.5) * grid.spacing[freeAxes[0]];
    coordinates[freeAxes[1]] = grid.bounds.min[freeAxes[1]] + (row + 0.5) * grid.spacing[freeAxes[1]];
    return coordinates;
  };
  for (let row = 0; row < height - 1; row += 1) {
    for (let column = 0; column < width - 1; column += 1) {
      const samples = [
        row * width + column,
        row * width + column + 1,
        (row + 1) * width + column + 1,
        (row + 1) * width + column
      ];
      if (samples.some((sample) => !plane.validity[sample])) continue;
      const corners = [
        point(column, row),
        point(column + 1, row),
        point(column + 1, row + 1),
        point(column, row + 1)
      ];
      [0, 1, 2, 0, 2, 3].forEach((cornerIndex) => {
        const sample = samples[cornerIndex];
        const color = colorFor(plane.values[sample], minimum, maximum);
        positions.push(...corners[cornerIndex]);
        colors.push(color.r, color.g, color.b);
      });
    }
  }
  if (positions.length === 0) return;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.92,
      side: THREE.DoubleSide,
      depthWrite: false
    })
  );
  mesh.name = "FlowLab derived internal cut plane";
  group.add(mesh);
}

function addIsoSurface(group: THREE.Group, triangles: DerivedIsoTriangle[]) {
  if (triangles.length === 0) return;
  const positions = triangles.flatMap((triangle) => triangle.vertices.flatMap((vertex) => vertex.position));
  const provenance = triangles.flatMap((triangle) =>
    triangle.vertices.flatMap(() => triangle.probeOnly ? [1] : [0])
  );
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("probeOnly", new THREE.Float32BufferAttribute(provenance, 1));
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshStandardMaterial({
      color: 0xffd95a,
      emissive: 0x3a2800,
      roughness: 0.28,
      metalness: 0.08,
      transparent: true,
      opacity: 0.8,
      side: THREE.DoubleSide
    })
  );
  mesh.name = "FlowLab presentation-only iso surface";
  mesh.userData.derivedIsoTriangles = triangles;
  group.add(mesh);
}

function addPathlines(
  group: THREE.Group,
  decoded: DecodedDerivedVisualization,
  showParticles: boolean
): { particles: THREE.Points | null; update: (time: number) => void } {
  const pathlines = decoded.manifest.pathlines;
  if (!pathlines) return { particles: null, update: () => {} };
  const positions = decoded.blobs.get(pathlines.positions.name);
  const offsets = decoded.blobs.get(pathlines.offsets.name);
  if (!(positions instanceof Float32Array) || !(offsets instanceof Uint32Array)) {
    throw new Error("Decoded pathline blobs are unavailable.");
  }
  const segmentPositions: number[] = [];
  for (let pathIndex = 0; pathIndex + 1 < offsets.length; pathIndex += 1) {
    for (let index = offsets[pathIndex]; index + 1 < offsets[pathIndex + 1]; index += 1) {
      segmentPositions.push(
        positions[index * 3],
        positions[index * 3 + 1],
        positions[index * 3 + 2],
        positions[(index + 1) * 3],
        positions[(index + 1) * 3 + 1],
        positions[(index + 1) * 3 + 2]
      );
    }
  }
  const lineGeometry = new THREE.BufferGeometry();
  lineGeometry.setAttribute("position", new THREE.Float32BufferAttribute(segmentPositions, 3));
  group.add(new THREE.LineSegments(lineGeometry, new THREE.LineBasicMaterial({ color: 0x57e8ff, transparent: true, opacity: 0.78 })));
  if (!showParticles) return { particles: null, update: () => {} };
  const particleArray = new Float32Array((offsets.length - 1) * 3);
  const particleGeometry = new THREE.BufferGeometry();
  particleGeometry.setAttribute("position", new THREE.BufferAttribute(particleArray, 3));
  const particles = new THREE.Points(
    particleGeometry,
    new THREE.PointsMaterial({ color: 0xffffff, size: 0.045, transparent: true, opacity: 0.9, depthWrite: false })
  );
  group.add(particles);
  return {
    particles,
    update(timeSeconds: number) {
      const duration = Math.max(pathlines.endTime - pathlines.startTime, 1e-9);
      const time = pathlines.startTime + ((timeSeconds - pathlines.startTime) % duration + duration) % duration;
      const attribute = particleGeometry.getAttribute("position") as THREE.BufferAttribute;
      for (let pathIndex = 0; pathIndex + 1 < offsets.length; pathIndex += 1) {
        const position = pathlinePositionAt(decoded, pathIndex, time);
        if (position) attribute.setXYZ(pathIndex, position[0], position[1], position[2]);
      }
      attribute.needsUpdate = true;
    }
  };
}

export function buildDerivedPresentation(
  renderer: Pick<THREE.WebGLRenderer, "capabilities">,
  decoded: DecodedDerivedVisualization,
  options: DerivedPresentationOptions = {}
): DerivedPresentation {
  const group = new THREE.Group();
  group.name = "FlowLab provenance-preserving derived visualization";
  if (!renderer.capabilities.isWebGL2 && decoded.manifest.operation === "volume") {
    return {
      group,
      fallback: "webgl2-required",
      render: () => {},
      dispose: () => {
        group.clear();
      }
    };
  }
  const fieldIndex = options.fieldIndex ?? 0;
  let textures: DerivedVolumeTextures | null = null;
  let volumeMaterial: THREE.ShaderMaterial | null = null;
  let pathlineUpdate = (_time: number) => {};
  if (decoded.manifest.operation === "volume") {
    const grid = decoded.manifest.grid;
    const field = decoded.manifest.fields?.[fieldIndex];
    const validityDescriptor = decoded.manifest.provenance.validity;
    if (!grid || !field || !validityDescriptor) throw new Error("Derived volume manifest is incomplete.");
    const values = decoded.blobs.get(field.values.name);
    const validity = decoded.blobs.get(validityDescriptor.name);
    if (!(values instanceof Float32Array) || !(validity instanceof Uint8Array)) throw new Error("Derived volume values are unavailable.");
    const components = field.kind === "vector" ? 3 : 1;
    const range = fieldRange(values, validity, components);
    textures = createDerivedVolumeTextures(decoded, fieldIndex);
    if (options.showVolume !== false) {
      const uniforms = {
        uValues: { value: textures.values },
        uValidity: { value: textures.validity },
        uCameraPositionLocal: { value: new THREE.Vector3(0, 0, 2) },
        uOpacity: { value: Math.max(0.01, Math.min(1, options.opacity ?? 0.32)) },
        uValueMin: { value: range.minimum },
        uValueRange: { value: Math.max(range.maximum - range.minimum, 1e-20) }
      };
      volumeMaterial = new THREE.ShaderMaterial({
        uniforms,
        vertexShader: volumeVertexShader,
        fragmentShader: volumeFragmentShader,
        transparent: true,
        depthWrite: false,
        side: THREE.FrontSide,
        glslVersion: THREE.GLSL3
      });
      const volume = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), volumeMaterial);
      const span = grid.bounds.max.map((value, axis) => value - grid.bounds.min[axis]) as [number, number, number];
      const center = grid.bounds.max.map((value, axis) => (value + grid.bounds.min[axis]) / 2) as [number, number, number];
      volume.scale.set(...span);
      volume.position.set(...center);
      volume.onBeforeRender = (_renderer, _scene, camera) => {
        const inverse = volume.matrixWorld.clone().invert();
        uniforms.uCameraPositionLocal.value.copy(camera.position).applyMatrix4(inverse);
      };
      volume.name = "FlowLab translucent derived volume";
      group.add(volume);
    }
    if (options.cutPlane) {
      addCutPlane(group, decoded, options.cutPlane.axis, options.cutPlane.index, fieldIndex, range.minimum, range.maximum);
    }
    if (options.isoValue !== null && options.isoValue !== undefined) {
      addIsoSurface(group, extractDerivedIsoSurface(decoded, options.isoValue, fieldIndex));
    }
  } else {
    const pathlinePresentation = addPathlines(group, decoded, options.showParticles !== false);
    pathlineUpdate = pathlinePresentation.update;
  }
  return {
    group,
    fallback: "none",
    render(timeSeconds: number) {
      pathlineUpdate(timeSeconds);
    },
    dispose() {
      group.traverse((object) => {
        const mesh = object as THREE.Mesh;
        mesh.geometry?.dispose();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((entry) => entry.dispose());
        else material?.dispose();
      });
      textures?.dispose();
      volumeMaterial?.dispose();
      group.clear();
    }
  };
}
