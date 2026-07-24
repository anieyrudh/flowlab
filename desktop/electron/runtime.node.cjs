"use strict"; // Executed by node:test, outside Vitest's test-file glob.

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");
const {
  MAX_EXPORT_FILE_BYTES,
  backendExecutablePath,
  backendSpawnSpec,
  isAllowedRendererUrl,
  normalizeExportFiles,
  requireSupportedPlatform,
  sanitizeExportFilename
} = require("./runtime.cjs");

test("resolves packaged backend executables per supported platform", () => {
  assert.equal(
    backendExecutablePath("/resources", "darwin"),
    path.join("/resources", "backend", "FlowLabBackend")
  );
  assert.equal(
    backendExecutablePath("C:\\resources", "win32"),
    path.join("C:\\resources", "backend", "FlowLabBackend.exe")
  );
  assert.throws(() => backendExecutablePath("/resources", "linux"), /support only macOS and Windows/);
});

test("uses bundled backends only for packaged applications", () => {
  assert.deepEqual(
    backendSpawnSpec({
      appRoot: "/app",
      resourcesPath: "/resources",
      packaged: true,
      platform: "darwin"
    }),
    { command: path.join("/resources", "backend", "FlowLabBackend"), args: [] }
  );
  const development = backendSpawnSpec({
    appRoot: "/app",
    resourcesPath: "/resources",
    packaged: false,
    platform: "win32",
    buildPython: "python.exe"
  });
  assert.equal(development.command, "python.exe");
  assert.equal(development.args[0], path.join("/app", "desktop", "macos", "backend_main.py"));
});

test("accepts only the exact local backend renderer origin", () => {
  assert.equal(isAllowedRendererUrl("http://127.0.0.1:8787/", 8787), true);
  assert.equal(isAllowedRendererUrl("http://localhost:8787/results", 8787), true);
  assert.equal(isAllowedRendererUrl("http://127.0.0.1:8788/", 8787), false);
  assert.equal(isAllowedRendererUrl("https://127.0.0.1:8787/", 8787), false);
  assert.equal(isAllowedRendererUrl("https://example.com/", 8787), false);
});

test("normalizes bounded text exports and rejects unsafe paths", () => {
  assert.deepEqual(
    normalizeExportFiles([{ filename: "result.json", text: "{}", type: "application/json" }]),
    [{ filename: "result.json", text: "{}", type: "application/json", bytes: 2 }]
  );
  assert.equal(sanitizeExportFilename(" result.csv "), "result.csv");
  for (const filename of ["", ".", "..", "../result.json", "folder/result.json", "folder\\result.json"]) {
    assert.throws(() => sanitizeExportFilename(filename));
  }
  assert.throws(
    () => normalizeExportFiles([{ filename: "large.txt", text: "x".repeat(MAX_EXPORT_FILE_BYTES + 1), type: "text/plain" }]),
    /per-file size limit/
  );
  assert.throws(
    () => normalizeExportFiles([
      { filename: "duplicate.txt", text: "one", type: "text/plain" },
      { filename: "duplicate.txt", text: "two", type: "text/plain" }
    ]),
    /Duplicate/
  );
  for (const filename of ["CON", "nul.txt", "bad:name.json", "trailing."]) {
    assert.throws(() => sanitizeExportFilename(filename, "win32"));
  }
  assert.throws(
    () => normalizeExportFiles([
      { filename: "Result.json", text: "one", type: "application/json" },
      { filename: "result.json", text: "two", type: "application/json" }
    ], "win32"),
    /Duplicate/
  );
});

test("fails closed on unsupported desktop platforms", () => {
  assert.equal(requireSupportedPlatform("darwin"), "darwin");
  assert.equal(requireSupportedPlatform("win32"), "win32");
  assert.throws(() => requireSupportedPlatform("linux"), /support only macOS and Windows/);
});
