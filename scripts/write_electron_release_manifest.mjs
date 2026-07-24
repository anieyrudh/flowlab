import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

function sha256(candidate) {
  const hash = crypto.createHash("sha256");
  hash.update(fs.readFileSync(candidate));
  return hash.digest("hex");
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

const [inputArgument, outputArgument] = process.argv.slice(2);
if (!inputArgument || !outputArgument) {
  throw new Error("Usage: node scripts/write_electron_release_manifest.mjs <artifact-directory> <output.json>");
}
const input = path.resolve(inputArgument);
const output = path.resolve(outputArgument);
if (!fs.statSync(input, { throwIfNoEntry: false })?.isDirectory()) {
  throw new Error(`Artifact directory does not exist: ${input}`);
}
const outputParent = path.dirname(output);
fs.mkdirSync(outputParent, { recursive: true });
const artifactFiles = filesUnder(input)
  .filter((candidate) => candidate !== output)
  .sort((left, right) => left.localeCompare(right));
if (artifactFiles.length === 0) throw new Error("No Electron release artifacts were found.");

const manifest = {
  schema: "flowlab.electron_release_artifacts.v1",
  sourceCommit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  sourceTreeClean: execFileSync(
    "git",
    ["status", "--porcelain", "--untracked-files=no"],
    { encoding: "utf8" }
  ).trim() === "",
  platform: process.platform,
  architecture: process.arch,
  artifacts: artifactFiles.map((candidate) => ({
    path: path.relative(input, candidate).replaceAll(path.sep, "/"),
    bytes: fs.statSync(candidate).size,
    sha256: sha256(candidate)
  }))
};
fs.writeFileSync(output, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
console.log(`Wrote ${output} with ${manifest.artifacts.length} artifact(s).`);
