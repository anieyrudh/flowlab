"use strict";

const path = require("node:path");

const MAX_EXPORT_FILES = 24;
const MAX_EXPORT_FILE_BYTES = 16 * 1024 * 1024;
const MAX_EXPORT_TOTAL_BYTES = 32 * 1024 * 1024;
const ALLOWED_EXPORT_TYPES = new Set([
  "application/json",
  "application/octet-stream",
  "application/vnd.vtk",
  "application/xml",
  "text/csv",
  "text/plain",
  "text/xml"
]);

function requireSupportedPlatform(platform) {
  if (platform !== "darwin" && platform !== "win32") {
    throw new Error(`FlowLab Electron packages support only macOS and Windows; received ${platform}.`);
  }
  return platform;
}

function backendExecutablePath(resourcesPath, platform = process.platform) {
  requireSupportedPlatform(platform);
  const executable = platform === "win32" ? "FlowLabBackend.exe" : "FlowLabBackend";
  return path.join(resourcesPath, "backend", executable);
}

function sanitizeExportFilename(value, platform = process.platform) {
  requireSupportedPlatform(platform);
  if (typeof value !== "string") {
    throw new TypeError("Export filename must be a string.");
  }
  const filename = value.trim();
  const windowsStem = filename.split(".")[0]?.toUpperCase();
  const windowsReserved = (
    windowsStem === "CON"
    || windowsStem === "PRN"
    || windowsStem === "AUX"
    || windowsStem === "NUL"
    || /^COM[1-9]$/.test(windowsStem)
    || /^LPT[1-9]$/.test(windowsStem)
  );
  if (
    filename.length === 0
    || filename.length > 180
    || filename === "."
    || filename === ".."
    || filename !== path.basename(filename)
    || filename.includes("/")
    || filename.includes("\\")
    || filename.includes("\0")
    || (
      platform === "win32"
      && (
        /[<>:"|?*]/.test(filename)
        || /[. ]$/.test(filename)
        || windowsReserved
      )
    )
  ) {
    throw new Error("Export filename must be a safe base filename.");
  }
  return filename;
}

function normalizeExportFiles(value, platform = process.platform) {
  requireSupportedPlatform(platform);
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_EXPORT_FILES) {
    throw new Error(`Exports must contain between 1 and ${MAX_EXPORT_FILES} files.`);
  }

  let totalBytes = 0;
  const seen = new Set();
  const files = value.map((candidate) => {
    if (candidate === null || typeof candidate !== "object" || Array.isArray(candidate)) {
      throw new TypeError("Each export must be an object.");
    }
    const filename = sanitizeExportFilename(candidate.filename, platform);
    const key = platform === "win32" ? filename.toLowerCase() : filename;
    if (seen.has(key)) {
      throw new Error(`Duplicate export filename: ${filename}`);
    }
    seen.add(key);

    if (typeof candidate.text !== "string") {
      throw new TypeError(`Export ${filename} must contain text.`);
    }
    const bytes = Buffer.byteLength(candidate.text, "utf8");
    if (bytes > MAX_EXPORT_FILE_BYTES) {
      throw new Error(`Export ${filename} exceeds the per-file size limit.`);
    }
    totalBytes += bytes;
    if (totalBytes > MAX_EXPORT_TOTAL_BYTES) {
      throw new Error("Export payload exceeds the total size limit.");
    }

    const type = typeof candidate.type === "string" && ALLOWED_EXPORT_TYPES.has(candidate.type)
      ? candidate.type
      : "text/plain";
    return { filename, text: candidate.text, type, bytes };
  });

  return files;
}

function isAllowedRendererUrl(value, port) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "http:"
      && (url.hostname === "127.0.0.1" || url.hostname === "localhost")
      && url.port === String(port)
    );
  } catch {
    return false;
  }
}

function backendSpawnSpec({
  appRoot,
  resourcesPath,
  packaged,
  platform = process.platform,
  buildPython
}) {
  requireSupportedPlatform(platform);
  if (packaged) {
    return {
      command: backendExecutablePath(resourcesPath, platform),
      args: []
    };
  }
  return {
    command: buildPython || process.env.FLOWLAB_BUILD_PYTHON || (platform === "win32" ? "python" : "python3"),
    args: [path.join(appRoot, "desktop", "macos", "backend_main.py")]
  };
}

module.exports = {
  ALLOWED_EXPORT_TYPES,
  MAX_EXPORT_FILES,
  MAX_EXPORT_FILE_BYTES,
  MAX_EXPORT_TOTAL_BYTES,
  backendExecutablePath,
  backendSpawnSpec,
  isAllowedRendererUrl,
  normalizeExportFiles,
  requireSupportedPlatform,
  sanitizeExportFilename
};
