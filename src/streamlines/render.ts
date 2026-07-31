import * as THREE from "three";
import type { ResultColorMap } from "../types";
import { STREAMLINE_LIMITS, type StreamlineDisplayOptions, type StreamlineLine, type StreamlineResult } from "./types";

type ResultBounds = {
  center: [number, number, number];
  span: number;
};

const palettes: Record<ResultColorMap, string[]> = {
  turbo: ["#2b4cff", "#00c2ff", "#67f3a5", "#ffe15c", "#ff6a3a", "#c5164f"],
  viridis: ["#440154", "#3b528b", "#21918c", "#5ec962", "#fde725"],
  thermal: ["#18206f", "#1954d2", "#1eb6ff", "#f7e733", "#ff8c42", "#d62839"],
  grayscale: ["#17212b", "#4b5d70", "#8da1b5", "#d6e2ec", "#ffffff"]
};

export type PassiveSprite = {
  line: StreamlineLine;
  phase: number;
};

export function streamlineFieldExtent(result: StreamlineResult, field: StreamlineDisplayOptions["colorField"]) {
  const values = result.lines.flatMap((line) => line.vertices.map((vertex) => vertex.fields[field])).filter(Number.isFinite);
  return {
    min: values.length > 0 ? Math.min(...values) : 0,
    max: values.length > 0 ? Math.max(...values) : 1
  };
}

function colorForValue(value: number, min: number, max: number, colorMap: ResultColorMap) {
  const range = Math.max(max - min, 1e-12);
  const normalized = Math.max(0, Math.min(0.999999, (value - min) / range));
  const palette = palettes[colorMap];
  return new THREE.Color(palette[Math.floor(normalized * palette.length)]);
}

function scenePoint(
  point: [number, number, number],
  bounds: ResultBounds,
  meshScale: number
): [number, number, number] {
  return [
    (point[0] - bounds.center[0]) * meshScale,
    (point[1] - bounds.center[1]) * meshScale,
    (point[2] - bounds.center[2]) * meshScale + 0.17
  ];
}

export function passiveSpriteLayout(result: StreamlineResult): PassiveSprite[] {
  const eligible = result.lines.filter((line) => line.vertices.length >= 2);
  if (eligible.length === 0) return [];
  const count = Math.min(STREAMLINE_LIMITS.maxSprites, Math.max(eligible.length, result.seedCount));
  return Array.from({ length: count }, (_value, index) => ({
    line: eligible[index % eligible.length],
    phase: index / count
  }));
}

export function passiveSpritePositions(
  layout: PassiveSprite[],
  elapsedSeconds: number,
  reducedMotion: boolean
): Float32Array {
  const positions = new Float32Array(layout.length * 3);
  layout.forEach(({ line, phase }, spriteIndex) => {
    const progress = reducedMotion ? phase : (phase + elapsedSeconds * 0.08) % 1;
    const scaled = progress * (line.vertices.length - 1);
    const lower = Math.floor(scaled);
    const upper = Math.min(line.vertices.length - 1, lower + 1);
    const fraction = scaled - lower;
    const first = line.vertices[lower].position;
    const second = line.vertices[upper].position;
    for (let axis = 0; axis < 3; axis += 1) {
      positions[spriteIndex * 3 + axis] = first[axis] + (second[axis] - first[axis]) * fraction;
    }
  });
  return positions;
}

export function addStreamlineScene(
  scene: THREE.Scene,
  result: StreamlineResult,
  bounds: ResultBounds,
  meshScale: number,
  display: StreamlineDisplayOptions
) {
  const extent = streamlineFieldExtent(result, display.colorField);
  if (display.showLines) {
    result.lines.forEach((line) => {
      if (line.vertices.length < 2) return;
      const positions: number[] = [];
      const colors: number[] = [];
      line.vertices.forEach((vertex) => {
        positions.push(...scenePoint(vertex.position, bounds, meshScale));
        const color = colorForValue(vertex.fields[display.colorField] ?? 0, extent.min, extent.max, display.colorMap);
        colors.push(color.r, color.g, color.b);
      });
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
      scene.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.96 })));
    });
  }

  const layout = display.showSprites ? passiveSpriteLayout(result) : [];
  if (layout.length === 0) return { update: (_time: number, _advance: boolean) => undefined };
  const colors: number[] = [];
  layout.forEach(({ line, phase }) => {
    const vertex = line.vertices[Math.min(line.vertices.length - 1, Math.floor(phase * line.vertices.length))];
    const color = colorForValue(vertex.fields[display.colorField] ?? 0, extent.min, extent.max, display.colorMap);
    colors.push(color.r, color.g, color.b);
  });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(layout.length * 3), 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const sprites = new THREE.Points(
    geometry,
    new THREE.PointsMaterial({
      size: 0.075,
      sizeAttenuation: true,
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      blending: THREE.AdditiveBlending
    })
  );
  scene.add(sprites);
  let frozenTime = 0;

  return {
    update(time: number, advance: boolean) {
      if (advance && !display.reducedMotion) frozenTime = time;
      const physical = passiveSpritePositions(layout, frozenTime / 1000, display.reducedMotion);
      const position = geometry.getAttribute("position") as THREE.BufferAttribute;
      for (let index = 0; index < layout.length; index += 1) {
        const transformed = scenePoint(
          [physical[index * 3], physical[index * 3 + 1], physical[index * 3 + 2]],
          bounds,
          meshScale
        );
        position.setXYZ(index, transformed[0], transformed[1], transformed[2]);
      }
      position.needsUpdate = true;
    }
  };
}
