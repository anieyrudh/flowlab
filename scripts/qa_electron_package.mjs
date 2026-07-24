import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

function fail(message) {
  throw new Error(`Electron package QA failed: ${message}`);
}

function requireFile(candidate, label) {
  if (!fs.statSync(candidate, { throwIfNoEntry: false })?.isFile()) {
    fail(`${label} is missing at ${candidate}`);
  }
}

function requireDirectory(candidate, label) {
  if (!fs.statSync(candidate, { throwIfNoEntry: false })?.isDirectory()) {
    fail(`${label} is missing at ${candidate}`);
  }
}

function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  if (result.status !== 0) {
    fail(`${command} ${args.join(" ")} failed: ${(result.stderr || result.stdout || "").trim()}`);
  }
  return `${result.stdout || ""}\n${result.stderr || ""}`.trim();
}

const args = process.argv.slice(2);
const packageIndex = args.indexOf("--package");
const modeIndex = args.indexOf("--mode");
if (packageIndex === -1 || !args[packageIndex + 1]) {
  fail("use --package <packaged app path>");
}
const packagePath = path.resolve(args[packageIndex + 1]);
const mode = modeIndex === -1 ? "internal" : args[modeIndex + 1];
if (mode !== "internal" && mode !== "external") fail("mode must be internal or external");

let platform;
let resources;
let executable;
if (packagePath.endsWith(".app")) {
  platform = "darwin";
  resources = path.join(packagePath, "Contents", "Resources");
  executable = path.join(packagePath, "Contents", "MacOS", "FlowLab");
} else {
  platform = "win32";
  resources = path.join(packagePath, "resources");
  executable = path.join(packagePath, "FlowLab.exe");
}

requireDirectory(packagePath, "packaged application");
requireDirectory(resources, "resources directory");
requireFile(executable, "desktop executable");
requireFile(path.join(resources, "app.asar"), "Electron application archive");
requireFile(path.join(resources, "dist", "index.html"), "production renderer");
requireFile(path.join(resources, "release-contract.json"), "release contract");
const backendExecutable = path.join(
  resources,
  "backend",
  platform === "win32" ? "FlowLabBackend.exe" : "FlowLabBackend"
);
requireFile(backendExecutable, "bundled backend");
const backendManifestPath = path.join(
  resources,
  "backend",
  "flowlab-electron-backend-build-manifest.json"
);
requireFile(backendManifestPath, "backend build manifest");
const releaseAuthorizationPath = path.join(
  resources,
  "backend",
  "flowlab-electron-release-authorization.json"
);
requireFile(releaseAuthorizationPath, "release authorization receipt");

const contract = JSON.parse(fs.readFileSync(path.join(resources, "release-contract.json"), "utf8"));
if (contract.schema !== "flowlab.electron_release_contract.v1") fail("release contract schema mismatch");
if (contract.distributionShell?.contextIsolationRequired !== true) fail("context isolation is not required");
if (contract.distributionShell?.nodeIntegrationAllowed !== false) fail("renderer Node integration is not prohibited");
if (contract.authorization?.externalPublicationAuthorized !== false) {
  fail("package candidate must not self-authorize external publication");
}

const backendManifest = JSON.parse(fs.readFileSync(backendManifestPath, "utf8"));
const releaseAuthorization = JSON.parse(fs.readFileSync(releaseAuthorizationPath, "utf8"));
if (backendManifest.schema !== "flowlab.electron_backend_build_manifest.v1") {
  fail("backend build manifest schema mismatch");
}
const expectedPlatform = platform === "darwin" ? "darwin" : "win32";
if (backendManifest.target?.platform !== expectedPlatform) {
  fail(`backend target ${backendManifest.target?.platform} does not match package ${expectedPlatform}`);
}
const backendArchitecture = String(backendManifest.target?.architecture || "").toLowerCase();
const expectedArchitectures = platform === "darwin"
  ? new Set(["arm64"])
  : new Set(["amd64", "x86_64"]);
if (!expectedArchitectures.has(backendArchitecture)) {
  fail(`backend architecture ${backendManifest.target?.architecture} does not match the bounded package target`);
}
if (mode === "external" && backendManifest.sourceTreeClean !== true) {
  fail("external package backend was not produced from a clean tracked source tree");
}
if (mode === "external") {
  if (releaseAuthorization.externalPublicationAuthorized !== true) {
    fail("external publication authorization is absent");
  }
  if (
    releaseAuthorization.controlledReview?.accepted !== true
    || releaseAuthorization.controlledReview?.acceptedPackageTreeDigest
      !== "11358ec9fdc70c8f198255195f8321f12dbdab55d61db7dc659d6eeed28664c6"
  ) {
    fail("controlled O-grid review acceptance is absent or bound to the wrong package digest");
  }
  if (releaseAuthorization.scientificPromotionAuthorized !== false) {
    fail("desktop distribution must not self-authorize scientific promotion");
  }
}

if (platform === "darwin") {
  run("codesign", ["--verify", "--deep", "--strict", packagePath]);
  if (mode === "external") {
    const details = run("codesign", ["-dv", "--verbose=4", packagePath]);
    if (!details.includes("Developer ID Application:")) {
      fail("Developer ID Application signature is absent");
    }
    if (!/flags=.*runtime/m.test(details)) {
      fail("Developer ID package is missing the hardened-runtime signature flag");
    }
    run("spctl", ["--assess", "--type", "execute", "--verbose=4", packagePath]);
    run("xcrun", ["stapler", "validate", packagePath]);
  }
} else if (mode === "external") {
  const escaped = executable.replaceAll("'", "''");
  const status = run("powershell", [
    "-NoProfile",
    "-Command",
    `(Get-AuthenticodeSignature -LiteralPath '${escaped}').Status`
  ]);
  if (status !== "Valid") fail(`Windows Authenticode status is ${status || "missing"}`);
}

console.log(JSON.stringify({
  schema: "flowlab.electron_package_qa.v1",
  status: "passed",
  mode,
  platform,
  packagePath,
  backendTarget: backendManifest.target,
  sourceTreeClean: backendManifest.sourceTreeClean,
  externalPublicationAuthorized: releaseAuthorization.externalPublicationAuthorized,
  scientificPromotionAuthorized: releaseAuthorization.scientificPromotionAuthorized
}, null, 2));
