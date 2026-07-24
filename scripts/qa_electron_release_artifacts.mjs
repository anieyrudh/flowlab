import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

function fail(message) {
  throw new Error(`Electron release artifact QA failed: ${message}`);
}

function run(command, args) {
  const result = spawnSync(command, args, { encoding: "utf8" });
  if (result.status !== 0) {
    fail(`${command} ${args.join(" ")} failed: ${(result.stderr || result.stdout || "").trim()}`);
  }
  return `${result.stdout || ""}\n${result.stderr || ""}`.trim();
}

function filesUnder(root) {
  const files = [];
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const candidate = path.join(root, entry.name);
    if (entry.isDirectory()) files.push(...filesUnder(candidate));
    else if (entry.isFile()) files.push(candidate);
  }
  return files;
}

const args = process.argv.slice(2);
function argument(name, fallback) {
  const index = args.indexOf(name);
  return index === -1 ? fallback : args[index + 1];
}

const directory = path.resolve(argument("--directory", "out/make"));
const platform = argument("--platform", process.platform);
const architecture = argument("--arch", process.arch);
const mode = argument("--mode", "internal");
if (!["darwin", "win32"].includes(platform)) fail(`unsupported platform ${platform}`);
if (!["internal", "external"].includes(mode)) fail(`unsupported mode ${mode}`);
if (!fs.statSync(directory, { throwIfNoEntry: false })?.isDirectory()) {
  fail(`artifact directory is missing: ${directory}`);
}

const files = filesUnder(directory);
const relative = (candidate) => path.relative(directory, candidate).replaceAll(path.sep, "/");
const findSuffix = (suffix) => files.filter((candidate) => candidate.toLowerCase().endsWith(suffix));

let primaryInstallers;
if (platform === "darwin") {
  const dmgs = findSuffix(".dmg");
  const zips = findSuffix(".zip");
  if (dmgs.length !== 1) fail(`expected one DMG, found ${dmgs.length}`);
  if (zips.length !== 1) fail(`expected one macOS ZIP, found ${zips.length}`);
  primaryInstallers = [...dmgs, ...zips];
  if (mode === "external") {
    run("xcrun", ["stapler", "validate", dmgs[0]]);
    run("spctl", [
      "--assess",
      "--type",
      "open",
      "--context",
      "context:primary-signature",
      "--verbose=4",
      dmgs[0]
    ]);
  }
} else {
  const setupExecutables = files.filter((candidate) => /Setup\.exe$/i.test(candidate));
  const zips = findSuffix(".zip");
  const packages = findSuffix(".nupkg");
  if (setupExecutables.length !== 1) {
    fail(`expected one Windows setup executable, found ${setupExecutables.length}`);
  }
  if (zips.length !== 1) fail(`expected one Windows ZIP, found ${zips.length}`);
  if (packages.length < 1) fail("expected at least one Squirrel package");
  primaryInstallers = [...setupExecutables, ...zips, ...packages];
  if (mode === "external") {
    const escaped = setupExecutables[0].replaceAll("'", "''");
    const signature = run("powershell", [
      "-NoProfile",
      "-Command",
      `$signature = Get-AuthenticodeSignature -LiteralPath '${escaped}'; `
        + `if ($signature.Status -ne 'Valid' -or $null -eq $signature.TimeStamperCertificate) { `
        + `throw ('Invalid or untimestamped Authenticode signature: ' + $signature.Status) }`
    ]);
    if (signature) fail(`unexpected Authenticode output: ${signature}`);
  }
}

for (const candidate of primaryInstallers) {
  if (fs.statSync(candidate).size === 0) fail(`artifact is empty: ${relative(candidate)}`);
}

console.log(JSON.stringify({
  schema: "flowlab.electron_release_artifact_qa.v1",
  status: "passed",
  mode,
  platform,
  architecture,
  directory,
  artifacts: primaryInstallers.map(relative)
}, null, 2));
