import { spawnSync } from "node:child_process";

const command = process.env.FLOWLAB_BUILD_PYTHON
  || (process.platform === "win32" ? "python" : "python3");
const result = spawnSync(command, ["scripts/build_electron_backend.py", ...process.argv.slice(2)], {
  cwd: process.cwd(),
  env: process.env,
  stdio: "inherit"
});

if (result.error) {
  throw result.error;
}
process.exit(result.status ?? 1);
