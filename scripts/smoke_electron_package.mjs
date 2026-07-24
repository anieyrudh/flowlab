import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const packageIndex = args.indexOf("--package");
if (packageIndex === -1 || !args[packageIndex + 1]) {
  throw new Error("Usage: node scripts/smoke_electron_package.mjs --package <packaged app path>");
}
const packagePath = path.resolve(args[packageIndex + 1]);
const executable = packagePath.endsWith(".app")
  ? path.join(packagePath, "Contents", "MacOS", "FlowLab")
  : path.join(packagePath, "FlowLab.exe");
if (!fs.statSync(executable, { throwIfNoEntry: false })?.isFile()) {
  throw new Error(`Packaged Electron executable is missing: ${executable}`);
}

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "flowlab-electron-smoke-"));
const output = path.join(temporaryRoot, "smoke.json");
const child = spawn(executable, [], {
  env: {
    ...process.env,
    FLOWLAB_DESKTOP_SMOKE_OUTPUT: output
  },
  stdio: "inherit",
  windowsHide: true
});
try {
  const timeout = setTimeout(() => child.kill(), 60_000);
  const status = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  clearTimeout(timeout);

  if (status.code !== 0) {
    throw new Error(`Packaged Electron smoke exited with ${status.signal || status.code}.`);
  }
  if (!fs.statSync(output, { throwIfNoEntry: false })?.isFile()) {
    throw new Error("Packaged Electron smoke did not write its result.");
  }
  const result = JSON.parse(fs.readFileSync(output, "utf8"));
  if (
    result.schema !== "flowlab.electron_package_smoke.v1"
    || result.packaged !== true
    || result.backendHealthy !== true
    || result.uiReachable !== true
  ) {
    throw new Error(`Packaged Electron smoke failed: ${JSON.stringify(result)}`);
  }
  console.log(JSON.stringify(result, null, 2));
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
}
